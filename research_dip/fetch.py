"""Panic-dip research pull (read-only public data; never trades, never alerts).

For every candidate market side in research_dip/cands.csv.gz (a side that traded
at 56c+ and later at 10c or less in the coarse bucketed data), pull the FULL
trade tape, find the first trade after a 56c+ print where that side traded at
15c or less ("dip start"), and save:
  events.csv.gz   one row per side: high/dip times, tape size, truncation flag
  trades.csv.gz   every trade from the dip window (3h before dip start .. 3 days after)
  candles.csv.gz  1-minute yes bid/ask candles over the same window (real quotes)

usage: python research_dip/fetch.py <shard> <n_shards> <out dir>
"""
from __future__ import annotations

import csv
import gzip
import os
import sys
import threading
import time
import zlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import requests

K = "https://api.elections.kalshi.com/trade-api/v2"
HIGH, DIP = 0.56, 0.15
PRE, POST = 3 * 3600, 3 * 86400
MAX_PAGES = 40
CHUNK_MIN = 2000
BUDGET_S = float(os.environ.get("BUDGET_H", "4.5")) * 3600
S = requests.Session()
S.headers["User-Agent"] = "kalshi-research/0.1"
_lock = threading.Lock()
_last = [0.0]
T0 = time.time()
LOG: list[str] = []


def log(*a):
    msg = f"[{time.time() - T0:6.0f}s] " + " ".join(str(x) for x in a)
    print(msg, flush=True)
    LOG.append(msg)


def get(path, params=None):
    for i in range(8):
        with _lock:
            wait = _last[0] + 0.07 - time.time()
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
                log("HTTP", r.status_code, path, str(params)[:120], r.text[:160])
            return None
        return r.json()
    return None


def ts(s):
    if not s:
        return 0
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def tape(ticker, hist):
    """All trades (oldest first) as (t, yes_price, count, taker_side), truncated flag."""
    out, cur, trunc = [], None, False
    path = "/historical/trades" if hist else "/markets/trades"
    for i in range(MAX_PAGES):
        p = {"ticker": ticker, "limit": 1000}
        if cur:
            p["cursor"] = cur
        js = get(path, p)
        if not js and i == 0:
            path = "/markets/trades" if hist else "/historical/trades"
            js = get(path, p)
        if not js:
            break
        for t in js.get("trades") or []:
            yp, c = f(t.get("yes_price_dollars", t.get("yes_price"))), f(t.get("count_fp", t.get("count")))
            if yp is None or c is None:
                continue
            if yp > 1.5:
                yp /= 100
            out.append((ts(t.get("created_time")), round(yp, 4), c, t.get("taker_side") or ""))
        cur = js.get("cursor")
        if not cur:
            break
        if i == MAX_PAGES - 1:
            trunc = True
    out.sort()
    return out, trunc


def dip_event(tr, side):
    seen = None
    for t, y, c, s in tr:
        p = y if side == "yes" else 1 - y
        if p >= HIGH:
            seen = t
        elif seen is not None and p <= DIP:
            return seen, t
    return None, None


def candles(ticker, series, hist, start, end):
    rows = []
    base = (f"/historical/markets/{ticker}/candlesticks" if hist
            else f"/series/{series}/markets/{ticker}/candlesticks")
    a = start
    while a < end:
        b = min(end, a + CHUNK_MIN * 60)
        js = get(base, {"start_ts": a, "end_ts": b, "period_interval": 1})
        if js is None and not hist:
            js = get(f"/historical/markets/{ticker}/candlesticks",
                     {"start_ts": a, "end_ts": b, "period_interval": 1})
        for c in (js or {}).get("candlesticks") or []:
            yb, ya = c.get("yes_bid") or {}, c.get("yes_ask") or {}

            def d(o, k):
                v = o.get(k + "_dollars", o.get(k))
                v = f(v)
                if v is not None and v > 1.5:
                    v /= 100
                return "" if v is None else round(v, 4)
            rows.append((c.get("end_period_ts"), d(yb, "close"), d(yb, "high"), d(ya, "close"),
                         d(ya, "low"), f(c.get("volume_fp", c.get("volume"))) or 0))
        a = b
    return rows


def main():
    shard, n, out = int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).with_name("cands.csv.gz")
    by = defaultdict(list)
    for r in csv.DictReader(gzip.open(src, "rt")):
        if zlib.crc32(r["ticker"].encode()) % n == shard:
            by[r["ticker"]].append(r)
    items = sorted(by.items())
    if os.environ.get("LIMIT"):
        items = items[: int(os.environ["LIMIT"])]
    log("shard", shard, "tickers", len(items))
    deadline = T0 + BUDGET_S
    done = [0]

    def work(item):
        tk, rs = item
        if time.time() > deadline:
            return tk, rs, None, False, {}, []
        r0 = rs[0]
        tr, trunc = tape(tk, r0["hist"] == "1")
        ev = {r["side"]: dip_event(tr, r["side"]) for r in rs}
        wins = [(max(h, d - PRE), d + POST) for h, d in ev.values() if d]
        cs = []
        if wins:
            a, b = min(w[0] for w in wins), max(w[1] for w in wins)
            if tr:
                b = min(b, tr[-1][0] + 3600)
            cs = candles(tk, r0["series"], r0["hist"] == "1", a - a % 60, b)
        done[0] += 1
        if done[0] % 500 == 0:
            log("done", done[0])
        return tk, rs, tr, trunc, ev, cs

    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(work, items))

    with gzip.open(out / "events.csv.gz", "wt", newline="") as fe, \
            gzip.open(out / "trades.csv.gz", "wt", newline="") as ft, \
            gzip.open(out / "candles.csv.gz", "wt", newline="") as fc:
        we, wt, wc = csv.writer(fe), csv.writer(ft), csv.writer(fc)
        we.writerow(["ticker", "side", "cat", "series", "result", "volume", "fetched", "n_trades",
                     "truncated", "t_high", "t_dip", "first_trade", "last_trade"])
        wt.writerow(["ticker", "t", "yes_price", "count", "taker_side"])
        wc.writerow(["ticker", "end_ts", "yes_bid", "yes_bid_high", "yes_ask", "yes_ask_low", "volume"])
        for tk, rs, tr, trunc, ev, cs in res:
            for r in rs:
                h, d = ev.get(r["side"], (None, None))
                we.writerow([tk, r["side"], r["cat"], r["series"], r["result"], r["volume"],
                             int(tr is not None), len(tr or []), int(trunc), h or "", d or "",
                             tr[0][0] if tr else "", tr[-1][0] if tr else ""])
            wins = [(max(h, d - PRE), d + POST) for h, d in ev.values() if d]
            if not wins:
                continue
            a, b = min(w[0] for w in wins), max(w[1] for w in wins)
            for t in tr:
                if a <= t[0] <= b:
                    wt.writerow([tk, *t])
            for c in cs:
                wc.writerow([tk, *c])
    (out / "log.txt").write_text("\n".join(LOG) + "\n")
    log("wrote", out)


if __name__ == "__main__":
    main()
