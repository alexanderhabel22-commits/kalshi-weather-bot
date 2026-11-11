"""Copycat (Nov 10, 2026; leaderboard study idea 2): follow proven winners' public trades.

Two records per account, kept apart: PAPER (every trade of theirs we saw, scored as if we had
copied it at the price left ~10-20 s later, after fees) and REAL (copies actually bought with
money, wxbot/copycat_live.py). Real copies only happen for accounts whose paper record passes
(100 copies or 7 days, score 80+), and only while copycat_control.json on main says "live".
The boss (wxbot/copycat_boss.py, Claude) reviews the accounts and can bench them.

How it works (one thread, polite polling of Kalshi's public social API):
  - every 12 h, build the watch list: profitable accounts (lifetime pnl >= MIN_PNL) from the
    all-time and category profit boards whose trade history is public; every 10 min, also scout the
    global public trade feed (every trader with a public profile) and add any new profitable name;
  - each account is scored on a rolling window of its last 50 settled copies: it passes while the
    pessimistic estimate is >= 0 (min 20 settled), fails when it isn't, and can pass again later;
  - passing accounts get every other poll (each checked every few seconds);
  - read the global public trade feed about every second (one read covers every watched account),
    and also poll each watched account's own trades round-robin as a backstop;
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
MIN_PNL = 500               # dollars of lifetime profit to be watched (Alex Nov 10: as many as possible)
MAX_WATCH = 1000            # the global feed covers every account in one read, so more is cheap
SCOUT_EVERY_S = 600         # scan the global public feed for new profitable names
SCOUT_PAGES = 10            # 100 trades per page
SCOUT_CHECKS = 80           # new names looked up per scan
ROLL_WINDOW = 50            # an account's verdict uses its last 50 settled copies
MIN_SETTLED = 20
SNAP_DELAYS = (0, 60, 300)  # +0 is the copy price; the rest show the move after (30 min dropped Nov 10 for capacity)
SNAP_GAP_S = 300            # at most one snapshot series per (account, ticker) per 5 min
MAX_PENDING = 4000          # cap on queued snapshots
FEED_EVERY_S = 1.0          # global public trade feed: every account's new trades in one read
FEED_PAGES = 3
REQ_GAP_S = 0.3             # spacing between API calls (~150/min; a 3/s test had 0 errors)
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
def taker_fee_c(p: float, n: int = 1) -> float:
    """Kalshi taker fee in cents per contract: ceil(7% x n x p x (1-p)) dollars, per order."""
    import math
    return math.ceil(round(7 * n * p * (1 - p), 6)) / max(1, n)


def side_value(side: str, yes_px: float) -> float:
    return yes_px if side == "yes" else 1 - yes_px


def grade(rows: list[dict], results: dict[str, str] | None = None) -> dict:
    """Per account (and "ALL"): lists of cents/contract on the sharp's side.
    move_N = mid N s after we saw the fill minus the sharp's price; follow_N = mid minus the ask a
    copier would have paid at detection (and the taker fee); settle = settlement value minus that
    ask and fee. Lists are in time order."""
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
        fee = taker_fee_c(pay)                 # copying always takes the ask
        for nick in (f["nick"], "ALL"):
            o = out[nick]
            o["lag"].append(f.get("lag_s") or 0)
            o["first_ts"].append(f.get("ts") or 0)
            for d, snap in s.items():
                if snap.get("bid") is None or snap.get("ask") is None:
                    continue
                mid = side_value(side, (snap["bid"] + snap["ask"]) / 2)
                o[f"move_{d}"].append(100 * (mid - entry))
                o[f"follow_{d}"].append(100 * (mid - pay) - fee)
            res = (results or {}).get(f["ticker"])
            if res in ("yes", "no"):
                o["settle"].append(100 * ((1.0 if res == side else 0.0) - pay) - fee)
        res = (results or {}).get(f["ticker"])
        out[f["nick"]]["trades"].append({
            "et": (f.get("et") or "")[5:16], "ticker": f.get("ticker"), "side": side,
            "their_c": round(100 * entry), "pay_c": round(100 * pay), "count": f.get("count"),
            "role": f.get("role"), "result": res,
            "c": round(100 * ((1.0 if res == side else 0.0) - pay) - fee, 1) if res in ("yes", "no") else None})
    return out


def detail(o: dict) -> dict:
    """The pop-up report card: best/worst copies, latest trades and how the account trades."""
    tr = o.get("trades") or []
    done = [t for t in tr if t["c"] is not None]
    ser = defaultdict(lambda: [0, 0.0, 0])           # series -> [copies, cents sum, settled]
    for t in tr:
        k = (t["ticker"] or "").split("-")[0]
        ser[k][0] += 1
        if t["c"] is not None:
            ser[k][1] += t["c"]
            ser[k][2] += 1
    prices = sorted(t["pay_c"] for t in tr)
    sizes = sorted(float(t["count"] or 0) for t in tr)
    hours = defaultdict(int)
    for t in tr:
        if len(t["et"]) >= 8:
            hours[t["et"][6:8]] += 1
    bands = {"5-25c": 0, "25-50c": 0, "50-75c": 0, "75-95c": 0, "other": 0}
    for p in prices:
        b = "5-25c" if 5 <= p < 25 else "25-50c" if p < 50 else "50-75c" if p < 75 else "75-95c" if p <= 95 else "other"
        bands[b] += 1
    med = lambda v: v[len(v) // 2] if v else None  # noqa: E731
    return {
        "best": sorted(done, key=lambda t: -t["c"])[:5], "worst": sorted(done, key=lambda t: t["c"])[:5],
        "latest": list(reversed(tr[-10:])),
        "won": sum(1 for t in done if t["c"] > 0), "lost": sum(1 for t in done if t["c"] <= 0),
        "series": [{"s": k, "n": v[0], "settled": v[2], "avg_c": round(v[1] / v[2], 1) if v[2] else None}
                   for k, v in sorted(ser.items(), key=lambda kv: -kv[1][0])[:8]],
        "bands": bands, "median_price_c": med(prices), "median_size": med(sizes),
        "maker_pct": round(100 * sum(1 for t in tr if t["role"] == "maker") / len(tr)) if tr else None,
        "busiest_hours_et": [h for h, _ in sorted(hours.items(), key=lambda kv: -kv[1])[:3]],
        "follow_1m_c": mean_low(o.get("follow_60", []))[0],
    }


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


LETTERS = ((97, "A+"), (93, "A"), (90, "A-"), (87, "B+"), (83, "B"), (80, "B-"), (77, "C+"),
           (73, "C"), (70, "C-"), (67, "D+"), (63, "D"), (60, "D-"))
COPY_SCORE = 80.0           # B- or better is copied


def score_grade(settle: list[float]) -> tuple[float | None, str]:
    """A test-style score (0-100) and letter from the whole copy history (cents per contract after
    fees). It is built on the pessimistic mean, so a short lucky run can't score high:
    80 + 2.5 x (lower 95% bound of the mean, in cents), so 0c = 80 (B-), +4c = 90 (A-), +7c = 97.5 (A+),
    -4c = 70 (C-), below -8c = F. A small recency term (+/-3) rewards a hot last 50 or marks a cold one."""
    if len(settle) < MIN_SETTLED:
        return None, "-"
    m, low = mean_low(settle)
    rec = st.mean(settle[-ROLL_WINDOW:]) - m
    score = max(0.0, min(100.0, 80 + 2.5 * low + max(-3.0, min(3.0, rec * 0.25))))
    letter = next((lt for cut, lt in LETTERS if score >= cut), "F")
    return round(score, 1), letter


def leeway_c(banked: float, score: float | None = None) -> float:
    """How bad (c/contract) its last 50 copies may average before it is benched: 5c for a newcomer,
    +1c per $1 it has made us on 1-contract copies (max +15c), +up to 5c for a high score."""
    extra = max(0.0, min(5.0, ((score or 80) - 80) / 2))
    return 5.0 + max(0.0, min(15.0, banked)) + extra


def size_for(score: float | None, n: int) -> int:
    if score is None:
        return 0
    return 3 if score >= 95 and n >= 300 else 2 if score >= 90 else 1


def build_status(rows: list[dict], results: dict[str, str], watch: dict, now: float,
                 account_stops: dict | None = None, notes: dict | None = None) -> dict:
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
        score, gl = score_grade(settle_all)
        banked = sum(settle_all) / 100            # $ a 1-contract copy would have made, all-time
        leeway = leeway_c(banked, score)
        copyable = score is not None and score >= COPY_SCORE
        rmean = st.mean(window) if window else None
        if not due:
            verdict = "watching"
        elif (account_stops or {}).get(nick):
            verdict = "benched"                   # its own real-money loss stop, or the boss
        elif copyable and rmean is not None and rmean >= -leeway:
            verdict = "passes"
        elif copyable:
            verdict = "slump"                     # good record, recent run below its leeway
        else:
            verdict = "fails"
        accounts.append({"nick": nick, "watched": nick in watch, **(watch.get(nick) or {}),
                         "fills": n, "settled": len(settle_all), "window": len(window), "days": round(days, 1),
                         "lag_s": round(st.median(o["lag"]), 1) if n else None,
                         "follow_5m_c": f300, "follow_30m_c": f1800,
                         "settle_c": settle, "settle_low_c": settle_low, "verdict": verdict,
                         "grade": gl, "score": score, "banked": round(banked, 2), "leeway_c": round(leeway, 1),
                         "size": size_for(score, len(settle_all)) if verdict == "passes" else 0,
                         "boss_note": (notes or {}).get(nick), "detail": detail(o) if n else None})
    recent = [r for r in rows if r.get("kind") == "fill" and not r.get("backfill")][-40:]
    return {"generated": datetime.fromtimestamp(now, ET).strftime("%Y-%m-%d %H:%M ET"),
            "mode": "watch-only", "rule": f"judged at {GO_LIVE_FILLS} copied fills or {GO_LIVE_DAYS} days. Score 0-100 from every copy so far "
            "(after fees; 80 = break-even even in the pessimistic case). Copied at 80+ (B- or better) while its "
            f"last {ROLL_WINDOW} copies average better than -leeway (5c, +1c per $1 it has made us, +up to 5c "
            "for a high score); benched below that, back when it recovers. Size: 90+ = 2, 95+ (300 copies) = 3",
            "accounts": accounts,
            "recent": [{k: r.get(k) for k in ("et", "nick", "ticker", "side", "yes_price", "count",
                                                  "role", "lag_s")} for r in reversed(recent)]}


def add_real(status: dict, state: dict | None) -> None:
    """Puts each account's REAL-money record next to its paper (shadow) record."""
    pos = (state or {}).get("positions") or {}
    for a in status["accounts"]:
        mine = [p for p in pos.values() if p.get("nick") == a["nick"]]
        done = [p for p in mine if p.get("settled")]
        a["real"] = {"copies": len(mine), "open": len(mine) - len(done), "settled": len(done),
                     "won": sum(1 for p in done if (p.get("pnl") or 0) > 0),
                     "contracts": round(sum(p.get("n") or 0 for p in mine), 2),
                     "open_cost": round(sum(p.get("cost") or 0 for p in mine if not p.get("settled")), 2),
                     "pnl": round(((state or {}).get("pnl") or {}).get(a["nick"], 0.0), 2)}


