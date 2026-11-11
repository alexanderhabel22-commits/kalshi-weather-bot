"""Copycat, shadow mode (Nov 10, 2026; leaderboard study idea 2): follow proven winners' public trades.

SHADOW ONLY: nothing here places, changes or cancels an order, and it never reads the Kalshi
trading key. It answers one question: when a proven Kalshi winner whose trades are public makes a
trade, does the price keep moving their way afterwards, and do we see the trade soon enough to
follow it?

How it works (one thread, polite polling of Kalshi's public social API):
  - every 12 h, build the watch list: profitable accounts (lifetime pnl >= MIN_PNL) from the
    all-time and category profit boards whose trade history is public; every 10 min, also scout the
    global public trade feed (every trader with a public profile) and add any new profitable name;
  - each account is scored on a rolling window of its last 50 settled copies: it passes while the
    pessimistic estimate is >= 0 (min 20 settled), fails when it isn't, and can pass again later;
  - passing accounts are polled 3x as often;
  - poll each watched account's public trades round-robin (about every 30-60 s per account);
  - each new fill is logged with the sharp's side (yes/no), price, size, maker/taker, the trade's
    own timestamp and when we saw it (detection lag);
  - the market is snapshotted (best bid/ask, last) when we see the fill and at +1, +5, +30 min.
    Settlement results are joined later by the grading script, from the public market data;
  - hourly files copycat-YYYY-MM-DD-HH.jsonl are uploaded to branch copycat-data (gzipped)
    once the hour ends (needs GITHUB_TOKEN; without it files stay on disk).

Research only: sports stay excluded from real money whatever this shows.
Run: python bot.py copycat   (systemd unit deploy/m15/kalshi-copycat.service on the server)
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import statistics as st
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import requests

from wxbot.srclog import UA, github_put

log = logging.getLogger("wxbot")
ET = ZoneInfo("America/New_York")
SOCIAL = "https://api.elections.kalshi.com/v1/social"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
BRANCH = "copycat-data"
MIN_PNL = 10_000            # dollars of lifetime profit to be watched
MAX_WATCH = 100
SCOUT_EVERY_S = 600         # scan the global public feed for new profitable names
SCOUT_PAGES = 10            # 100 trades per page
SCOUT_CHECKS = 40           # new names looked up per scan
ROLL_WINDOW = 50            # an account's verdict uses its last 50 settled copies
MIN_SETTLED = 20
SNAP_DELAYS = (0, 60, 300, 1800)
SNAP_GAP_S = 300            # at most one snapshot series per (account, ticker) per 5 min
MAX_PENDING = 4000          # cap on queued snapshots
REQ_GAP_S = 0.4             # spacing between API calls (~150/min; a 3/s test had 0 errors)
WATCH_REFRESH_S = 12 * 3600
STATUS_EVERY_S = 300
GO_LIVE_FILLS = 100         # an account is judged at 100 copied fills or 7 days, whichever first
GO_LIVE_DAYS = 7
CATEGORIES = ("Sports", "Politics", "Crypto", "Economics", "Climate and Weather", "Financials",
              "Entertainment", "Mentions", "Combos", "Commodities", "Elections", "Companies")
SEED = ("BWmarkets", "thebigdon", "BitcoinTradingChallenge", "szg.szg", "brief.theme",
        "CSPTRADINGonX", "tempChecker73", "troubled.owl0488", "lost.buck", "ripjohnpork",
        "balla23", "crow.hcis", "reluctant.fox5394", "spoon.rope", "pentasir", "far.bike")


def _px(v) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return round(v / 100, 4) if v > 1.5 else v


def sharp_side(t: dict, nick: str) -> tuple[str | None, str | None]:
    """(role, side the sharp ended up long: 'yes'/'no') for one public trade row."""
    ts = t.get("taker_side")
    act = t.get("taker_action", "buy")
    if ts not in ("yes", "no"):
        return None, None
    taker_long = ts if act == "buy" else ("no" if ts == "yes" else "yes")
    other = "no" if taker_long == "yes" else "yes"
    if t.get("taker_nickname") == nick:
        return "taker", taker_long
    if t.get("maker_nickname") == nick:
        return "maker", other
    return None, None


# ------------------------------------------------------------------ scoring (shared with tools/)
def side_value(side: str, yes_px: float) -> float:
    return yes_px if side == "yes" else 1 - yes_px


def grade(rows: list[dict], results: dict[str, str] | None = None) -> dict:
    """Per account (and "ALL"): lists of cents/contract on the sharp's side.
    move_N = mid N s after we saw the fill minus the sharp's price; follow_N = mid minus the ask a
    copier would have paid at detection; settle = settlement value minus that ask."""
    fills = {r["trade_id"]: r for r in rows if r.get("kind") == "fill" and not r.get("backfill")
             and r.get("side") in ("yes", "no") and r.get("yes_price") is not None}
    snaps: dict[str, dict] = defaultdict(dict)
    for r in rows:
        if r.get("kind") == "snap":
            snaps[r["trade_id"]][r["delay"]] = r
            if r.get("result") in ("yes", "no") and results is not None:
                results.setdefault(r["ticker"], r["result"])
    out: dict = defaultdict(lambda: defaultdict(list))
    for tid, f in fills.items():
        s = snaps.get(tid) or {}
        s0 = s.get(0)
        if not s0 or s0.get("bid") is None or s0.get("ask") is None:
            continue
        side = f["side"]
        entry = side_value(side, f["yes_price"])
        pay = s0["ask"] if side == "yes" else 1 - s0["bid"]
        for nick in (f["nick"], "ALL"):
            o = out[nick]
            o["lag"].append(f.get("lag_s") or 0)
            o["first_ts"].append(f.get("ts") or 0)
            for d, snap in s.items():
                if snap.get("bid") is None or snap.get("ask") is None:
                    continue
                mid = side_value(side, (snap["bid"] + snap["ask"]) / 2)
                o[f"move_{d}"].append(100 * (mid - entry))
                o[f"follow_{d}"].append(100 * (mid - pay))
            res = (results or {}).get(f["ticker"])
            if res in ("yes", "no"):
                o["settle"].append(100 * ((1.0 if res == side else 0.0) - pay))
    return out


def mean_low(vals: list[float]) -> tuple[float | None, float | None]:
    """Mean and a pessimistic (one-sided 95%) lower bound, in cents."""
    if not vals:
        return None, None
    m = st.mean(vals)
    se = st.pstdev(vals) / len(vals) ** 0.5 if len(vals) > 1 else abs(m) + 50
    return round(m, 2), round(m - 1.645 * se, 2)


_ROW_CACHE: dict[str, list[dict]] = {}


def _slim(r: dict) -> bool:
    """Rows the scoring needs: snapped fills and their snapshots (keeps memory small)."""
    return r.get("kind") == "snap" or (r.get("kind") == "fill" and r.get("snap", True)
                                         and not r.get("backfill"))


def load_rows(d: Path, cache_done_hours: bool = False, now: float | None = None) -> list[dict]:
    rows = []
    cur = (datetime.fromtimestamp(now, timezone.utc).strftime("copycat-%Y-%m-%d-%H.jsonl")
           if now else None)
    for p in sorted(d.glob("copycat-*.jsonl*")):
        if cache_done_hours and p.name in _ROW_CACHE:
            rows += _ROW_CACHE[p.name]
            continue
        raw = p.read_bytes()
        if p.suffix == ".gz":
            raw = gzip.decompress(raw)
        part = []
        for x in raw.decode().splitlines():
            try:
                r = json.loads(x)
            except ValueError:
                continue
            if _slim(r):
                part.append(r)
        if cache_done_hours and p.name != cur:
            _ROW_CACHE[p.name] = part
        rows += part
    return rows


def build_status(rows: list[dict], results: dict[str, str], watch: dict, now: float) -> dict:
    g = grade(rows, results)
    accounts = []
    for nick in sorted(set(watch) | {n for n in g if n != "ALL"}):
        o = g.get(nick) or {}
        n = len(o.get("lag", []))
        first = min(o["first_ts"]) if n else None
        days = (now - first) / 86400 if first else 0
        settle_all = o.get("settle", [])
        window = settle_all[-ROLL_WINDOW:]
        settle, settle_low = mean_low(window)
        f300, _ = mean_low(o.get("follow_300", []))
        f1800, _ = mean_low(o.get("follow_1800", []))
        due = n >= GO_LIVE_FILLS or days >= GO_LIVE_DAYS
        if not due:
            verdict = "watching"
        elif settle_low is not None and settle_low >= 0 and len(window) >= MIN_SETTLED:
            verdict = "passes"
        else:
            verdict = "fails"
        accounts.append({"nick": nick, "watched": nick in watch, **(watch.get(nick) or {}),
                         "fills": n, "settled": len(settle_all), "window": len(window), "days": round(days, 1),
                         "lag_s": round(st.median(o["lag"]), 1) if n else None,
                         "follow_5m_c": f300, "follow_30m_c": f1800,
                         "settle_c": settle, "settle_low_c": settle_low, "verdict": verdict})
    recent = [r for r in rows if r.get("kind") == "fill" and not r.get("backfill")][-40:]
    return {"generated": datetime.fromtimestamp(now, ET).strftime("%Y-%m-%d %H:%M ET"),
            "mode": "watch-only", "rule": f"judged at {GO_LIVE_FILLS} copied fills or {GO_LIVE_DAYS} days, then on a rolling "
            f"window of the last {ROLL_WINDOW} settled copies: passes while the pessimistic "
            f"copy-to-settlement estimate is >= 0 (min {MIN_SETTLED} settled), can pass again later",
            "accounts": accounts,
            "recent": [{k: r.get(k) for k in ("et", "nick", "ticker", "side", "yes_price", "count",
                                                  "role", "lag_s")} for r in reversed(recent)]}


class Copycat:
    def __init__(self, out_dir: Path, session: requests.Session | None = None, clock=time.time,
                 uploader: Callable[[str, bytes], None] | None = None, req_gap: float = REQ_GAP_S):
        self.out = out_dir
        self.out.mkdir(parents=True, exist_ok=True)
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": UA})
        self.clock = clock
        self.uploader = uploader
        self.req_gap = req_gap
        self.watch: dict[str, dict] = {}         # nick -> {"pnl":..., "cats":...}
        self.seen: dict[str, set[str]] = {}       # nick -> trade ids already logged
        self.last_snap: dict[tuple[str, str], float] = {}
        self.snaps: list[tuple[float, int, str, str, str]] = []  # (due, delay, nick, ticker, trade_id)
        self.watch_next = 0.0
        self.rr = 0
        self.uploaded: set[str] = set()
        self.results: dict[str, str] = {}
        self.status_next = 0.0
        self.checked: set[str] = set()           # names already looked up by the scout
        self.scout_next = 0.0
        self.hot: set[str] = set()               # passing accounts, polled more often
        self._lock = threading.Lock()

    # -- files
    def _hour_file(self, ts: float) -> Path:
        d = datetime.fromtimestamp(ts, timezone.utc)
        return self.out / f"copycat-{d:%Y-%m-%d-%H}.jsonl"

    def write(self, row: dict) -> None:
        row.setdefault("ts", round(self.clock(), 3))
        row["et"] = datetime.fromtimestamp(row["ts"], ET).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, open(self._hour_file(row["ts"]), "a") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")

    def upload_done_hours(self, now: float) -> None:
        if not self.uploader:
            return
        cur = self._hour_file(now).name
        for p in sorted(self.out.glob("copycat-*.jsonl")):
            if p.name == cur or p.name in self.uploaded:
                continue
            try:
                self.uploader(f"copycat/{p.name}.gz", gzip.compress(p.read_bytes()))
                self.uploaded.add(p.name)
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat upload %s: %s", p.name, exc)
                return

    # -- api
    def get(self, url: str, **params):
        r = self.s.get(url, params=params, timeout=20)
        if self.req_gap:
            time.sleep(self.req_gap)
        if r.status_code != 200:
            raise RuntimeError(f"{r.status_code} {url}")
        return r.json()

    def build_watch(self) -> None:
        names: dict[str, None] = dict.fromkeys(SEED)
        boards = [dict(metric_name="projected_pnl", since_day_before=0)]
        boards += [dict(metric_name="projected_pnl", since_day_before=0, category=c) for c in CATEGORIES]
        for b in boards:
            try:
                for r in self.get(f"{SOCIAL}/leaderboard", limit=100, **b).get("rank_list") or []:
                    if not r.get("is_anonymous") and r.get("nickname"):
                        names.setdefault(r["nickname"], None)
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat board %s: %s", b, exc)
        keep: dict[str, dict] = {}
        for n in names:
            if len(keep) >= MAX_WATCH:
                break
            try:
                t = self.get(f"{SOCIAL}/trades", nickname=n, limit=1)
                if not t.get("trades"):
                    continue
                m = self.get(f"{SOCIAL}/profile/metrics", nickname=n).get("metrics") or {}
                pnl = (m.get("pnl") or 0) / 1e4
                if pnl < MIN_PNL:
                    continue
                p = (self.get(f"{SOCIAL}/profile", nickname=n).get("social_profile") or {})
                keep[n] = {"pnl": round(pnl), "contracts": m.get("volume"),
                           "markets": m.get("num_markets_traded"),
                           "cats": p.get("top_categories"), "joined": p.get("joined_at")}
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat check %s: %s", n, exc)
        scouted = {n: v for n, v in self.watch.items() if v.get("src") == "scout" and n not in keep}
        keep.update(scouted)
        if keep:
            self.watch = dict(sorted(keep.items(), key=lambda kv: -(kv[1].get("pnl") or 0))[:MAX_WATCH])
        self.checked.update(names)
        self.write({"kind": "watch", "accounts": self.watch})

    def check_name(self, n: str) -> dict | None:
        m = self.get(f"{SOCIAL}/profile/metrics", nickname=n).get("metrics") or {}
        pnl = (m.get("pnl") or 0) / 1e4
        if pnl < MIN_PNL:
            return None
        p = (self.get(f"{SOCIAL}/profile", nickname=n).get("social_profile") or {})
        return {"pnl": round(pnl), "contracts": m.get("volume"), "markets": m.get("num_markets_traded"),
                "cats": p.get("top_categories"), "joined": p.get("joined_at")}

    def scout(self) -> None:
        """Reads recent pages of the global public trade feed and adds new profitable names."""
        names: dict[str, None] = {}
        cursor = None
        for _ in range(SCOUT_PAGES):
            params = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            j = self.get(f"{SOCIAL}/trades", **params)
            for t in j.get("trades") or []:
                for k in ("maker_nickname", "taker_nickname"):
                    if t.get(k) and t[k] not in self.checked and t[k] not in self.watch:
                        names.setdefault(t[k], None)
            cursor = j.get("cursor")
            if not cursor:
                break
        added = []
        for n in list(names)[:SCOUT_CHECKS]:
            self.checked.add(n)
            try:
                info = self.check_name(n)
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat scout %s: %s", n, exc)
                continue
            if not info:
                continue
            if len(self.watch) >= MAX_WATCH:
                cold = [k for k in self.watch if k not in self.hot]
                low = min(cold, key=lambda k: self.watch[k].get("pnl") or 0, default=None)
                if low is None or (self.watch[low].get("pnl") or 0) >= info["pnl"]:
                    continue
                self.watch.pop(low)
            self.watch[n] = dict(info, src="scout")
            added.append(n)
        if added:
            self.write({"kind": "scout", "added": added, "looked_up": min(len(names), SCOUT_CHECKS)})

    def snapshot(self, ticker: str) -> dict:
        m = self.get(f"{KALSHI}/markets/{ticker}").get("market") or {}
        return {"bid": _px(m.get("yes_bid_dollars", m.get("yes_bid"))),
                "ask": _px(m.get("yes_ask_dollars", m.get("yes_ask"))),
                "last": _px(m.get("last_price_dollars", m.get("last_price"))),
                "status": m.get("status"), "result": m.get("result") or None}

    # -- one account
    def poll(self, nick: str, now: float) -> None:
        trades = self.get(f"{SOCIAL}/trades", nickname=nick, limit=50).get("trades") or []
        first = nick not in self.seen
        seen = self.seen.setdefault(nick, set())
        for t in reversed(trades):
            tid = t.get("trade_id")
            if not tid or tid in seen:
                continue
            seen.add(tid)
            role, side = sharp_side(t, nick)
            row = {"kind": "fill", "nick": nick, "trade_id": tid, "ticker": t.get("ticker"),
                   "role": role, "side": side, "yes_price": _px(t.get("price_dollars")),
                   "count": t.get("count_fp", t.get("count")), "trade_time": t.get("create_date"),
                   "backfill": first}
            try:
                ct = datetime.fromisoformat(str(t.get("create_date")).replace("Z", "+00:00")).timestamp()
                row["lag_s"] = round(now - ct, 1)
            except ValueError:
                pass
            key = (nick, t.get("ticker") or "")
            row["snap"] = not (first or now - self.last_snap.get(key, 0) < SNAP_GAP_S
                               or len(self.snaps) > MAX_PENDING)
            self.write(row)
            if not row["snap"]:
                continue
            self.last_snap[key] = now
            for d in SNAP_DELAYS:
                self.snaps.append((now + d, d, nick, key[1], tid))
        if len(seen) > 5000:                       # keep memory bounded
            self.seen[nick] = {t.get("trade_id") for t in trades}

    def run_snaps(self, now: float) -> None:
        due = [x for x in self.snaps if x[0] <= now]
        self.snaps = [x for x in self.snaps if x[0] > now]
        for _, d, nick, ticker, tid in sorted(due):
            try:
                self.write({"kind": "snap", "nick": nick, "ticker": ticker, "trade_id": tid,
                            "delay": d, **self.snapshot(ticker)})
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat snap %s: %s", ticker, exc)

    def step(self) -> None:
        now = self.clock()
        if now >= self.watch_next:
            self.watch_next = now + WATCH_REFRESH_S
            self.build_watch()
        if now >= self.scout_next and self.watch_next > now:
            self.scout_next = now + SCOUT_EVERY_S
            try:
                self.scout()
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat scout: %s", exc)
        names = list(self.watch) + [n for n in self.watch if n in self.hot] * 2
        if names:
            nick = names[self.rr % len(names)]
            self.rr += 1
            try:
                self.poll(nick, self.clock())
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat poll %s: %s", nick, exc)
        self.run_snaps(self.clock())

    def update_status(self, now: float) -> dict:
        rows = load_rows(self.out, cache_done_hours=True, now=now)
        old = now - 900
        need = sorted({r["ticker"] for r in rows if r.get("kind") == "fill" and not r.get("backfill")
                       and (r.get("ts") or now) < old and r.get("ticker") not in self.results})
        for t in need[:30]:
            try:
                res = self.snapshot(t).get("result")
                if res in ("yes", "no"):
                    self.results[t] = res
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat result %s: %s", t, exc)
        status = build_status(rows, self.results, self.watch, now)
        self.hot = {a["nick"] for a in status["accounts"] if a["verdict"] == "passes"}
        (self.out / "status.json").write_text(json.dumps(status, separators=(",", ":")))
        if self.uploader:
            try:
                self.uploader("copycat/status.json", json.dumps(status).encode())
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat status upload: %s", exc)
        return status

    def run(self) -> None:
        last_up = 0.0
        self.write({"kind": "start"})
        while True:
            try:
                self.step()
                if self.clock() - last_up > 300:
                    last_up = self.clock()
                    self.upload_done_hours(last_up)
                    self.update_status(last_up)
            except Exception as exc:  # noqa: BLE001  never die; log and keep going
                log.warning("copycat step: %s", exc)
                time.sleep(10)
            if not self.watch:
                time.sleep(60)


def main() -> int:
    out = Path(os.environ.get("COPYCAT_DIR", "/var/lib/kalshi-copycat"))
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    for k in list(os.environ):          # shadow only: drop the trading key and other secrets
        if k.startswith(("KALSHI", "NTFY", "ANTHROPIC", "OANDA")):
            os.environ.pop(k, None)
    Copycat(out, uploader=github_put(token, branch=BRANCH) if token else None).run()
    return 0
