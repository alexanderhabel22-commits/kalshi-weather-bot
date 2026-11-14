"""Copycat (Nov 10, 2026; leaderboard study idea 2): follow proven winners' public trades.

Two records per account, kept apart: PAPER (every trade of theirs we saw, scored as if we had
copied it at the price left ~10-20 s later, after fees) and REAL (copies actually bought with
money, wxbot/copycat_live.py). Real copies only happen for accounts whose paper record passes
(100 paper copies at a B+ score, else 150; score 80+ to copy), and only while copycat_control.json on main says "live".
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
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import requests

from wxbot.copycat_live import DEFAULTS as LIVE, event_of
from wxbot.srclog import UA, github_put

log = logging.getLogger("wxbot")
ET = ZoneInfo("America/New_York")
SOCIAL = "https://api.elections.kalshi.com/v1/social"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
CATS_REFRESH_S = 24 * 3600                # full series -> category map, once a day
CATS_RETRY_S = 3600                       # after a failed refresh
BRANCH = "copycat-data"
MIN_PNL = 100               # watched if lifetime profit >= $100 OR last-30-day profit >= MIN_PNL_30D
MIN_PNL_30D = 100           # (Alex Nov 10: as many as possible; catches lifetime losers on a hot run)
MAX_WATCH = 1000            # the global feed covers every account in one read, so more is cheap
SCOUT_EVERY_S = 600         # scan the global public feed for new profitable names
SCOUT_PAGES = 10            # 100 trades per page
SCOUT_CHECKS = 80           # new names looked up per scan
ROLL_WINDOW = 50            # an account's verdict uses its last 50 settled copies
MIN_SETTLED = 20
SNAP_DELAYS = (0, 60, 300)  # +0 is the copy price; the rest show the move after (30 min dropped Nov 10 for capacity)
SNAP_GAP_S = 300            # at most one snapshot series per (account, ticker) per 5 min
MAX_PENDING = 4000          # cap on queued snapshots
FEED_EVERY_S = 1.0          # check the global public trade feed's first page every second
FEED_PAGES = 200            # Kalshi serves the feed as a snapshot refreshed every ~20-30 s (probe, Nov 11:
FEED_GAP_S = 0.05           # ~250-400 trades/s, so a refresh holds ~50-100 new pages); on each refresh
                            # read every new page, quickly, back to the last trade already seen
REQ_GAP_S = 0.3             # spacing between API calls (~150/min; a 3/s test had 0 errors)
WATCH_REFRESH_S = 12 * 3600
STATUS_EVERY_S = 300
ROW_V = 2                   # fresh start (Alex, Nov 10 10:20 PM ET): fills logged before the 1-second
                            # feed (v1, 20-100 s late) don't count toward any score
GO_LIVE_FILLS = 100         # tiered test (Alex, Nov 10): B+ (87+) accounts qualify at 100 paper copies,
GO_LIVE_FAST_SCORE = 87.0   # everyone else needs 150; no time-based shortcut (a quiet account can't
GO_LIVE_SLOW_FILLS = 150    # qualify on a handful of trades)
GO_LIVE_SETTLED = 40        # and at least 40 SETTLED copies before real money (Nov 14)
DROP_DAYS = 30              # dropped accounts (failed the 150-copy test, or long-run losers) are not
                            # watched, polled or snapshotted; after 30 days they may be re-scouted
                            # and start again from zero (Alex, Nov 10 10:31 PM ET)
RECHECK_S = 7 * 86400       # re-check each watched account's Kalshi profit weekly ...
RECHECK_PER_SCOUT = 15      # ... a few per scout run, oldest check first
TEST_CHANCES = 3            # Alex (Nov 11): "don't disqualify someone for a bad day". An account that fails
RETEST_EVERY = 50           # its paper test (150 copies) is re-tested after each further 50 copies, judged on
                            # its LAST 150 copies (so a bad day rolls off); dropped only after 3 fails in a row
LOSER_STRIKES = 2           # dropped after 2 weekly checks in a row losing money over the last 30 days
FEED_KEEP_DAYS = 7          # every public trade (anonymous too) kept on the server, rolling (Alex, Nov 11):
FEED_MIN_FREE_GB = 3.0      # ~300 trades/s, ~0.5 GB/day gzipped; oldest files go first if disk runs low
WHALE_USD = 500             # "big bet" scanner (Alex, Nov 11 12:23 AM ET): any named trade of $500+
WHALE_NEW_DAYS = 30         # flagged NEW if the account joined within 30 days or has traded <= 10 markets;
WHALE_FEW_MARKETS = 10      # paper only: we log the bet and score how it settles, never copy it
WHALE_ANON_USD = 2000       # anonymous trades: no account to check, so only bigger bets ($2,000+, the taker's side)
WHALE_CHECKS = 4            # profile lookups per loop (cached per account)
RESULT_BATCH = 100          # settlement results: markets per call, and calls per status update (every 5 min),
RESULT_CALLS = 20           # so up to 2,000 markets are checked each time, least recently checked first
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


def orders(trades: list[dict]) -> list[dict]:
    """Groups feed fills into whole orders for the big-bet scanner. A taker's order that sweeps the
    book shows up as many fills with the same timestamp; so does a maker's resting order filled by
    several takers at once. Each group becomes one trade-like row (count summed, price averaged)."""
    g: dict[tuple, dict] = {}
    for t in trades:
        px, n = _px(t.get("price_dollars")), t.get("count_fp", t.get("count"))
        try:
            n = float(n)
        except (TypeError, ValueError):
            continue
        if px is None:
            continue
        for role in ("taker", "maker"):
            nick = t.get(f"{role}_nickname") or ""
            if role == "maker" and not nick:
                continue
            key = (role, nick, t.get("ticker"), t.get("taker_side"), t.get("taker_action", "buy"), t.get("create_date"))
            o = g.get(key)
            if o is None:
                o = g[key] = {"trade_id": t.get("trade_id"), "ticker": t.get("ticker"), "taker_side": t.get("taker_side"),
                              "taker_action": t.get("taker_action", "buy"), "create_date": t.get("create_date"),
                              "taker_nickname": nick if role == "taker" else "",
                              "maker_nickname": nick if role == "maker" else "", "_n": 0.0, "_px": 0.0, "fills": 0}
            o["_n"] += n
            o["_px"] += n * px
            o["fills"] += 1
    out = []
    for o in g.values():
        if o["_n"] > 0:
            o["count_fp"] = round(o["_n"], 2)
            o["price_dollars"] = round(o["_px"] / o["_n"], 4)
            out.append(o)
    return out


def whale_summary(rows: list[dict], results: dict[str, str], now: float) -> dict:
    """Paper record of big single bets: per contract, after the taker fee, as if copied at their price."""
    out = {"new": [], "old": [], "anon": [], "new_ours": []}
    recent = []
    for r in rows:
        if r.get("kind") != "whale":
            continue
        res = results.get(r.get("ticker") or "")
        c = None
        if res in ("yes", "no") and r.get("paid") is not None:
            c = round(100 * ((1.0 if r["side"] == res else 0.0) - r["paid"]) - taker_fee_c(r["paid"]), 1)
            out["anon" if r.get("anon") else "new" if r.get("new") else "old"].append(c)
            if r.get("new") and r.get("ours") is not None:   # as we'd really have copied it
                out["new_ours"].append(round(100 * ((1.0 if r["side"] == res else 0.0) - r["ours"])
                                             - taker_fee_c(r["ours"]), 1))
        recent.append({**{k: r.get(k) for k in ("et", "nick", "ticker", "side", "paid", "count", "usd", "joined",
                                                    "markets", "new", "anon", "role", "lag_s", "ours")}, "result_c": c})

    def stats(v):
        m, lo = mean_low(v)
        return {"settled": len(v), "won": sum(1 for x in v if x > 0), "avg_c": m, "low_c": lo}
    nw = [x for x in recent if x["new"]]
    an = [x for x in recent if x.get("anon")]
    named = [x for x in recent if not x.get("anon")]
    return {"min_usd": WHALE_USD, "anon_min_usd": WHALE_ANON_USD, "new_days": WHALE_NEW_DAYS,
            "count": len(named), "new_count": len(nw), "anon_count": len(an),
            "new": stats(out["new"]), "new_ours": stats(out["new_ours"]), "old": stats(out["old"]),
            "anon": stats(out["anon"]),
            "recent_new": list(reversed(nw))[:40], "recent_anon": sorted(an, key=lambda x: -(x["usd"] or 0))[:10],
            "recent": list(reversed(named))[:40]}


def real_ok(their: float, pay: float, lag: float | None) -> bool:
    """Would the real-money rails have taken this copy? (their price + 2c at most, 5-95c, seen in time).
    Only such copies count toward an account's score (Nov 14 audit: 62% of paper copies were trades
    real money could never get, and copies where the price had run more than 2c lost 11.9c)."""
    return (pay <= round(min(their + LIVE["slip"], LIVE["max_px"]), 2) + 1e-9 and pay >= LIVE["min_px"]
            and (lag or 0) <= LIVE["max_lag_s"])


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
        elif r.get("kind") == "result" and results is not None:
            results.setdefault(r["ticker"], r["result"])
    out: dict = defaultdict(lambda: defaultdict(list))
    for tid, f in fills.items():
        s = ({d: {"bid": b, "ask": a} for d, (b, a) in f["_s"].items()} if f.get("_s")
             else snaps.get(tid) or {})
        s0 = s.get(0)
        if not s0 or s0.get("bid") is None or s0.get("ask") is None:
            continue
        side = f["side"]
        entry = side_value(side, f["yes_price"])
        pay = s0["ask"] if side == "yes" else 1 - s0["bid"]
        fee = taker_fee_c(pay)                 # copying always takes the ask
        ok = real_ok(entry, pay, f.get("lag_s"))
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
            if res in ("yes", "no") and ok:    # the score: only copies real money could have made
                o["settle"].append(100 * ((1.0 if res == side else 0.0) - pay) - fee)
                o["settle_ev"].append(event_of(f.get("ticker") or ""))
        res = (results or {}).get(f["ticker"])
        out[f["nick"]]["trades"].append({
            "tid": tid, "et": (f.get("et") or "")[5:16], "ticker": f.get("ticker"), "side": side,
            "their_c": round(100 * entry), "pay_c": round(100 * pay), "count": f.get("count"),
            "role": f.get("role"), "result": res, "ok": ok,
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
        "curve": _curve([t["c"] for t in done]),
        "pts": [[t["pay_c"], t["c"]] for t in done[-150:]],
        "hours": [hours.get(f"{h:02d}", 0) for h in range(24)],
    }


def _curve(vals: list[float], pts: int = 120) -> list[float]:
    """Running total of paper copy results in cents (1 contract each), thinned to <= pts points."""
    out, tot = [], 0.0
    for v in vals:
        tot += v
        out.append(round(tot, 1))
    if len(out) > pts:
        step = len(out) / pts
        out = [out[int(i * step)] for i in range(pts - 1)] + [out[-1]]
    return out


def _pcts(v: list[float]) -> dict | None:
    """Median / 90th percentile / count, for delay stats."""
    if not v:
        return None
    s = sorted(v)
    return {"p50": s[len(s) // 2], "p90": s[int(len(s) * 0.9)], "n": len(s)}


def mean_low(vals: list[float]) -> tuple[float | None, float | None]:
    """Mean and a pessimistic (one-sided 95%) lower bound, in cents."""
    if not vals:
        return None, None
    m = st.mean(vals)
    se = st.pstdev(vals) / len(vals) ** 0.5 if len(vals) > 1 else abs(m) + 50
    return round(m, 2), round(m - 1.645 * se, 2)


def mean_low_events(vals: list[float], events: list[str]) -> tuple[float | None, float | None, int]:
    """Mean, pessimistic (one-sided 95%) lower bound and number of events, counting copies in the same
    event (game) together: ten copies in one game are one piece of evidence, not ten (Nov 14 audit)."""
    if not vals:
        return None, None, 0
    by: dict = defaultdict(list)
    for v, e in zip(vals, events):
        by[e].append(v)
    n, g = len(vals), len(by)
    m = st.mean(vals)
    if g < 2:
        return round(m, 2), round(m - 1.645 * (abs(m) + 50), 2), g
    se = (sum((sum(v) - m * len(v)) ** 2 for v in by.values()) * g / (g - 1)) ** 0.5 / n
    return round(m, 2), round(m - 1.645 * se, 2), g


_ROW_CACHE: dict[str, tuple[list[dict], dict]] = {}
KEEP_COPIES = 500           # newest copies per account held in memory for grading (verdicts come at 100-150;
                            # everything stays on disk). Memory grew ~100 MB/day without it (crash Nov 11)
WHALE_MEM_DAYS = 3          # big-bet rows held in memory (anonymous $2,000+ bets alone are ~1,000/hour)


def _slim(r: dict) -> bool:
    """Rows the scoring needs: snapped fresh-start fills, their snapshots and big-bet rows (keeps
    memory small)."""
    k = r.get("kind")
    if k == "fill":
        return r.get("snap", True) and not r.get("backfill") and (r.get("v") or 1) >= ROW_V
    return k in ("snap", "whale")


_INTERN_VALS = ("kind", "nick", "ticker", "role", "side", "src")


def _compact(r: dict) -> dict:
    """The same row with shared key strings (and shared common values). json.loads gives every row
    its own copy of each key, ~0.8 KB a row; interned, ~80k held rows use about half the memory
    (Copycat crash loop Nov 12-13: restarts every ~6 min once the start-up load passed 300 MB)."""
    return {sys.intern(k): (sys.intern(v) if k in _INTERN_VALS and isinstance(v, str) else v)
            for k, v in r.items()}


def _hour_end(p: Path) -> float | None:
    """End of the UTC hour a copycat-YYYY-MM-DD-HH file covers (None if the name doesn't parse)."""
    try:
        h = datetime.strptime(p.name[8:21], "%Y-%m-%d-%H").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return h.timestamp() + 3600


def _read_part(p: Path, prev: dict, whale_cut: float | None = None) -> tuple[list[dict], dict]:
    """One hour's scoring rows, streamed line by line: a busy hour is tens of MB once unzipped, and
    reading whole files pushed the service past its 200 MB cap (crash loop, Nov 11 4:28 AM ET).
    To keep memory small each snapshot is folded into its fill (fill["_s"] = {delay: (bid, ask)});
    snapshots come within ~5 min, so a fill from this hour or the one before is found. Settled
    results become one small {"kind": "result"} row per ticker."""
    part, tids, res = [], {}, {}
    end = _hour_end(p)
    skip_whales = whale_cut is not None and end is not None and end < whale_cut   # too old to hold
    try:
        with (gzip.open(p, "rt", encoding="utf-8") if p.suffix == ".gz"
              else open(p, encoding="utf-8")) as fh:
            for x in fh:
                if '"fill"' in x and ('"snap":false' in x or '"snap": false' in x):
                    continue                        # unsnapped fills are never scored: skip the parse
                if skip_whales and '"whale"' in x:
                    continue                        # big-bet rows older than WHALE_MEM_DAYS: never held
                try:
                    r = json.loads(x)
                except ValueError:
                    continue
                if not _slim(r):
                    continue
                if r["kind"] == "snap":
                    if r.get("result") in ("yes", "no") and r.get("ticker"):
                        res[r["ticker"]] = r["result"]
                    f = tids.get(r.get("trade_id")) or prev.get(r.get("trade_id"))
                    if f is not None:
                        f.setdefault("_s", {})[r.get("delay")] = (r.get("bid"), r.get("ask"))
                    continue
                r = _compact(r)
                if r["kind"] == "fill":
                    for k in ("trade_time", "snap", "dropped"):
                        r.pop(k, None)
                    tids[r.get("trade_id")] = r
                elif r["kind"] == "whale":
                    for k in ("trade_id", "trade_time", "age_d", "their_pnl", "fills"):
                        r.pop(k, None)
                part.append(r)
    except (OSError, EOFError) as exc:              # a torn gzip tail (killed mid-write) keeps what parsed
        log.warning("copycat rows %s: %s", p.name, exc)
    part += [{"kind": "result", "ticker": t, "result": v} for t, v in res.items()]
    return part, tids


def load_rows(d: Path, cache_done_hours: bool = False, now: float | None = None) -> list[dict]:
    rows = []
    cur = (datetime.fromtimestamp(now, timezone.utc).strftime("copycat-%Y-%m-%d-%H.jsonl")
           if now else None)
    prev: dict = {}
    for p in sorted(d.glob("copycat-*.jsonl*")):
        if cache_done_hours and p.name in _ROW_CACHE:
            part, prev = _ROW_CACHE[p.name]
            rows += part
            continue
        part, tids = _read_part(p, prev, whale_cut=now - WHALE_MEM_DAYS * 86400 if now else None)
        if cache_done_hours and p.name != cur:
            _ROW_CACHE[p.name] = (part, tids)
        rows += part
        prev = tids
    if cache_done_hours and _trim_cache(rows, now):
        return load_rows(d, cache_done_hours, now)
    return rows


def _trim_cache(rows: list[dict], now: float | None) -> bool:
    """Drops from the cached hours each account's fills beyond its newest KEEP_COPIES and big-bet
    rows older than WHALE_MEM_DAYS. True if anything was dropped (the caller reloads from cache)."""
    per: dict = defaultdict(int)
    gone: set = set()
    for r in reversed(rows):
        if r.get("kind") == "fill":
            per[r.get("nick")] += 1
            if per[r.get("nick")] > KEEP_COPIES:
                gone.add(id(r))
        elif r.get("kind") == "whale" and now and (r.get("ts") or now) < now - WHALE_MEM_DAYS * 86400:
            gone.add(id(r))
    if not gone:
        return False
    hit = False
    for name, (part, tids) in list(_ROW_CACHE.items()):
        keep = [r for r in part if id(r) not in gone]
        if len(keep) != len(part):
            hit = True
            _ROW_CACHE[name] = (keep, {k: v for k, v in tids.items() if id(v) not in gone})
    return hit


LETTERS = ((97, "A+"), (93, "A"), (90, "A-"), (87, "B+"), (83, "B"), (80, "B-"), (77, "C+"),
           (73, "C"), (70, "C-"), (67, "D+"), (63, "D"), (60, "D-"))
COPY_SCORE = 80.0           # B- or better is copied


MIN_EVENTS = 30             # an account needs settled copies in 30+ different events (games) to go real


def score_grade(settle: list[float], events: list[str] | None = None) -> tuple[float | None, str]:
    """A test-style score (0-100) and letter from the whole copy history (cents per contract after
    fees). It is built on the pessimistic mean, so a short lucky run can't score high:
    80 + 2.5 x (lower 95% bound of the mean, in cents), so 0c = 80 (B-), +4c = 90 (A-), +7c = 97.5 (A+),
    -4c = 70 (C-), below -8c = F. A small recency term (+/-3) rewards a hot last 50 or marks a cold one."""
    if len(settle) < MIN_SETTLED:
        return None, "-"
    m, low = mean_low(settle) if events is None else mean_low_events(settle, events)[:2]
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


CAT_MIN = 15                # category grades (Nov 14): a passing account is not copied for real in a
                            # category where its 15+ settled paper copies there are clearly negative (even the
                            # optimistic 95% bound < 0). Unlocking categories for non-passing accounts tested noisy
                            # out of sample on 3.7 days of data (sign flipped between splits): revisit ~Nov 28.
REAL_MIN = 20               # real-money check: after 20 settled real copies, back to paper if real trails paper
REAL_GAP_C = 3.0            # (same trades) by more than 3c/contract, or real is clearly negative


def cat_grades(trades: list[dict], cats: dict | None) -> dict:
    """Per Kalshi category: settled paper copies, c/contract, grade, and whether it is blocked."""
    by: dict = defaultdict(list)
    for t in trades:
        if t.get("c") is not None:
            by[(cats or {}).get((t.get("ticker") or "").split("-")[0]) or "unknown"].append(t["c"])
    out = {}
    for k, v in by.items():
        m, lo = mean_low(v)
        hi = m + (m - lo) if len(v) > 1 else None
        sc, gl = score_grade(v)
        out[k] = {"n": len(v), "c": m, "low": lo, "grade": gl, "score": sc,
                  "blocked": len(v) >= CAT_MIN and hi is not None and hi < 0}
    return out


CAT_DETAIL_MIN = 20         # dashboard Categories tab (Nov 14): full per-category breakdown only for accounts
                            # with 20+ copies (keeps status.json small; smaller accounts show grades only)
BANDS = (("5-25c", 5, 25), ("25-50c", 25, 50), ("50-75c", 50, 75), ("75-95c", 75, 96))


def cat_breakdown(trades: list[dict], cats: dict | None, grades: dict) -> dict:
    """Adds to each category's grade: copies (settled or not), won/lost, price bands and series
    (each [copies, settled, avg c]) and the 3 newest copies. Categories with no settled copy yet get a row too."""
    by: dict = defaultdict(list)
    for t in trades:
        by[(cats or {}).get((t.get("ticker") or "").split("-")[0]) or "unknown"].append(t)
    out = {k: dict(v) for k, v in grades.items()}
    for k, tr in by.items():
        g = out.setdefault(k, {"n": 0, "c": None, "low": None, "grade": "-", "score": None, "blocked": False})
        done = [t for t in tr if t.get("c") is not None]
        g.update(copies=len(tr), won=sum(1 for t in done if t["c"] > 0), lost=sum(1 for t in done if t["c"] <= 0))
        bands, ser = {}, defaultdict(list)
        for lbl, lo, hi in BANDS:
            b = [t for t in tr if lo <= (t.get("pay_c") or 0) < hi]
            d = [t["c"] for t in b if t.get("c") is not None]
            if b:
                bands[lbl] = [len(b), len(d), round(st.mean(d), 1) if d else None]
        for t in tr:
            ser[(t.get("ticker") or "").split("-")[0]].append(t)
        g["bands"] = bands
        g["series"] = [[s_, len(v), len(d), round(st.mean(d), 1) if d else None]
                       for s_, v in sorted(ser.items(), key=lambda kv: -len(kv[1]))[:5]
                       for d in [[t["c"] for t in v if t.get("c") is not None]]]
        g["all_series"] = sorted(ser)
        g["latest"] = [{"et": t.get("et"), "ticker": t.get("ticker"), "side": t.get("side"),
                        "pay_c": t.get("pay_c"), "c": t.get("c")} for t in tr[-3:][::-1]]
    return out


def build_status(rows: list[dict], results: dict[str, str], watch: dict, now: float,
                 account_stops: dict | None = None, notes: dict | None = None, cats: dict | None = None,
                 keep: set | None = None) -> dict:
    g = grade(rows, results)
    accounts = []
    paper_c = {}
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
        ev_all = o.get("settle_ev", [])[-GO_LIVE_SLOW_FILLS:]
        score, gl = score_grade(settle_all[-GO_LIVE_SLOW_FILLS:], ev_all)   # rolling: the last 150 copies
        n_ev = len(set(ev_all))
        kept = nick in (keep or ())               # Alex's hand-picked real test (copycat_control.json "keep")
        due = (n >= GO_LIVE_SLOW_FILLS or (n >= GO_LIVE_FILLS and (score or 0) >= GO_LIVE_FAST_SCORE)) \
            and len(settle_all) >= GO_LIVE_SETTLED
        banked = sum(settle_all) / 100            # $ a 1-contract copy would have made, all-time
        leeway = leeway_c(banked, score)
        copyable = score is not None and score >= COPY_SCORE and n_ev >= MIN_EVENTS
        rmean = st.mean(window) if window else None
        cg = cat_grades(o.get("trades") or [], cats)
        if n >= CAT_DETAIL_MIN:
            cg = cat_breakdown(o.get("trades") or [], cats, cg)
        paper_c.update({t["tid"]: t["c"] for t in o.get("trades") or [] if t.get("c") is not None})
        if (account_stops or {}).get(nick) and (due or kept):
            verdict = "benched"                   # its own real-money loss stop, or the boss
        elif kept:
            verdict = "passes"                    # stays on real money whatever its score, inside its $ limit
        elif not due:
            verdict = "watching"
        elif copyable and rmean is not None and rmean >= -leeway:
            verdict = "passes"
        elif copyable:
            verdict = "slump"                     # good record, recent run below its leeway
        elif score is not None and score >= COPY_SCORE:
            verdict = "watching"                  # good score but too few different events yet: not a fail
        else:
            verdict = "fails"
        accounts.append({"nick": nick, "watched": nick in watch, **(watch.get(nick) or {}),
                         "fills": n, "settled": len(settle_all), "window": len(window), "days": round(days, 1),
                         "lag_s": round(st.median(o["lag"]), 1) if n else None,
                         "follow_5m_c": f300, "follow_30m_c": f1800,
                         "settle_c": settle, "settle_low_c": settle_low, "verdict": verdict,
                         "grade": gl, "score": score, "events": n_ev, "kept": kept, "banked": round(banked, 2), "leeway_c": round(leeway, 1),
                         "size": (1 if kept and not copyable else size_for(score, len(settle_all)))
                         if verdict == "passes" else 0,
                         "boss_note": (notes or {}).get(nick), "detail": detail(o) if n else None,
                         "cat_grades": cg, "blocked_cats": sorted(k for k, v in cg.items() if v["blocked"])})
    recent = [r for r in rows if r.get("kind") == "fill" and not r.get("backfill")][-40:]
    return {"generated": datetime.fromtimestamp(now, ET).strftime("%Y-%m-%d %H:%M ET"),
            "mode": "watch-only", "rule": f"judged at {GO_LIVE_FILLS} paper copies if it scores {GO_LIVE_FAST_SCORE:.0f}+ (B+), otherwise at {GO_LIVE_SLOW_FILLS}, and only with {GO_LIVE_SETTLED}+ settled in {MIN_EVENTS}+ different events. Score 0-100 from its last 150 settled copies "
            "that real money could have made (within 2c of their price, 5-95c), counting copies in one game together "
            "(after fees; 80 = break-even even in the pessimistic case). Copied at 80+ (B- or better) while its "
            f"last {ROLL_WINDOW} copies average better than -leeway (5c, +1c per $1 it has made us, +up to 5c "
            "for a high score); benched below that, back when it recovers. Size: 90+ = 2, 95+ (300 copies) = 3",
            "accounts": accounts, "_paper_c": paper_c,
            "recent": [{k: r.get(k) for k in ("et", "nick", "ticker", "side", "yes_price", "count",
                                                  "role", "lag_s")} for r in reversed(recent)]}


def add_real(status: dict, state: dict | None) -> None:
    """Puts each account's REAL-money record next to its paper (shadow) record, and measures what
    paper still misses (IOC misses, our 2c limit, the ~16 s delay, thin books): real c/contract minus
    paper c/contract on the same trades. After REAL_MIN settled real copies an account goes back to
    paper ("real-lag") if real trails paper by more than REAL_GAP_C, or real is clearly negative."""
    pos = (state or {}).get("positions") or {}
    paper = status.pop("_paper_c", None) or {}
    for a in status["accounts"]:
        mine = [p for p in pos.values() if p.get("nick") == a["nick"]]
        done = [p for p in mine if p.get("settled")]
        a["real"] = {"copies": len(mine), "open": len(mine) - len(done), "settled": len(done),
                     "won": sum(1 for p in done if (p.get("pnl") or 0) > 0),
                     "contracts": round(sum(p.get("n") or 0 for p in mine), 2),
                     "open_cost": round(sum(p.get("cost") or 0 for p in mine if not p.get("settled")), 2),
                     "pnl": round(((state or {}).get("pnl") or {}).get(a["nick"], 0.0), 2)}
        real_c = [100 * (p.get("pnl") or 0) / p["n"] for p in done if p.get("n")]
        same = [(100 * (p.get("pnl") or 0) / p["n"], paper[p["trade_id"]]) for p in done
                if p.get("n") and p.get("trade_id") in paper]
        m, lo = mean_low(real_c)
        gap = round(st.mean(r - q for r, q in same), 2) if same else None
        a["real"].update(c=m, gap_c=gap, gap_n=len(same))
        if len(real_c) >= REAL_MIN and a["verdict"] == "passes" and not a.get("kept"):
            hi = m + (m - lo)
            if (gap is not None and gap < -REAL_GAP_C) or hi < 0:
                a["verdict"], a["size"] = "real-lag", 0
                a["real"]["why"] = (f"real trails paper by {-gap:.1f}c/contract" if gap is not None and gap < -REAL_GAP_C
                                    else f"real money clearly losing ({m:+.1f}c/contract over {len(real_c)})")


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
        self.based: set[str] = set()              # accounts whose trade history a first poll marked as backfill
        self.last_snap: dict[tuple[str, str], float] = {}
        self.snaps: list[tuple[float, int, str, str, str]] = []  # (due, delay, nick, ticker, trade_id)
        self.watch_next = 0.0
        self.rr = 0
        self.uploaded: set[str] = set()
        self.results: dict[str, str] = {}
        self.res_asked: dict[str, float] = {}    # ticker -> when its result was last asked for
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
        self.cats_next = 0.0                     # next full refresh of the series -> category map
        self.lag_told: set[str] = set()           # accounts already pushed as "back to paper" (real-lag)
        self.feed_seen: set[str] = set()         # trade ids already read from the global feed
        self.feed_top: str | None = None         # newest trade id of the feed snapshot last read
        self.feed_fh = None                      # open gzip file for the rolling full-feed log
        self.feed_hour = ""
        self.feed_off = False
        self.profiles: dict[str, dict] = {}      # nick -> {"joined", "markets", "new"} for the big-bet scanner
        self.whale_queue: list[tuple[str, dict, float]] = []   # (nick, trade, seen) awaiting a profile lookup
        self.feed_next = 0.0
        self.scout_queue: dict[str, None] = {}    # names seen in the feed, not looked up yet
        self.feed_names: set[str] = set()         # every public trader seen in the feed
        self.feed_lags: list[float] = []          # seconds from trade to its first sight in the feed
        self.dropped: dict[str, dict] = {}        # nick -> {"at", "reason", ...}; not watched for DROP_DAYS
        try:                                      # restart fast: reuse the saved watch list
            w = json.loads((self.out / "watch.json").read_text())
            self.watch = w.get("watch") or {}
            self.watch_next = (w.get("at") or 0) + WATCH_REFRESH_S
            self.dropped = w.get("dropped") or {}
        except (OSError, ValueError):
            pass
        self.logged_before = set(self.watch)     # their history is already on disk: a restart doesn't re-log it
        self._boss_busy = False
        self._in_status = False
        self.last_up = 0.0
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
    def get(self, url: str, _gap: float | None = None, **params):
        r = self.s.get(url, params=params, timeout=20)
        gap = self.req_gap if _gap is None or not self.req_gap else _gap
        if gap:
            time.sleep(gap)
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
            if self.is_dropped(n):
                continue
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
                keep[n] = {"pnl": round(pnl), "since": round(self.clock()), "checked": self.clock(),
                           "contracts": m.get("volume"),
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
            (self.out / "watch.json").write_text(json.dumps({"at": self.clock(), "watch": self.watch,
                                                             "dropped": self.dropped}))
        except OSError as exc:
            log.warning("copycat watch save: %s", exc)

    def is_dropped(self, n: str, now: float | None = None) -> bool:
        d = self.dropped.get(n)
        if not d:
            return False
        if (now or self.clock()) - (d.get("at") or 0) < DROP_DAYS * 86400:
            return True
        self.dropped.pop(n, None)                 # time served: may be scouted again, from zero
        self.checked.discard(n)
        return False

    def judge(self, a: dict) -> None:
        """Paper test with chances: a fail at 150 copies (then at each further RETEST_EVERY) uses one
        of TEST_CHANCES; a pass resets them; dropped for DROP_DAYS only when all are used up."""
        v = self.watch.get(a["nick"])
        if v is None:
            return
        due_at = v.get("retest_at") or GO_LIVE_SLOW_FILLS
        if a["verdict"] == "passes" and v.get("fails_in_row"):
            v["fails_in_row"], v["retest_at"] = 0, None
        elif a["verdict"] == "fails" and a["fills"] >= due_at:
            v["fails_in_row"] = (v.get("fails_in_row") or 0) + 1
            if v["fails_in_row"] >= TEST_CHANCES:
                self.drop(a["nick"], f"failed its paper test {TEST_CHANCES} times", fills=a["fills"],
                          score=a["score"], grade=a["grade"], real_pnl=(a.get("real") or {}).get("pnl"))
                return
            v["retest_at"] = a["fills"] + RETEST_EVERY
            self.write({"kind": "strike", "nick": a["nick"], "fills": a["fills"], "score": a["score"],
                        "fails_in_row": v["fails_in_row"], "retest_at": v["retest_at"]})
            self.save_watch()
        a["chances_left"] = TEST_CHANCES - (v.get("fails_in_row") or 0)
        a["retest_at"] = v.get("retest_at")

    def drop(self, n: str, reason: str, **extra) -> None:
        """Stops watching an account (no polls, snapshots or copies) for DROP_DAYS."""
        self.watch.pop(n, None)
        self.hot.discard(n)
        self.seen.pop(n, None)
        self.based.discard(n)
        self.snaps = [x for x in self.snaps if x[2] != n]
        self.dropped[n] = {"at": round(self.clock()), "reason": reason, **extra}
        self.write({"kind": "drop", "nick": n, "reason": reason, **extra})
        log.info("copycat dropped %s: %s", n, reason)

    def recheck(self) -> None:
        """Weekly look at each watched account's own Kalshi profit; drops long-run losers."""
        now = self.clock()
        old = sorted((n for n, v in self.watch.items() if now - (v.get("checked") or v.get("since") or 0) >= RECHECK_S),
                     key=lambda n: self.watch[n].get("checked") or 0)
        for n in old[:RECHECK_PER_SCOUT]:
            self.tick()
            v = self.watch.get(n)
            if v is None:                         # dropped by the status update meanwhile
                continue
            try:
                m = self.get(f"{SOCIAL}/profile/metrics", nickname=n).get("metrics") or {}
                m30 = self.get(f"{SOCIAL}/profile/metrics", nickname=n, since_day_before=30).get("metrics") or {}
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat recheck %s: %s", n, exc)
                v["checked"] = now
                continue
            pnl, pnl30 = (m.get("pnl") or 0) / 1e4, (m30.get("pnl") or 0) / 1e4
            v.update(pnl=round(pnl), pnl_30d=round(pnl30), checked=now)
            v["strikes"] = (v.get("strikes") or 0) + 1 if pnl30 < 0 else 0
            if v["strikes"] >= LOSER_STRIKES or (pnl < MIN_PNL and pnl30 < MIN_PNL_30D):
                self.drop(n, "long-run loser" if v["strikes"] >= LOSER_STRIKES else "no longer profitable",
                          pnl=round(pnl), pnl_30d=round(pnl30))
        if old:
            self.save_watch()

    def check_name(self, n: str) -> dict | None:
        m = self.get(f"{SOCIAL}/profile/metrics", nickname=n).get("metrics") or {}
        pnl = (m.get("pnl") or 0) / 1e4
        pnl30 = None
        if pnl < MIN_PNL:
            m30 = self.get(f"{SOCIAL}/profile/metrics", nickname=n, since_day_before=30).get("metrics") or {}
            pnl30 = (m30.get("pnl") or 0) / 1e4
            if pnl30 < MIN_PNL_30D:
                return None
        p = (self.get(f"{SOCIAL}/profile", nickname=n).get("social_profile") or {})
        return {"pnl": round(pnl), "pnl_30d": round(pnl30) if pnl30 is not None else None,
                "contracts": m.get("volume"), "markets": m.get("num_markets_traded"),
                "cats": p.get("top_categories"), "joined": p.get("joined_at")}

    def scout(self) -> None:
        """Reads recent pages of the global public trade feed and adds new profitable names."""
        now = self.clock()
        names: dict[str, None] = {n: None for n in self.scout_queue
                                  if n not in self.checked and not self.is_dropped(n, now)}
        self.scout_queue = {}
        cursor = None
        for _ in range(SCOUT_PAGES if len(names) < SCOUT_CHECKS else 0):
            params = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            j = self.get(f"{SOCIAL}/trades", **params)
            for t in j.get("trades") or []:
                for k in ("maker_nickname", "taker_nickname"):
                    if (t.get(k) and t[k] not in self.checked and t[k] not in self.watch
                            and not self.is_dropped(t[k], now)):
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
            self.watch[n] = dict(info, src="scout", since=round(self.clock()), checked=self.clock())
            added.append(n)
        if added:
            self.save_watch()
            self.write({"kind": "scout", "added": added, "looked_up": min(len(names), SCOUT_CHECKS)})
        self.recheck()

    def snapshot(self, ticker: str) -> dict:
        m = self.get(f"{KALSHI}/markets/{ticker}").get("market") or {}
        return {"bid": _px(m.get("yes_bid_dollars", m.get("yes_bid"))),
                "ask": _px(m.get("yes_ask_dollars", m.get("yes_ask"))),
                "last": _px(m.get("last_price_dollars", m.get("last_price"))),
                "status": m.get("status"), "result": m.get("result") or None}

    def fetch_results(self, want: set[str], now: float) -> None:
        """Looks up settlement results in batches of RESULT_BATCH markets per call, least recently
        checked first. (Until Nov 14 this asked for 40 markets per status update, one call each, in
        alphabetical order, and still-open markets were asked again every time: most settled copies
        never got a result, so scores were built on a small, lopsided part of each account's record.)"""
        self.res_asked = {t: v for t, v in self.res_asked.items() if t in want}
        todo = sorted(want, key=lambda t: (self.res_asked.get(t, 0.0), t))[:RESULT_BATCH * RESULT_CALLS]
        for i in range(0, len(todo), RESULT_BATCH):
            batch = todo[i:i + RESULT_BATCH]
            self.tick()
            try:
                j = self.get(f"{KALSHI}/markets", tickers=",".join(batch), limit=1000)
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat results: %s", exc)
                return
            for t in batch:
                self.res_asked[t] = now
            for m in j.get("markets") or []:
                if m.get("result") in ("yes", "no") and m.get("ticker"):
                    self.results[m["ticker"]] = m["result"]

    # -- one account
    def poll(self, nick: str, now: float) -> None:
        trades = self.get(f"{SOCIAL}/trades", nickname=nick, limit=50).get("trades") or []
        self.ingest(nick, trades, now)
        self.based.add(nick)
        if len(self.seen[nick]) > 5000:            # keep memory bounded
            self.seen[nick] = {t.get("trade_id") for t in trades}

    def tick(self) -> None:
        """Keeps the fast feed and the 5-minute status going during long jobs (watch-list rebuild,
        scouting, snapshot backlogs)."""
        now = self.clock()
        if self.uploader and not self._in_status and now - self.last_up > STATUS_EVERY_S:
            self._in_status = True
            try:
                self.last_up = now
                self.upload_done_hours(now)
                self.update_status(now)
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat status: %s", exc)
            finally:
                self._in_status = False
        if now >= self.feed_next:
            self.feed_next = now + FEED_EVERY_S
            try:
                self.poll_feed(now)
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat feed: %s", exc)

    def poll_feed(self, now: float) -> None:
        """One read of Kalshi's global public trade feed covers every watched account at once
        (newest first, 100 per page; reads up to FEED_PAGES pages back to the last trade seen).
        Fresh trades count from the first sight; an account's own first poll marks its older history as backfill."""
        new, cursor, boot = [], None, not self.feed_seen
        for i in range(FEED_PAGES):
            params = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            j = self.get(f"{SOCIAL}/trades", _gap=FEED_GAP_S, **params)
            page = j.get("trades") or []
            if i == 0:
                top = page[0].get("trade_id") if page else None
                if top == self.feed_top:
                    return                          # snapshot not refreshed yet
                self.feed_top = top
            fresh = [t for t in page if t.get("trade_id") and t["trade_id"] not in self.feed_seen]
            new += fresh
            cursor = j.get("cursor")
            if boot or len(fresh) < len(page) or not cursor:
                break
        self.feed_seen.update(t["trade_id"] for t in new)
        if len(self.feed_seen) > 200_000:
            self.feed_seen = {t["trade_id"] for t in new}
        if boot:
            return                                  # first read after a start: may be minutes old
        now = self.clock()
        try:
            self.log_feed(new, now)
        except Exception as exc:  # noqa: BLE001
            log.warning("copycat feed log: %s", exc)
        for o in orders(new):                    # a big order fills against many makers: judge whole orders
            self.spot_whale(o, now)
        by: dict[str, list] = defaultdict(list)
        for t in new:
            for k in ("taker_nickname", "maker_nickname"):
                n = t.get(k)
                if n and len(self.feed_names) < 500_000:
                    self.feed_names.add(n)
                if (n and n not in self.watch and n not in self.checked and n not in self.dropped
                        and len(self.scout_queue) < 2000):
                    self.scout_queue[n] = None        # looked up by the next scout
                if n and (n in self.watch or n in self.dropped):   # live from the first second, even
                                                                    # before the account's own first poll
                    by[n].append(t)
        for t in new:                             # how late Kalshi publishes trades to the feed
            try:
                ct = datetime.fromisoformat(str(t.get("create_date")).replace("Z", "+00:00")).timestamp()
                self.feed_lags.append(round(now - ct, 1))
            except ValueError:
                pass
        self.feed_lags = self.feed_lags[-5000:]
        for n, ts in by.items():
            self.ingest(n, ts, now, quiet=n in self.dropped, src="feed")

    def log_feed(self, trades: list[dict], now: float) -> None:
        """Rolling raw log of every public trade: feed/feed-YYYY-MM-DD-HH.csv.gz on the server only
        (too big for the data branch). Columns: trade time, ticker, yes price, contracts, taker side,
        taker action, taker name, maker name, trade id, seen-at."""
        if self.feed_off or not trades:
            return
        d = self.out / "feed"
        hour = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d-%H")
        if hour != self.feed_hour:
            if self.feed_fh:
                self.feed_fh.close()
            d.mkdir(exist_ok=True)
            self.prune_feed(d, now)
            if self.feed_off:
                self.feed_fh = None
                return
            self.feed_fh = gzip.open(d / f"feed-{hour}.csv.gz", "at", encoding="utf-8")
            self.feed_hour = hour
        seen = round(now, 1)
        for t in reversed(trades):
            self.feed_fh.write(",".join(str(t.get(k) or "").replace(",", " ") for k in
                                        ("create_date", "ticker", "price_dollars", "count_fp", "taker_side",
                                         "taker_action", "taker_nickname", "maker_nickname", "trade_id"))
                               + f",{seen}\n")

    def prune_feed(self, d: Path, now: float) -> None:
        import shutil
        files = sorted(d.glob("feed-*.csv.gz"))
        for p in files:
            if now - p.stat().st_mtime > FEED_KEEP_DAYS * 86400:
                p.unlink(missing_ok=True)
        files = sorted(d.glob("feed-*.csv.gz"))
        while files and shutil.disk_usage(d).free / 1e9 < FEED_MIN_FREE_GB:
            files.pop(0).unlink(missing_ok=True)
        if shutil.disk_usage(d).free / 1e9 < FEED_MIN_FREE_GB:
            if not self.feed_off:
                log.warning("copycat feed log off: disk under %s GB free", FEED_MIN_FREE_GB)
            self.feed_off = True

    def spot_whale(self, t: dict, now: float) -> None:
        """Queues a named trade of WHALE_USD+ (each named side) for the big-bet scanner."""
        px, n = _px(t.get("price_dollars")), t.get("count_fp", t.get("count"))
        try:
            n = float(n)
        except (TypeError, ValueError):
            return
        if px is None:
            return
        if not t.get("taker_nickname") and not t.get("maker_nickname"):
            ts, act = t.get("taker_side"), t.get("taker_action", "buy")
            if ts in ("yes", "no"):
                side = ts if act == "buy" else ("no" if ts == "yes" else "yes")
                paid = px if side == "yes" else 1 - px
                if n * paid >= WHALE_ANON_USD:
                    try:
                        ct = datetime.fromisoformat(str(t.get("create_date")).replace("Z", "+00:00")).timestamp()
                        lag = round(now - ct, 1)
                    except ValueError:
                        lag = None
                    self.write({"kind": "whale", "nick": None, "anon": True, "trade_id": t.get("trade_id"),
                                "ticker": t.get("ticker"), "role": "taker", "side": side, "paid": round(paid, 4),
                                "count": n, "usd": round(n * paid), "trade_time": t.get("create_date"),
                                "lag_s": lag, "fills": t.get("fills"), "new": False})
            return
        for k in ("taker_nickname", "maker_nickname"):
            nick = t.get(k)
            if not nick:
                continue
            role, side = sharp_side(t, nick)
            if not side:
                continue
            paid = px if side == "yes" else 1 - px
            if n * paid >= WHALE_USD and len(self.whale_queue) < 500:
                self.whale_queue.append((nick, t, now))

    def run_whales(self) -> None:
        """Looks up queued big bettors (cached) and logs each bet with a NEW flag. Paper only."""
        looked = 0
        while self.whale_queue and looked < WHALE_CHECKS:
            nick, t, seen = self.whale_queue.pop(0)
            p = self.profiles.get(nick)
            if p is None:
                looked += 1
                self.tick()
                try:
                    prof = self.get(f"{SOCIAL}/profile", nickname=nick).get("social_profile") or {}
                    m = self.get(f"{SOCIAL}/profile/metrics", nickname=nick).get("metrics") or {}
                except Exception as exc:  # noqa: BLE001
                    log.warning("copycat whale %s: %s", nick, exc)
                    continue
                joined = prof.get("joined_at")
                try:
                    age = (seen - datetime.fromisoformat(str(joined).replace("Z", "+00:00")).timestamp()) / 86400
                except ValueError:
                    age = None
                mk = m.get("num_markets_traded")
                p = {"joined": joined, "age_d": round(age, 1) if age is not None else None, "markets": mk,
                     "pnl": round((m.get("pnl") or 0) / 1e4),
                     "new": bool((age is not None and age <= WHALE_NEW_DAYS)
                                 or (mk is not None and mk <= WHALE_FEW_MARKETS))}
                if len(self.profiles) < 50_000:
                    self.profiles[nick] = p
            role, side = sharp_side(t, nick)
            px, n = _px(t.get("price_dollars")), float(t.get("count_fp", t.get("count")) or 0)
            paid = px if side == "yes" else 1 - px
            try:
                lag = round(seen - datetime.fromisoformat(str(t.get("create_date")).replace("Z", "+00:00")).timestamp(), 1)
            except ValueError:
                lag = None
            ours = None                         # NEW accounts: the price we could really have bought at
            if p["new"]:
                try:
                    sn = self.snapshot(t.get("ticker") or "")
                    b, a = sn.get("bid"), sn.get("ask")
                    ours = a if side == "yes" else (1 - b if b is not None else None)
                    ours = round(ours, 4) if ours is not None and 0 < ours < 1 else None
                except Exception as exc:  # noqa: BLE001
                    log.warning("copycat whale price %s: %s", t.get("ticker"), exc)
            self.write({"kind": "whale", "nick": nick, "trade_id": t.get("trade_id"), "ticker": t.get("ticker"),
                        "ours": ours, "ours_lag_s": round(self.clock() - seen + (lag or 0), 1) if ours else None,
                        "role": role, "side": side, "paid": round(paid, 4), "count": n, "usd": round(n * paid),
                        "trade_time": t.get("create_date"), "lag_s": lag, "fills": t.get("fills"), "joined": p["joined"],
                        "age_d": p["age_d"], "markets": p["markets"], "their_pnl": p["pnl"], "new": p["new"]})

    def ingest(self, nick: str, trades: list[dict], now: float, quiet: bool = False, src: str = "poll") -> None:
        """Logs an account's new trades (newest first, as the API sends them). quiet = a dropped
        account: logged for research (e.g. how market makers trade), never snapshotted or copied."""
        first = src == "poll" and nick not in self.based and not quiet   # its history = backfill
        seen = self.seen.setdefault(nick, set())
        if first and nick in self.logged_before:  # after a restart: remember, don't re-log ~50 old fills
            seen.update(t.get("trade_id") for t in trades if t.get("trade_id"))
            return
        for t in reversed(trades):
            tid = t.get("trade_id")
            if not tid or tid in seen:
                continue
            seen.add(tid)
            role, side = sharp_side(t, nick)
            row = {"kind": "fill", "v": ROW_V, "nick": nick, "trade_id": tid, "ticker": t.get("ticker"),
                   "role": role, "side": side, "yes_price": _px(t.get("price_dollars")),
                   "count": t.get("count_fp", t.get("count")), "trade_time": t.get("create_date"),
                   "backfill": first, "src": src, "act": t.get("taker_action")}   # act: the taker bought or sold
                   # (a sell is usually an exit; copies of sells did ~2c worse in the Nov 14 audit)
            if quiet:
                row["dropped"] = True
            try:
                ct = datetime.fromisoformat(str(t.get("create_date")).replace("Z", "+00:00")).timestamp()
                row["lag_s"] = round(now - ct, 1)
            except ValueError:
                pass
            key = (nick, t.get("ticker") or "")
            row["snap"] = not (first or quiet or now - self.last_snap.get(key, 0) < SNAP_GAP_S
                               or len(self.snaps) > MAX_PENDING)
            self.write(row)
            if not first and not quiet and self.live is not None and nick in self.hot:
                self.try_copy(row, now)
            if not row["snap"]:
                continue
            self.last_snap[key] = now
            for d in SNAP_DELAYS:
                self.snaps.append((now + d, d, nick, key[1], tid))

    def load_cats(self, now: float) -> None:
        """Fill the series -> category map from Kalshi's full series list (one ~20 MB call, daily),
        so per-category grades cover every copied trade, not only the ones we tried to copy live."""
        self.cats_next = now + CATS_RETRY_S
        try:
            r = self.s.get(f"{KALSHI}/series", timeout=90)
            if r.status_code != 200:
                raise RuntimeError(f"{r.status_code} series list")
            got = {x["ticker"]: x.get("category") for x in r.json().get("series") or [] if x.get("ticker")}
            del r
        except Exception as exc:  # noqa: BLE001
            log.warning("copycat series list: %s", exc)
            return
        self.cats.update(got)
        self.cats_next = now + CATS_REFRESH_S
        log.info("copycat categories: %d series", len(got))

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
            acct = self.acct.get(row["nick"]) or {}
            if (snap["category"] or "unknown") in (acct.get("blocked_cats") or []):
                what = f"category blocked: {snap['category']}"
            else:
                what = self.live.maybe_copy(acct or None, row, snap, now)
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
        if now >= self.cats_next:
            self.load_cats(now)
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
        try:
            self.run_whales()
        except Exception as exc:  # noqa: BLE001
            log.warning("copycat whales: %s", exc)
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
        def counts(r: dict) -> bool:
            if r.get("kind") != "fill":
                return True
            n = r.get("nick")
            if (r.get("v") or 1) < ROW_V or n in self.dropped:                # fresh start
                return False
            return (r.get("ts") or now) >= ((self.watch.get(n) or {}).get("since") or 0)
        rows = [r for r in load_rows(self.out, cache_done_hours=True, now=now) if counts(r)]
        old = now - 900
        held = [t for t, p in (self.live.state["positions"].items() if self.live else [])
                if not p.get("settled") and t not in self.results]
        want = ({r["ticker"] for r in rows if r.get("kind") in ("fill", "whale") and not r.get("backfill")
                 and r.get("ticker") and (r.get("ts") or now) < old and r["ticker"] not in self.results}
                - set(held))
        for t in held:                            # markets we hold with real money: always, one by one
            try:
                res = self.snapshot(t).get("result")
                if res in ("yes", "no"):
                    self.results[t] = res
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat result %s: %s", t, exc)
        self.fetch_results(want, now)
        stops, notes, live = {}, {}, None
        if self.live is not None:
            self.live.refresh(now, force=True)
            self.live.settle(self.results, now)
            stops.update(self.live.benched())
            live = self.live.summary(now)
        if self.boss is not None:
            stops.update({k: "boss: " + v["reason"] for k, v in self.boss.benches(now).items()})
            notes = dict(self.boss.state["notes"])
        keep = set((self.live.cfg.get("keep") or []) if self.live is not None else [])
        status = build_status(rows, self.results, self.watch, now, account_stops=stops, notes=notes, cats=self.cats,
                              keep=keep)
        add_real(status, self.live.state if self.live else None)
        for a in status["accounts"]:
            if a["verdict"] == "real-lag" and a["nick"] not in self.lag_told:
                self.lag_told.add(a["nick"])
                self.alert("Copycat: back to paper", f"{a['nick']}: {a['real'].get('why')}. Paper copies continue.")
        for a in status["accounts"]:
            self.judge(a)
        if any(a["nick"] in self.dropped for a in status["accounts"]):
            status["accounts"] = [a for a in status["accounts"] if a["nick"] not in self.dropped]
            self.save_watch()
        status["dropped"] = sorted(({"nick": k, **v, "back": datetime.fromtimestamp(v["at"] + DROP_DAYS * 86400, ET)
                                     .strftime("%b %-d")} for k, v in self.dropped.items()),
                                   key=lambda x: -x["at"])[:200]
        status["live"] = live
        status["whales"] = whale_summary(rows, self.results, now)
        v2 = [r["ts"] for r in rows if r.get("kind") == "fill" and r.get("ts")]
        status["fresh_since"] = datetime.fromtimestamp(min(v2), ET).strftime("%b %-d, %-I:%M %p ET") if v2 else None
        status["scan"] = {"public_traders_seen": len(self.feed_names), "looked_up": len(self.checked),
                          "watched": len(self.watch), "min_profit": MIN_PNL, "max_watch": MAX_WATCH,
                          "dropped": len(self.dropped), "drop_days": DROP_DAYS,
                          "feed_lag_s": _pcts(self.feed_lags),
                          "copy_lag_s": {k: _pcts([r["lag_s"] for r in rows if r.get("kind") == "fill" and r.get("src") == k
                                                   and not r.get("backfill") and r.get("lag_s") is not None][-2000:])
                                         for k in ("feed", "poll")}}
        status["mode"] = live["mode"] if live and live["mode"] == "live" else "paper only"
        if self.boss is not None:
            status["boss"] = {"summary": self.boss.state.get("summary"), "usage": self.boss.state.get("usage"),
                              "reviews_today": self.boss.state.get("deep_n"),
                              "spend": self.boss.budget.summary(), "model": getattr(self.boss, "model", None),
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
        self.write({"kind": "start"})
        while True:
            try:
                self.step()
                if not self.uploader and self.clock() - self.last_up > STATUS_EVERY_S:
                    self.last_up = self.clock()
                    self.update_status(self.last_up)
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