class Copycat:
    def __init__(self, out_dir: Path, session: requests.Session | None = None, clock=time.time,
                 uploader: Callable[[str, bytes], None] | None = None, req_gap: float = REQ_GAP_S,
                 live=None, boss=None, alert: Callable[[str, str], None] | None = None):
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
        self.acct: dict[str, dict] = {}          # nick -> last status row
        self.live = live                         # copycat_live.Live (real money) or None
        self.boss = boss                         # copycat_boss.Boss or None
        self.alert = alert or (lambda t, b: None)
        self.copied_once: set[str] = set()
        self.cats: dict[str, str | None] = {}    # series -> Kalshi category
        self.feed_seen: set[str] = set()         # trade ids already read from the global feed
        self.feed_next = 0.0
        self.scout_queue: dict[str, None] = {}    # names seen in the feed, not looked up yet
        self.feed_names: set[str] = set()         # every public trader seen in the feed
        try:                                      # restart fast: reuse the saved watch list
            w = json.loads((self.out / "watch.json").read_text())
            self.watch = w.get("watch") or {}
            self.watch_next = (w.get("at") or 0) + WATCH_REFRESH_S
        except (OSError, ValueError):
            pass
        self._boss_busy = False
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
            self.tick()
            if n in self.watch and self.watch[n].get("src") != "scout":
                keep[n] = self.watch[n]          # already checked; refreshed on restart only
                continue
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
        self.save_watch()

    def save_watch(self) -> None:
        try:
            (self.out / "watch.json").write_text(json.dumps({"at": self.clock(), "watch": self.watch}))
        except OSError as exc:
            log.warning("copycat watch save: %s", exc)

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
        names: dict[str, None] = {n: None for n in self.scout_queue if n not in self.checked}
        self.scout_queue = {}
        cursor = None
        for _ in range(SCOUT_PAGES if len(names) < SCOUT_CHECKS else 0):
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
            self.tick()
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
            self.save_watch()
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
        self.ingest(nick, trades, now)
        if len(self.seen[nick]) > 5000:            # keep memory bounded
            self.seen[nick] = {t.get("trade_id") for t in trades}

    def tick(self) -> None:
        """Keeps the fast feed going during long jobs (watch-list rebuild, scouting)."""
        now = self.clock()
        if now >= self.feed_next:
            self.feed_next = now + FEED_EVERY_S
            try:
                self.poll_feed(now)
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat feed: %s", exc)

    def poll_feed(self, now: float) -> None:
        """One read of Kalshi's global public trade feed covers every watched account at once
        (newest first, 100 per page; reads up to FEED_PAGES pages back to the last trade seen).
        Accounts are only fed from here after their own first poll, so backfill stays marked."""
        new, cursor = [], None
        for _ in range(FEED_PAGES):
            params = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            j = self.get(f"{SOCIAL}/trades", **params)
            page = j.get("trades") or []
            fresh = [t for t in page if t.get("trade_id") and t["trade_id"] not in self.feed_seen]
            new += fresh
            cursor = j.get("cursor")
            if not self.feed_seen or len(fresh) < len(page) or not cursor:
                break
        self.feed_seen.update(t["trade_id"] for t in new)
        if len(self.feed_seen) > 50_000:
            self.feed_seen = {t["trade_id"] for t in new}
        by: dict[str, list] = defaultdict(list)
        for t in new:
            for k in ("taker_nickname", "maker_nickname"):
                n = t.get(k)
                if n and len(self.feed_names) < 500_000:
                    self.feed_names.add(n)
                if n and n not in self.watch and n not in self.checked and len(self.scout_queue) < 2000:
                    self.scout_queue[n] = None        # looked up by the next scout
                if n and n in self.watch and n in self.seen:
                    by[n].append(t)
        for n, ts in by.items():
            self.ingest(n, ts, now)

    def ingest(self, nick: str, trades: list[dict], now: float) -> None:
        """Logs an account's new trades (newest first, as the API sends them)."""
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
            if not first and self.live is not None and nick in self.hot:
                self.try_copy(row, now)
            if not row["snap"]:
                continue
            self.last_snap[key] = now
            for d in SNAP_DELAYS:
                self.snaps.append((now + d, d, nick, key[1], tid))

    def category(self, ticker: str) -> str | None:
        """Kalshi category of a market's series (e.g. 'Sports'), cached."""
        ser = ticker.split("-")[0]
        if ser and ser not in self.cats:
            try:
                self.cats[ser] = (self.get(f"{KALSHI}/series/{ser}").get("series") or {}).get("category")
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat series %s: %s", ser, exc)
                return "unknown"            # unknown = not copied (fails the category check below)
        return self.cats.get(ser)

    def try_copy(self, row: dict, now: float) -> None:
        """Real money: hand a passing account's new fill to the live rails (they decide)."""
        try:
            self.live.refresh(now)
            if self.live.mode(now) != "live":
                return
            snap = self.snapshot(row["ticker"]) if row.get("ticker") else {}
            snap["category"] = self.category(row.get("ticker") or "")
            what = self.live.maybe_copy(self.acct.get(row["nick"]), row, snap, now)
        except Exception as exc:  # noqa: BLE001
            what = f"error: {str(exc)[:120]}"
        self.write({"kind": "copy", "nick": row["nick"], "ticker": row.get("ticker"),
                    "trade_id": row.get("trade_id"), "what": what})
        if what == "copied" and row["nick"] not in self.copied_once:
            self.copied_once.add(row["nick"])
            p = self.live.state["positions"].get(row.get("ticker")) or {}
            self.alert("Copycat: first real copy", f"Copied {row['nick']}: {p.get('n')} x {row.get('side')} "
                       f"{row.get('ticker')} for ${p.get('cost')}.")

    def run_snaps(self, now: float) -> None:
        due = [x for x in self.snaps if x[0] <= now]
        self.snaps = [x for x in self.snaps if x[0] > now]
        for _, d, nick, ticker, tid in sorted(due):
            self.tick()                          # the 1-second feed always comes first
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
        self.tick()
        hot = [n for n in self.watch if n in self.hot]
        names = list(self.watch)
        if names:
            # every other poll goes to a passing (real-money) account, so each of them is checked
            # every few seconds; Kalshi's own publishing delay (~2-20 s) is the floor
            self.rr += 1
            nick = hot[(self.rr // 2) % len(hot)] if hot and self.rr % 2 else names[(self.rr // 2) % len(names)]
            try:
                self.poll(nick, self.clock())
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat poll %s: %s", nick, exc)
        self.run_snaps(self.clock())

    def update_status(self, now: float) -> dict:
        rows = load_rows(self.out, cache_done_hours=True, now=now)
        old = now - 900
        held = [t for t, p in (self.live.state["positions"].items() if self.live else [])
                if not p.get("settled") and t not in self.results]
        need = held + sorted({r["ticker"] for r in rows if r.get("kind") == "fill" and not r.get("backfill")
                              and (r.get("ts") or now) < old and r.get("ticker") not in self.results}
                             - set(held))
        for t in need[:40]:
            try:
                res = self.snapshot(t).get("result")
                if res in ("yes", "no"):
                    self.results[t] = res
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat result %s: %s", t, exc)
        stops, notes, live = {}, {}, None
        if self.live is not None:
            self.live.refresh(now, force=True)
            self.live.settle(self.results, now)
            stops.update(self.live.benched())
            live = self.live.summary(now)
        if self.boss is not None:
            stops.update({k: "boss: " + v["reason"] for k, v in self.boss.benches(now).items()})
            notes = dict(self.boss.state["notes"])
        status = build_status(rows, self.results, self.watch, now, account_stops=stops, notes=notes)
        add_real(status, self.live.state if self.live else None)
        status["live"] = live
        status["scan"] = {"public_traders_seen": len(self.feed_names), "looked_up": len(self.checked),
                          "watched": len(self.watch), "min_profit": MIN_PNL, "max_watch": MAX_WATCH}
        status["mode"] = live["mode"] if live and live["mode"] == "live" else "paper only"
        if self.boss is not None:
            status["boss"] = {"summary": self.boss.state.get("summary"), "usage": self.boss.state.get("usage"),
                              "reviews_today": self.boss.state.get("deep_n"),
                              "benches": self.boss.benches(now)}
        self.acct = {a["nick"]: a for a in status["accounts"]}
        self.hot = {a["nick"] for a in status["accounts"] if a["verdict"] == "passes"}
        (self.out / "status.json").write_text(json.dumps(status, separators=(",", ":")))
        if self.uploader:
            try:
                self.uploader("copycat/status.json", json.dumps(status).encode())
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat status upload: %s", exc)
        if self.boss is not None and not self._boss_busy:
            self._boss_busy = True
            threading.Thread(target=self._run_boss, args=(status, rows, live), daemon=True).start()
        return status

    def _run_boss(self, status: dict, rows: list[dict], live: dict | None) -> None:
        try:
            self.boss.step(status, rows, live)
        except Exception as exc:  # noqa: BLE001
            log.warning("copycat boss: %s", exc)
        finally:
            self._boss_busy = False

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
    from wxbot import notify
    from wxbot.copycat_boss import Boss, make_client
    from wxbot.copycat_live import Live
    from wxbot.m15 import client_from_env
    from wxbot.util import Http, load_config

    out = Path(os.environ.get("COPYCAT_DIR", "/var/lib/kalshi-copycat"))
    out.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    os.environ.pop("OANDA_TOKEN", None)
    cfg = load_config()
    http = Http(UA, min_interval=0.06)

    def alert(title: str, body: str) -> None:
        try:
            notify.send(http, cfg, title, body, priority=4, tags=["busts_in_silhouette"])
        except Exception as exc:  # noqa: BLE001
            log.warning("copycat alert: %s", exc)

    client = client_from_env(http)        # real orders only when copycat_control.json says "live"
    live = Live(out, client=client, fetch_json=http.get_json, alert=alert)
    boss = Boss(out, client=make_client(), alert=alert)
    cc = Copycat(out, uploader=github_put(token, branch=BRANCH) if token else None,
                 live=live, boss=boss, alert=alert)
    live.write = cc.write
    cc.run()
    return 0
