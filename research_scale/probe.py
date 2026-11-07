"""Capacity probe: how many contracts could each paper strategy fill per week?

Research only. Read-only public Kalshi data; never trades, never alerts.
usage: python research_scale/probe.py OUT_DIR

For markets that closed in the last DAYS days, per strategy group:
  * markets / events per week that pass the strategy's filters
  * "pool" = contracts that makers got on OUR side at OUR price band
    (taker hit a resting order we could have been part of), and the part of
    that pool that traded THROUGH the level within 1h (what a back-of-queue
    order would surely have got)
Plus a live order-book snapshot of open markets: resting depth on our side in
the band (the queue we compete with).
"""
from __future__ import annotations

import json
import random
import statistics as st
import sys
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

K = "https://api.elections.kalshi.com/trade-api/v2"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
OUT.mkdir(parents=True, exist_ok=True)
DAYS = 28
NOW = int(time.time())
LO = NOW - DAYS * 86400
PER_GROUP = 450
S = requests.Session()
S.headers["User-Agent"] = "kalshi-research/0.1"
_lock = threading.Lock()
_last = [0.0]
T0 = time.time()
LOG = []

MACRO = ["KXFED", "KXFEDDECISION", "KXCPI", "KXCPIYOY", "KXCPICORE", "KXPCECORE"]

# group -> (bands, window_s, min_market_volume)
# a band = (taker_side, yes_lo, yes_hi): trades where the TAKER bought taker_side
# at a yes_price in [lo, hi] filled a MAKER on the other side at our price band.
GROUPS = {
    # maker buys NO at 75-90c  <=> taker buys YES at 10-25c
    "mentions": ([("yes", 0.10, 0.25)], 168 * 3600 * 10, 1000),
    # maker buys NO at 71-95c  <=> taker buys YES at 5-29c
    "macro": ([("yes", 0.05, 0.29)], 504 * 3600, 500),
    # maker buys the 85-96c favourite (either side)
    "favorites": ([("no", 0.85, 0.96), ("yes", 0.04, 0.15)], 168 * 3600, 0),
    # day-before weather: any maker fill 12-48h before close (model picks side)
    "weather": ([("yes", 0.02, 0.98), ("no", 0.02, 0.98)], 48 * 3600, 0),
    # hourly temp market making: all volume
    "hourly": ([("yes", 0.01, 0.99), ("no", 0.01, 0.99)], 3600 * 3, 0),
}
WEATHER_SKIP_S = 12 * 3600  # weather: only trades 12-48h before close


def log(*a):
    msg = f"[{time.time() - T0:6.0f}s] " + " ".join(str(x) for x in a)
    print(msg, flush=True)
    LOG.append(msg)


