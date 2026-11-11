"""Always-on AI ops watcher ("operations guys", Alex Nov 8/10).

Runs every 15 minutes on the 15-minute bot's server (systemd timer kalshi-watch).

  1. Plain code gathers health facts about BOTH bots (15-min status on m15-data, the server's
     own service + log, the Actions scan/trade runs, trader_state, bankroll) and applies fixed
     rules (stale status, service down, overfills, mode mismatch, scan gaps, failures...).
  2. Claude Haiku reads a short digest every run and says ok / watch / escalate (~$1-2/month).
  3. On escalate (or a critical rule), Claude Opus diagnoses it and writes a plain message for
     Alex (ntfy push) plus a proposed fix. Capped per day.
  4. It may PAUSE the 15-minute bot (m15_control.json -> "paper") only on a clear malfunction
     (a rule flagged as malfunction AND Opus agrees, or Opus unreachable). Never on mere losses:
     the bot's own stops handle those.

It never changes caps, stops, sizes or trading code, and never un-pauses. The scheduled Claude
check-ins read m15/watch.json and turn proposed fixes into PRs.

Without ANTHROPIC_API_KEY it does nothing (exits quietly).
"""
from __future__ import annotations

import base64
import csv
import io
import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import notify
from .util import Config, Http, HttpError, read_json, write_json

log = logging.getLogger("wxbot")

ET = ZoneInfo("America/New_York")
REPO = os.environ.get("GITHUB_REPO", "alexanderhabel22-commits/kalshi-weather-bot")
API = f"https://api.github.com/repos/{REPO}"
STATE = Path(os.environ.get("M15_DATA_DIR") or "data/m15") / "watch_state.json"

DEFAULTS = {
    "haiku_model": "claude-haiku-5-5",
    "opus_model": "claude-opus-5-5",
    "max_haiku_per_day": 120,
    "max_opus_per_day": 6,
    "realert_hours": 6,
    "status_stale_min": 35,        # m15 uploads every 15 min
    "copycat_stale_min": 20,
    "lip_stale_min": 40,           # rewards bot uploads lip-status.json every 15 min
    "lip_budget_slack": 1.10,      # resting + filled above budget * this = malfunction -> pull bids       # Copycat writes status every 5 min (it crash-looped unseen for 9 h, Nov 11)
    "scan_gap_min": 25,            # cron-job.org dispatches a scan every 5 min
    "bankroll_stale_min": 40,
    "fail_runs_1h": 2,
    "max_filled_per_market": 2,    # contracts + extra_contracts (Nov 8 rule)
    "exposure_slack": 1.15,        # live_exposure above pool_pct * bankroll * this = malfunction
    "auto_pause": True,
    "mem_warn_pct": 85,            # server RAM used; Alex's droplet jumped 35% -> 60% on Nov 10
    "svc_mem_warn_pct": 85,        # kalshi-m15 vs its MemoryMax (systemd kills it at 100%)
    "journal_keep": 30,            # Foreman's memory: recent events fed back into each check
    "notes_keep": 8,               # ... and standing notes it chose to remember
}

PERSONA = """You are the Foreman: the night-and-day crew boss Alex put over his trading bots.
Voice: steady, plain-spoken, a little dry, like a good site foreman. Short sentences. At most
one light touch of personality per message; never joke about losses, a pause, or a problem
Alex must fix. You never touch the money settings; you watch, report, and stop the line only
when a machine is clearly broken."""

HAIKU_SYSTEM = PERSONA + """
You watch the small automated Kalshi trading bots for their owner, Alex.
- MAIN BOT: GitHub Actions. A weather scan is dispatched every 5 minutes, then a trade run.
  Strategies: Mentions NO, big-event favorites, Fed/CPI fade, weather day-before. Halts itself
  at its stops (-10%/day pause, -25% total halt).
- 15-MIN BOT: always-on server, bids the 71-95c favourite in 15-minute BTC and silver markets,
  1 contract (2 at 80-87c). Paper markets also run for data. Its own stops: rolling 24h -35%,
  total -50%. A losing window costs ~$0.75-1.60; a winning one makes ~$0.05-0.45. Losing days
  happen and are NOT a malfunction while within the stops.
- REWARDS BOT (kalshi-lip, real money test Nov 11-15): rests small 1-8c bids in ~8 quiet Kalshi
  liquidity-reward markets to earn Kalshi's daily reward pool (paid after the period ends, so
  $0 credits before then is NORMAL). Budget ~$20 resting + filled, own $10 fill stop, news guard
  (pulls a bid for 6 h when the price jumps 3c). A fill is not a malfunction; it is the risk we
  accepted, and the bot alerts Alex itself. Its halting at the fill stop is the stop WORKING.
  Danger signs: bot down/stale while bids still rest (nobody watching them), bids above budget,
  bids still up after it was switched off, repeated rejects or errors.
- PAPER LAB (kalshi-paper): read-only paper tests (market making, parlay quotes). Low stakes.
You get a digest with health facts and rule flags. Decide:
- ok: healthy (losses inside the stops are fine).
- watch: something odd but not urgent; note it.
- escalate: a likely malfunction or something Alex or an engineer must look at now (bot down or
  stuck, orders bigger than allowed, mode not matching its control, scans stopped, repeated
  failures/rejects, money moving in ways the rules don't explain).
Be calm and conservative: do not escalate on ordinary P&L swings. One or two short sentences.
Use YOUR MEMORY (your notes and recent log) to spot repeats: something that keeps coming back
deserves a mention; something already known and explained does not need re-escalating.
"remember": usually empty. Fill it (one line) only with a NEW durable fact that will help future
checks, e.g. a new recurring pattern or a known harmless quirk. Leave it empty if your notes
already cover it. Never restate the digest."""

OPUS_SYSTEM = PERSONA + """
Right now you are the on-call engineer for the small automated Kalshi trading bots owned by
Alex (not a coder; reads on his iPhone; wants plain words and Eastern times). A cheaper watcher
escalated the digest below. Rails you must respect: you may recommend pausing the 15-minute bot
(it switches to paper, logging continues) or pulling the rewards bot's bids (pause_lip: its
switch goes off and it cancels its resting bids; filled contracts are held) ONLY for a clear
malfunction (wrong order sizes, mode
not matching its control, stuck/hung bot that still holds orders, exposure above its pool,
runaway errors). Never pause for ordinary losses: the bots' own stops handle those. You cannot
change caps, stops, sizes or code; propose fixes for an engineer instead.
Return: severity ok (false alarm, no push), alert (push Alex), or pause_m15; a short title; a
message for Alex (max 60 words, plain English, ET times, say what he should do, or that nothing
is needed from him); your diagnosis; a concrete proposed fix (files/functions if you can tell);
needs_alex true only if he personally must act (deposit, key, server console, approval).
Check YOUR MEMORY first: say so if this is a repeat. "notebook": your full standing notes after
this incident (max 8 short lines; keep what still matters, drop what's stale)."""

HAIKU_SCHEMA = {
    "type": "object",
    "properties": {
        "level": {"type": "string", "enum": ["ok", "watch", "escalate"]},
        "summary": {"type": "string"},
        "remember": {"type": "string"},
    },
    "required": ["level", "summary", "remember"],
    "additionalProperties": False,
}
OPUS_SCHEMA = {
    "type": "object",
    "properties": {
        "severity": {"type": "string", "enum": ["ok", "alert", "pause_m15", "pause_lip"]},
        "title": {"type": "string"},
        "message": {"type": "string"},
        "diagnosis": {"type": "string"},
        "proposed_fix": {"type": "string"},
        "needs_alex": {"type": "boolean"},
        "notebook": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["severity", "title", "message", "diagnosis", "proposed_fix", "needs_alex", "notebook"],
    "additionalProperties": False,
}


def settings(cfg: Config) -> dict:
    s = dict(DEFAULTS)
    s.update(cfg.raw.get("watch", {}) if hasattr(cfg, "raw") else {})
    return s


def et(ts: float) -> str:
    d = datetime.fromtimestamp(ts, ET)
    return f"{(d.hour % 12) or 12}:{d:%M} {'AM' if d.hour < 12 else 'PM'} ET"


def _ts(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ---------------------------------------------------------------- gathering

class GitHub:
    def __init__(self, http: Http, token: str = ""):
        self.http = http
        self.hdr = {"Accept": "application/vnd.github+json"}
        if token:
            self.hdr["Authorization"] = f"Bearer {token}"

    def text(self, path: str, ref: str = "main") -> str | None:
        try:
            js = self.http.get_json(f"{API}/contents/{path}", params={"ref": ref}, headers=self.hdr)
        except HttpError as exc:
            if exc.status == 404:
                return None
            raise
        return base64.b64decode(js.get("content", "")).decode()

    def json(self, path: str, ref: str = "main"):
        t = self.text(path, ref)
        return json.loads(t) if t else None

    def runs(self) -> list:
        js = self.http.get_json(f"{API}/actions/runs", params={"per_page": 40}, headers=self.hdr)
        return js.get("workflow_runs", [])

    def put(self, path: str, text: str, message: str, branch: str = "main") -> None:
        url = f"{API}/contents/{path}"
        sha = None
        try:
            sha = self.http.get_json(url, params={"ref": branch}, headers=self.hdr).get("sha")
        except HttpError as exc:
            if exc.status != 404:
                raise
        body = {"message": message, "branch": branch,
                "content": base64.b64encode(text.encode()).decode()}
        if sha:
            body["sha"] = sha
        self.http._request("PUT", url, json=body, headers=self.hdr)


def _sh(cmd: list, runner=subprocess.run) -> str:
    try:
        return runner(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception as exc:          # not on the server (tests, laptop)
        return f"unavailable: {exc}"


def mem_used(path: str = "/proc/meminfo") -> float | None:
    """Percent of server RAM in use (MemAvailable counts reclaimable cache as free)."""
    try:
        kv = {}
        for line in Path(path).read_text().splitlines():
            k, v = line.split(":", 1)
            kv[k] = float(v.split()[0])
        return round(100 * (1 - kv["MemAvailable"] / kv["MemTotal"]), 1)
    except Exception:
        return None


def svc_mem_pct(text: str | None) -> float | None:
    """kalshi-m15's memory as a percent of its MemoryMax (from systemctl show)."""
    try:
        kv = dict(line.split("=", 1) for line in (text or "").splitlines() if "=" in line)
        cur, cap = float(kv["MemoryCurrent"]), float(kv["MemoryMax"])
        return round(100 * cur / cap, 1) if cap > 0 else None
    except (KeyError, ValueError):
        return None


COPYCAT_STATUS = Path(os.environ.get("COPYCAT_DIR") or "/var/lib/kalshi-copycat") / "status.json"


def gather(gh: GitHub, now: float, runner=subprocess.run, meminfo: str = "/proc/meminfo",
           cc_status: Path | None = None) -> dict:
    f: dict = {"now": now, "errors": []}

    def grab(key, fn):
        try:
            f[key] = fn()
        except Exception as exc:
            f[key] = None
            f["errors"].append(f"{key}: {exc}")

    grab("status", lambda: gh.json("m15/status.json", "m15-data"))
    day = datetime.fromtimestamp(now, ET).date().isoformat()
    grab("markets", lambda: list(csv.DictReader(io.StringIO(
        gh.text(f"m15/markets-{day}.csv", "m15-data") or ""))))
    grab("control", lambda: gh.json("m15_control.json"))
    grab("trader", lambda: gh.json("data/trader_state.json"))
    grab("bankroll", lambda: gh.json("data/bankroll.json"))
    grab("runs", gh.runs)
    f["service"] = _sh(["systemctl", "is-active", "kalshi-m15"], runner)
    f["mem"] = mem_used(meminfo)
    f["svc_mem"] = _sh(["systemctl", "show", "-p", "MemoryCurrent", "-p", "MemoryMax", "kalshi-m15"], runner)
    f["journal"] = _sh(["journalctl", "-u", "kalshi-m15", "--since", "-20 min", "-p", "warning",
                        "--no-pager", "-o", "cat", "-n", "40"], runner)
    f["cc_service"] = _sh(["systemctl", "is-active", "kalshi-copycat"], runner)
    grab("lip", lambda: gh.json("m15/lip-status.json", "m15-data"))
    grab("lip_control", lambda: gh.json("lip_control.json"))
    f["lip_service"] = _sh(["systemctl", "is-active", "kalshi-lip"], runner)
    f["paper_service"] = _sh(["systemctl", "is-active", "kalshi-paper"], runner)
    try:                              # same server: the file's age shows whether Copycat is working
        f["cc_age_min"] = round((now - (cc_status or COPYCAT_STATUS).stat().st_mtime) / 60, 1)
    except OSError:
        f["cc_age_min"] = None
    return f


# ---------------------------------------------------------------- rules

def overfills(f: dict, s: dict) -> dict:
    """Live rows today with more contracts than allowed: {ticker: filled}."""
    out = {}
    for r in f.get("markets") or []:
        try:
            filled = float(r.get("filled") or 0)
        except ValueError:
            continue
        if r.get("mode") == "live" and filled > s["max_filled_per_market"]:
            out[r.get("ticker")] = filled
    return out


def rules(f: dict, s: dict, m15cfg: dict | None = None, over_seen: dict | None = None) -> list[dict]:
    """Fixed checks. Each flag: code, sev (info|warn|critical), malfunction (may justify a pause)."""
    out = []
    now = f["now"]

    def flag(code, sev, text, malfunction=False):
        out.append({"code": code, "sev": sev, "text": text, "malfunction": malfunction})

    st = f.get("status") or {}
    ctl = ((f.get("control") or {}).get("mode") or "").lower()
    cc_svc = str(f.get("cc_service") or "")
    if cc_svc and cc_svc not in ("active", "inactive") and not cc_svc.startswith("unavailable"):
        flag("COPYCAT_DOWN", "critical", f"kalshi-copycat service is '{cc_svc}'")
    cc_age = f.get("cc_age_min")
    if cc_svc == "active" and cc_age is not None and cc_age > s["copycat_stale_min"]:
        flag("COPYCAT_STALE", "critical", f"Copycat status not updated for {int(cc_age)} min "
             "(running but stuck or restarting; it writes every 5 min)")
    svc = str(f.get("service") or "")
    if svc and svc != "active" and not svc.startswith("unavailable"):
        flag("M15_SERVICE_DOWN", "critical", f"kalshi-m15 service is '{f['service']}'")
    at = _ts(st.get("at"))
    if at is None:
        flag("M15_STATUS_MISSING", "critical", "no 15-min status.json")
    elif now - at > s["status_stale_min"] * 60:
        flag("M15_STATUS_STALE", "critical", f"15-min status last updated {int((now - at) / 60)} min ago")
    if st.get("errors"):
        flag("M15_ERRORS", "warn", f"15-min bot error count {st['errors']}")
    if st.get("halted"):
        flag("M15_HALTED", "info", f"15-min bot halted (total-loss stop): {st['halted']}")
    if st.get("roll_stop") or st.get("day_stop"):
        flag("M15_STOPPED", "info", f"15-min loss stop active: {st.get('roll_stop') or st.get('day_stop')}")
    if st.get("fund_error"):
        flag("M15_FUND", "warn", f"15-min funding error: {st['fund_error']}")
    rej = st.get("last_reject") or {}
    rej_at = rej.get("at") if isinstance(rej, dict) else None
    if (rej and "post only cross" not in str(rej)
            and (not isinstance(rej_at, (int, float)) or now - rej_at < 3600)):
        flag("M15_REJECT", "warn", f"15-min order reject: {str(rej)[:160]}")
    seen = (st.get("control") or "").lower()
    if seen in ("paper", "off") and st.get("mode") == "live":
        flag("M15_MODE_MISMATCH", "critical",
             f"the bot read control={seen} but still reports live", malfunction=True)
    elif ctl and seen and ctl != seen:
        flag("M15_CONTROL_LAG", "info", f"control file says {ctl}, bot last read {seen} (normal for ~20 min after a change)")
    # Flag each overfilled row once, when it first shows up or grows. Rows can be re-booked
    # long after their window closes (Nov 10: a restart's reconciliation took BTC 5:30 PM ET
    # from 2 to 4 contracts 85 min after close), so this is keyed on what was already seen.
    for tk, filled in overfills(f, s).items():
        if over_seen is not None and over_seen.get(tk, 0) >= filled:
            continue
        flag("M15_OVERFILL", "critical",
             f"{tk} filled {filled:g} contracts (max {s['max_filled_per_market']})", malfunction=True)
    br = (f.get("bankroll") or {}).get("total")
    pool = (m15cfg or {}).get("pool_pct", 0.25)
    exp = st.get("live_exposure") or 0
    if br and exp > pool * br * s["exposure_slack"]:
        flag("M15_EXPOSURE", "critical",
             f"15-min live exposure ${exp:.2f} above its pool (${pool * br:.2f})", malfunction=True)
    mem = f.get("mem")
    if mem is not None and mem >= s["mem_warn_pct"]:
        flag("MEM_HIGH", "critical", f"server RAM {mem:.0f}% used (warn at {s['mem_warn_pct']}%)")
    smem = svc_mem_pct(f.get("svc_mem"))
    if smem is not None and smem >= s["svc_mem_warn_pct"]:
        flag("M15_MEM_HIGH", "critical",
             f"15-min bot using {smem:.0f}% of its memory cap (systemd kills it at 100%)")
    if st.get("spot_ok") is False:
        flag("M15_SPOT", "warn", "Coinbase spot feed down (jump guard blind)")

    # main bot
    runs = f.get("runs") or []
    scans = [r for r in runs if r.get("name") == "weather-scan"]
    if runs:
        last = _ts(scans[0]["created_at"]) if scans else None
        if last is None or now - last > s["scan_gap_min"] * 60:
            mins = "never" if last is None else f"{int((now - last) / 60)} min ago"
            flag("SCAN_GAP", "critical", f"last weather scan {mins} (cron-job.org timer?)")
        bad = [r for r in runs if r.get("name") in ("weather-scan", "trade")
               and r.get("conclusion") in ("failure", "timed_out")
               and (_ts(r.get("created_at")) or 0) > now - 3600]
        if len(bad) >= s["fail_runs_1h"]:
            flag("ACTIONS_FAIL", "warn", f"{len(bad)} failed runs in the last hour: "
                 + ", ".join(sorted({r.get('name', '?') for r in bad})))
    tr = f.get("trader") or {}
    if tr.get("halted"):
        flag("MAIN_HALTED", "info", f"main bot halted: {str(tr['halted'])[:120]}")
    bat = _ts((f.get("bankroll") or {}).get("at"))
    if bat and now - bat > s["bankroll_stale_min"] * 60:
        flag("BANKROLL_STALE", "warn", f"balance last read {int((now - bat) / 60)} min ago")
    j = f.get("journal") or ""
    if "Traceback" in j or j.count("ERROR") >= 3:
        flag("M15_LOG_ERRORS", "warn", "errors in the 15-min bot log (last 20 min)")
    out += lip_rules(f, s)
    for e in f.get("errors") or []:
        flag("WATCH_FETCH", "warn", f"watcher could not read {e[:120]}")
    return out


def lip_rules(f: dict, s: dict) -> list[dict]:
    """Checks on the liquidity-reward bot. Malfunction flags carry target "lip" (pull its bids)."""
    out = []
    now = f["now"]

    def flag(code, sev, text, malfunction=False):
        out.append({"code": code, "sev": sev, "text": text, "malfunction": malfunction, "target": "lip"})

    ctl = f.get("lip_control") or {}
    st = f.get("lip") or {}
    until = _ts(ctl.get("until"))
    on = (ctl.get("mode") or "").lower() == "live" and (until is None or now < until)
    orders = st.get("orders") or {}
    resting = float(st.get("resting_cost") or 0)
    filled = float(st.get("filled_cost") or 0)
    svc = str(f.get("lip_service") or "")
    if on and svc and svc != "active" and not svc.startswith("unavailable"):
        flag("LIP_SERVICE_DOWN", "critical", f"rewards bot service kalshi-lip is '{svc}' while the test is on"
             + (f" and {len(orders)} bids may still be resting unwatched" if orders else ""))
    at = _ts(st.get("at"))
    if on and at is None and ctl:
        flag("LIP_STATUS_MISSING", "warn", "no rewards-bot status file")
    elif at is not None and (on or orders) and now - at > s["lip_stale_min"] * 60:
        flag("LIP_STATUS_STALE", "critical", f"rewards bot status last updated {int((now - at) / 60)} min ago"
             + (f" with {len(orders)} bids resting (news guard not running)" if orders else ""))
    fresh = at is not None and now - at <= s["lip_stale_min"] * 60
    budget = float(st.get("budget") or ctl.get("budget") or 0)
    if fresh and budget and resting + filled > budget * s["lip_budget_slack"]:
        flag("LIP_OVER_BUDGET", "critical", f"rewards bot has ${resting:.2f} resting + ${filled:.2f} filled, "
             f"above its ${budget:.0f} budget", malfunction=True)
    if fresh and orders and not on and (st.get("control") == "off" or (until and now - until > 1800)):
        flag("LIP_OFF_BUT_BIDDING", "critical", f"rewards bot is switched off but still shows {len(orders)} "
             "resting bids", malfunction=True)
    if st.get("halted"):
        flag("LIP_HALTED", "info", f"rewards bot stopped itself: {str(st['halted'])[:120]}")
    stop = float(ctl.get("fill_stop") or 10)
    if filled >= stop / 2 and not st.get("halted"):
        flag("LIP_FILLS", "warn", f"rewards bot bids filled ${filled:.2f} (stop at ${stop:.0f})")
    recent = [e for e in st.get("events") or [] if isinstance(e.get("t"), (int, float)) and now - e["t"] < 3600]
    rej = [e for e in recent if e.get("kind") == "reject"]
    if len(rej) >= 3:
        flag("LIP_REJECTS", "warn", f"rewards bot: {len(rej)} order rejects in the last hour, last: "
             f"{str(rej[-1].get('error'))[:120]}")
    errs = [e for e in recent if e.get("kind") == "error"]
    if len(errs) >= 3:
        flag("LIP_ERRORS", "critical" if len(errs) >= 8 else "warn",
             f"rewards bot: {len(errs)} errors in the last hour, last: {str(errs[-1].get('error'))[:120]}")
    psvc = str(f.get("paper_service") or "")
    if psvc and psvc not in ("active",) and not psvc.startswith("unavailable") and psvc != "inactive":
        flag("PAPER_DOWN", "warn", f"paper lab service kalshi-paper is '{psvc}'")
    return out


# ---------------------------------------------------------------- digest

def digest(f: dict, flags: list[dict]) -> str:
    now = f["now"]
    st = f.get("status") or {}
    pnl = st.get("pnl") or {}
    lines = [f"Time: {et(now)} {datetime.fromtimestamp(now, ET):%a %b %d}"]
    lines.append(f"15-MIN: service={f.get('service')} mode={st.get('mode')} control="
                 f"{(f.get('control') or {}).get('mode')} status_at={st.get('at')} "
                 f"errors={st.get('errors')} halted={st.get('halted')} stop="
                 f"{st.get('roll_stop') or st.get('day_stop')} exposure=${st.get('live_exposure')} "
                 f"pnl_24h=${st.get('pnl_24h')} live_total=${pnl.get('live_total')}")
    days = pnl.get("live_days") or {}
    if days:
        lines.append("15-MIN live P&L by day: " + ", ".join(f"{k} ${v:.2f}" for k, v in list(days.items())[-4:]))
    for name, ser in (st.get("series") or {}).items():
        lines.append(f"  {name}: fills={ser.get('fills')} pnl=${ser.get('pnl')}"
                     + (f" decay_alert={ser.get('decay_alerted')}" if ser.get("decay_alerted") else ""))
    live = [r for r in (f.get("markets") or []) if r.get("mode") == "live" and r.get("filled") not in ("", "0", "0.0", None)]
    if live:
        lines.append("Last live fills today (ticker filled cost pnl):")
        for r in live[-8:]:
            lines.append(f"  {r.get('ticker')} {r.get('filled')} {r.get('cost')} {r.get('pnl')}")
    lines.append(f"SERVER: RAM {f.get('mem')}% used, 15-min bot at {svc_mem_pct(f.get('svc_mem'))}% of its memory cap")
    lines.append(f"COPYCAT (separate paper/copy service): {f.get('cc_service')}, status file updated "
                 f"{f.get('cc_age_min')} min ago (normal: under 6)")
    ls, lc = f.get("lip") or {}, f.get("lip_control") or {}
    lev = [e for e in (ls.get("events") or []) if e.get("kind") in ("fill", "reject", "error", "cancel", "credit")]
    lines.append(f"REWARDS BOT (real $, liquidity rewards): service={f.get('lip_service')} switch={lc.get('mode')} "
                 f"until={lc.get('until')} status_at={ls.get('at')} mode={ls.get('mode')} halted={ls.get('halted')} "
                 f"bids={len(ls.get('orders') or {})} resting=${ls.get('resting_cost')} filled=${ls.get('filled_cost')} "
                 f"budget=${ls.get('budget')} fill_stop=${lc.get('fill_stop')} credits_seen={len(ls.get('credits') or [])}")
    if lev:
        lines.append("  recent rewards-bot events: " + "; ".join(
            f"{e.get('kind')} {e.get('ticker', '')} {str(e.get('why') or e.get('error') or e.get('px') or '')[:60]}"
            for e in lev[-5:]))
    lines.append(f"PAPER LAB: service={f.get('paper_service')}")
    tr = f.get("trader") or {}
    br = f.get("bankroll") or {}
    lines.append(f"MAIN: mode={tr.get('mode')} halted={bool(tr.get('halted'))} bankroll=${br.get('total')} "
                 f"cash=${br.get('cash')} at={br.get('at')}")
    runs = f.get("runs") or []
    if runs:
        lines.append("Recent Actions runs: " + "; ".join(
            f"{r.get('name')} {r.get('conclusion') or r.get('status')} {r.get('created_at', '')[11:16]}Z"
            for r in runs[:8]))
    j = (f.get("journal") or "").strip()
    if j:
        lines.append("15-min log warnings (last 20 min):\n" + "\n".join(j.splitlines()[-12:]))
    lines.append("RULE FLAGS: " + ("none" if not flags else "; ".join(
        f"[{x['sev']}{', malfunction' if x['malfunction'] else ''}] {x['code']}: {x['text']}"
        + (f" ({x['runs']} checks in a row)" if x.get("runs", 1) > 1 else "") for x in flags)))
    return "\n".join(lines)


# ---------------------------------------------------------------- Claude

def _ask(client, model: str, system: str, text: str, schema: dict, effort: str, max_tokens: int) -> tuple[dict, dict]:
    r = client.messages.create(
        model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": text}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )
    usage = {"in": getattr(r.usage, "input_tokens", 0), "out": getattr(r.usage, "output_tokens", 0)}
    if r.stop_reason == "refusal":
        raise RuntimeError("model refused")
    body = next(b.text for b in r.content if b.type == "text")
    return json.loads(body), usage


def make_client():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=key, max_retries=3, timeout=120)


# ---------------------------------------------------------------- actions

def pause_m15(gh: GitHub, reason: str, now: float) -> bool:
    try:
        cur = gh.json("m15_control.json") or {}
        if (cur.get("mode") or "").lower() in ("paper", "off"):
            return True
        cur["mode"] = "paper"
        cur["paused_by"] = f"watcher {datetime.fromtimestamp(now, timezone.utc):%Y-%m-%dT%H:%MZ}: {reason[:200]}"
        gh.put("m15_control.json", json.dumps(cur, indent=1) + "\n",
               "watch: pause 15-min bot to paper (malfunction) [skip ci]")
        return True
    except Exception as exc:
        log.error("pause failed: %s", exc)
        return False


def pause_lip(gh: GitHub, reason: str, now: float) -> bool:
    """Switch the rewards bot off: within ~5 min it cancels every resting bid (fills are held)."""
    try:
        cur = gh.json("lip_control.json") or {}
        if (cur.get("mode") or "").lower() == "off":
            return True
        cur["mode"] = "off"
        cur["paused_by"] = f"watcher {datetime.fromtimestamp(now, timezone.utc):%Y-%m-%dT%H:%MZ}: {reason[:200]}"
        gh.put("lip_control.json", json.dumps(cur, indent=1) + "\n",
               "watch: switch rewards bot off (malfunction) [skip ci]")
        return True
    except Exception as exc:
        log.error("lip pause failed: %s", exc)
        return False


def journal(state: dict, stamp: str, what: str, keep: int) -> None:
    """The Foreman's running log of notable events (not every quiet check)."""
    j = state.setdefault("journal", [])
    j.append(f"{stamp}: {what[:240]}")
    state["journal"] = j[-keep:]


def _similarity(a: str, b: str) -> float:
    wa, wb = (set(w.strip(".,;:()'\"").lower() for w in x.split()) - {""} for x in (a, b))
    return len(wa & wb) / max(1, len(wa | wb))


def remember(state: dict, note: str | None, keep: int) -> None:
    note = (note or "").strip()
    if not note:
        return
    # a reworded version of a note it already has replaces it (Nov 11: Haiku re-noted the same
    # BTC decay alert every check and filled all 8 slots with one fact)
    notes = [n for n in state.get("notes", []) if _similarity(n, note) < 0.5]
    state["notes"] = (notes + [note[:200]])[-keep:]


def memory_block(state: dict) -> str:
    notes = state.get("notes") or []
    j = (state.get("journal") or [])[-10:]
    return ("YOUR MEMORY (the Foreman's notebook):\nNotes: " + ("; ".join(notes) if notes else "none yet")
            + "\nRecent log:\n" + ("\n".join(f"  {e}" for e in j) if j else "  nothing notable yet"))


def run(cfg: Config, http: Http | None = None, client=None, gh: GitHub | None = None,
        now: float | None = None, runner=subprocess.run, state_path: Path = STATE) -> dict:
    s = settings(cfg)
    client = client if client is not None else make_client()
    if client is None:
        print("watch: no ANTHROPIC_API_KEY, nothing to do")
        return {"skipped": "no key"}
    http = http or Http(cfg.user_agent)
    gh = gh or GitHub(http, os.environ.get("GITHUB_TOKEN", "").strip())
    now = now or time.time()
    state = read_json(state_path, {}) or {}
    day = datetime.fromtimestamp(now, ET).date().isoformat()
    if state.get("day") != day:
        state.update({"day": day, "haiku": 0, "opus": 0, "tokens": {"haiku_in": 0, "haiku_out": 0,
                                                                     "opus_in": 0, "opus_out": 0}})
    f = gather(gh, now, runner)
    over = overfills(f, s)
    if "over_seen" not in state:          # first run with this check: today's old rows are history
        state["over_seen"] = dict(over)
    flags = rules(f, s, cfg.raw.get("m15", {}) if hasattr(cfg, "raw") else {}, over_seen=state["over_seen"])
    state["over_seen"] = {**({} if state.get("over_day") != day else state["over_seen"]), **over}
    state["over_day"] = day
    streak = {x["code"]: (state.get("streak") or {}).get(x["code"], 0) + 1 for x in flags}
    for x in flags:
        x["runs"] = streak[x["code"]]
    codes, prev_codes = set(streak), set(state.get("streak") or {})
    state["streak"] = streak
    stamp = f"{datetime.fromtimestamp(now, ET):%b %d} {et(now)}"
    jot = lambda what: journal(state, stamp, what, s["journal_keep"])  # noqa: E731
    if codes - prev_codes:
        jot("new: " + "; ".join(x["text"] for x in flags if x["code"] in codes - prev_codes))
    if prev_codes - codes:
        jot("cleared: " + ", ".join(sorted(prev_codes - codes)))
    text = digest(f, flags) + "\n\n" + memory_block(state)
    rec = {"at": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
           "flags": flags, "haiku": None, "opus": None, "action": None}

    level = "ok"
    if state["haiku"] < s["max_haiku_per_day"]:
        try:
            h, u = _ask(client, s["haiku_model"], HAIKU_SYSTEM, text, HAIKU_SCHEMA, "low", 2000)
            state["haiku"] += 1
            state["tokens"]["haiku_in"] += u["in"]
            state["tokens"]["haiku_out"] += u["out"]
            rec["haiku"] = h
            level = h.get("level", "ok")
            if level != state.get("last_level", "ok") and level != "watch":
                jot(f"check: {level}. {h.get('summary', '')}")
            state["last_level"] = level
            remember(state, h.get("remember"), s["notes_keep"])
        except Exception as exc:
            rec["haiku"] = {"error": str(exc)[:200]}
            level = "escalate" if any(x["sev"] == "critical" for x in flags) else "watch"
    critical = [x for x in flags if x["sev"] == "critical"]
    malfunction = [x for x in flags if x["malfunction"]]
    key = ",".join(sorted({x["code"] for x in critical})) or (level if level == "escalate" else "")

    if (level == "escalate" or critical) and key:
        last = (state.get("alerted") or {}).get(key, 0)
        fresh = now - last > s["realert_hours"] * 3600
        o = None
        if fresh and state["opus"] < s["max_opus_per_day"]:
            try:
                o, u = _ask(client, s["opus_model"], OPUS_SYSTEM,
                            text + f"\n\nWatcher's note: {(rec['haiku'] or {}).get('summary', '')}",
                            OPUS_SCHEMA, "medium", 8000)
                state["opus"] += 1
                state["tokens"]["opus_in"] += u["in"]
                state["tokens"]["opus_out"] += u["out"]
                rec["opus"] = o
                jot(f"escalated: {o.get('title')} ({o.get('severity')}). {o.get('diagnosis', '')}")
                if o.get("notebook"):
                    state["notes"] = [str(n)[:200] for n in o["notebook"]][-s["notes_keep"]:]
            except Exception as exc:
                rec["opus"] = {"error": str(exc)[:200]}
        if fresh:
            sev = (o or {}).get("severity", "alert" if critical else "ok")
            paused = False
            m15_bad = [x for x in malfunction if x.get("target", "m15") == "m15"]
            lip_bad = [x for x in malfunction if x.get("target") == "lip"]
            what = "15-min bot"
            if m15_bad and s["auto_pause"] and (o is None or sev == "pause_m15"):
                paused = pause_m15(gh, "; ".join(x["text"] for x in m15_bad), now)
                rec["action"] = "paused_m15" if paused else "pause_failed"
                jot(f"ACTION {rec['action']}: " + "; ".join(x["text"] for x in m15_bad))
            elif lip_bad and s["auto_pause"] and (o is None or sev in ("pause_lip", "pause_m15")):
                what = "rewards bot"
                paused = pause_lip(gh, "; ".join(x["text"] for x in lip_bad), now)
                rec["action"] = "paused_lip" if paused else "pause_failed"
                jot(f"ACTION {rec['action']}: " + "; ".join(x["text"] for x in lip_bad))
            if sev != "ok" or paused:
                title = "👷 Foreman: " + ((o or {}).get("title") or ", ".join(x["code"] for x in critical) or "check")
                body = (o or {}).get("message") or "\n".join(x["text"] for x in critical)
                if paused and what == "rewards bot":
                    body = "SWITCHED OFF the rewards bot (its bids get cancelled within ~5 min). " + body
                elif paused:
                    body = "PAUSED the 15-min bot to paper (logging continues). " + body
                elif rec["action"] == "pause_failed":
                    body = f"Tried to pause the {what} and FAILED. " + body
                notify.send(http, cfg, title, body + f"\n({et(now)})", priority=5 if paused else 4,
                            tags=["construction_worker"])
                jot(f"pushed Alex: {title[11:]}")
            state.setdefault("alerted", {})[key] = now
    state["last"] = rec
    log_ = state.setdefault("log", [])
    if rec["opus"] or rec["action"] or (rec["haiku"] or {}).get("level") in ("watch", "escalate"):
        log_.append(rec)
        state["log"] = log_[-40:]
    write_json(state_path, state)
    try:
        out = {"last": rec, "today": {k: state.get(k) for k in ("day", "haiku", "opus", "tokens")},
               "notes": state.get("notes", []), "journal": state.get("journal", []),
               "log": state["log"], "digest": text}
        gh.put("m15/watch.json", json.dumps(out, indent=1, default=str),
               "m15: watch.json [skip ci]", branch="m15-data")
    except Exception as exc:
        log.warning("watch upload failed: %s", exc)
    print(f"watch {rec['at']}: level={level} flags={[x['code'] for x in flags]} action={rec['action']}")
    return rec


def main(cfg: Config) -> int:
    run(cfg)
    return 0