def get(path, params=None):
    for i in range(7):
        with _lock:
            wait = _last[0] + 0.065 - time.time()
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            r = S.get(K + path, params=params, timeout=60)
        except requests.RequestException:
            time.sleep(2 * (i + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (i + 1))
            continue
        if not r.ok:
            if r.status_code != 404:
                log("HTTP", r.status_code, path, params, r.text[:200])
            return None
        return r.json()
    return None


def ts(s):
    from datetime import datetime
    if not s:
        return 0
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def series_in(cat, min_vol=0):
    js = get("/series", {"category": cat, "include_volume": "true"}) or {}
    return [s["ticker"] for s in js.get("series") or []
            if s.get("ticker") and (f(s.get("volume_fp", s.get("volume"))) or 0) >= min_vol]


def markets(series, status):
    out, cur = [], None
    for _ in range(60):
        p = {"series_ticker": series, "limit": 1000, "status": status}
        if status == "settled":
            p.update(min_close_ts=LO, max_close_ts=NOW)
        if cur:
            p["cursor"] = cur
        js = get("/markets", p)
        if not js:
            break
        out += js.get("markets") or []
        cur = js.get("cursor")
        if not cur:
            break
    return out


def vol(m):
    return f(m.get("volume_fp", m.get("volume"))) or 0.0


def trades(tk):
    out, cur = [], None
    for _ in range(25):
        p = {"ticker": tk, "limit": 1000}
        if cur:
            p["cursor"] = cur
        js = get("/markets/trades", p)
        if not js:
            break
        for t in js.get("trades") or []:
            yp, c = f(t.get("yes_price_dollars", t.get("yes_price"))), f(
                t.get("count_fp", t.get("count")))
            if yp is None or c is None:
                continue
            if yp > 1.5:
                yp /= 100
            out.append((ts(t.get("created_time")), round(yp, 2), c, t.get("taker_side")))
        cur = js.get("cursor")
        if not cur:
            break
    out.sort()
    return out


def through_flags(tr, horizon=3600):
    """1 if, within `horizon` after trade j, a trade printed beyond j's price in
    the taker's direction (the level was used up)."""
    n = len(tr)
    res = [0] * n
    dmax, dmin = deque(), deque()
    k = 0
    for j in range(n):
        while dmax and dmax[0] <= j:
            dmax.popleft()
        while dmin and dmin[0] <= j:
            dmin.popleft()
        while k < n and tr[k][0] <= tr[j][0] + horizon:
            if k > j:
                while dmax and tr[dmax[-1]][1] <= tr[k][1]:
                    dmax.pop()
                dmax.append(k)
                while dmin and tr[dmin[-1]][1] >= tr[k][1]:
                    dmin.pop()
                dmin.append(k)
            k += 1
        if tr[j][3] == "yes":
            res[j] = int(bool(dmax) and tr[dmax[0]][1] > tr[j][1])
        else:
            res[j] = int(bool(dmin) and tr[dmin[0]][1] < tr[j][1])
    return res


def pool(group, m, tr):
    bands, window, _ = GROUPS[group]
    close = ts(m.get("close_time"))
    if group == "mentions":
        close = ts(m.get("occurrence_datetime") or "") or close
    thr = through_flags(tr)
    tot = thru = notional = 0.0
    for (t0, yp, c, side), th in zip(tr, thr):
        left = close - t0
        if left > window or (group == "weather" and left < WEATHER_SKIP_S):
            continue
        for bs, lo, hi in bands:
            if side == bs and lo - 1e-9 <= yp <= hi + 1e-9:
                tot += c
                thru += c * th
                notional += c * ((1 - yp) if bs == "yes" else yp)
                break
    return tot, thru, notional


def book_depth(group, tk):
    js = get(f"/markets/{tk}/orderbook") or {}
    ob = js.get("orderbook_fp") or js.get("orderbook") or {}
    def lv(side):
        raw = ob.get(f"{side}_dollars") or ob.get(side) or []
        out = []
        for p, q in raw:
            p = float(p)
            out.append((p / 100 if p > 1.5 else p, float(q)))
        return out
    yes, no = lv("yes"), lv("no")
    bands = GROUPS[group][0]
    # our resting side: maker on the opposite of the taker side, at price 1-yp or yp
    depth = 0.0
    for bs, lo, hi in bands:
        if bs == "yes":   # we rest NO bids at 1-hi .. 1-lo
            depth += sum(q for p, q in no if 1 - hi - 1e-9 <= p <= 1 - lo + 1e-9)
        else:             # we rest YES bids at lo .. hi
            depth += sum(q for p, q in yes if lo - 1e-9 <= p <= hi + 1e-9)
    best = lambda lvls: max((p for p, _ in lvls), default=None)
    return depth, best(yes), best(no)


def main():
    rnd = random.Random(5)
    gser = {
        "mentions": series_in("Mentions"),
        "macro": MACRO,
        "favorites": sum((series_in(c, 1_000_000) for c in ("Elections", "Politics", "Entertainment")), []),
    }
    clim = series_in("Climate and Weather") or series_in("Climate")
    gser["weather"] = [s for s in clim if (s.startswith("KXHIGH") or s.startswith("KXLOW"))
                       and not s.startswith("KXHIGHINFLATION")]
    gser["hourly"] = [s for s in clim if s.startswith("KXTEMP")]
    log({g: len(v) for g, v in gser.items()})

    summary = {"days": DAYS, "generated": NOW, "groups": {}}
    for g, series in gser.items():
        with ThreadPoolExecutor(6) as ex:
            settled = [m for lst in ex.map(lambda s: markets(s, "settled"), series) for m in lst]
        minv = GROUPS[g][2]
        settled = [m for m in settled if vol(m) >= max(minv, 1)]
        if g == "favorites":
            evv = defaultdict(float)
            for m in settled:
                evv[m["event_ticker"]] += vol(m)
            settled = [m for m in settled if evv[m["event_ticker"]] >= 1_000_000]
        events = {m["event_ticker"] for m in settled}
        pick = settled if len(settled) <= PER_GROUP else rnd.sample(settled, PER_GROUP)
        wgt = len(settled) / max(1, len(pick))
        log(g, "settled", len(settled), "events", len(events), "sampled", len(pick))
        with ThreadPoolExecutor(8) as ex:
            trs = list(ex.map(lambda m: trades(m["ticker"]), pick))
        rows = []
        for m, tr in zip(pick, trs):
            p_tot, p_thr, notl = pool(g, m, tr)
            rows.append({"ticker": m["ticker"], "event": m["event_ticker"], "volume": vol(m),
                         "pool": p_tot, "through": p_thr, "notional": notl})
        pools = [r["pool"] for r in rows]
        per_ev = defaultdict(float)
        for r in rows:
            per_ev[r["event"]] += r["pool"]
        # live books
        with ThreadPoolExecutor(6) as ex:
            opened = [m for lst in ex.map(lambda s: markets(s, "open"), series[:400]) for m in lst]
        opened = [m for m in opened if vol(m) >= max(minv, 1)]
        bpick = opened if len(opened) <= 150 else rnd.sample(opened, 150)
        with ThreadPoolExecutor(6) as ex:
            depths = list(ex.map(lambda m: book_depth(g, m["ticker"]), bpick))
        dq = [d[0] for d in depths]
        q = lambda xs, p: (sorted(xs)[int(p * (len(xs) - 1))] if xs else None)
        summary["groups"][g] = {
            "series": len(series), "markets": len(settled), "events": len(events),
            "markets_per_week": round(len(settled) / DAYS * 7, 1),
            "events_per_week": round(len(events) / DAYS * 7, 1),
            "pool_week_total": round(sum(pools) * wgt / DAYS * 7),
            "through_week_total": round(sum(r["through"] for r in rows) * wgt / DAYS * 7),
            "notional_week_total": round(sum(r["notional"] for r in rows) * wgt / DAYS * 7),
            "pool_per_market_p25_med_p75": [q(pools, .25), q(pools, .5), q(pools, .75)],
            "pool_per_market_mean": round(st.mean(pools), 1) if pools else None,
            "pool_per_event_med": q(list(per_ev.values()), .5),
            "open_markets_seen": len(opened),
            "book_depth_ours_p25_med_p75": [q(dq, .25), q(dq, .5), q(dq, .75)],
            "volume_per_market_med": q([r["volume"] for r in rows], .5),
        }
        log(g, json.dumps(summary["groups"][g]))
        with open(OUT / f"{g}_markets.json", "w") as fh:
            json.dump(rows, fh)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    try:
        main()
    finally:
        (OUT / "log.txt").write_text("\n".join(LOG))
