"""Pull every trade for finalized hourly markets since START (read-only).

Used to replay a market-making strategy. Writes tr_<series>.csv into argv[1].
"""
from __future__ import annotations

import csv
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

K = "https://api.elections.kalshi.com/trade-api/v2"
START = datetime(2026, 11, 5, tzinfo=timezone.utc)
SERIES = ["KXTEMPMIAH", "KXTEMPNYCHS", "KXTEMPCHIHS", "KXTEMPLAXHS"]
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
OUT.mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers["User-Agent"] = "kalshi-weather-research/0.1"
_lock = threading.Lock()
_last = [0.0]


def get(url, params=None):
    for i in range(6):
        with _lock:
            wait = _last[0] + 0.08 - time.time()
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            r = S.get(url, params=params, timeout=40)
        except requests.RequestException:
            time.sleep(2 * (i + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (i + 1))
            continue
        return r
    return None


def markets(series):
    out, cur = [], None
    for _ in range(300):
        p = {"series_ticker": series, "status": "settled", "limit": 1000,
             "min_close_ts": int(START.timestamp())}
        if cur:
            p["cursor"] = cur
        r = get(f"{K}/markets", p)
        if r is None or not r.ok:
            break
        js = r.json()
        out += js.get("markets") or []
        cur = js.get("cursor")
        if not cur:
            break
    return out


def trades(ticker):
    rows, cur = [], None
    for _ in range(50):
        p = {"ticker": ticker, "limit": 1000}
        if cur:
            p["cursor"] = cur
        r = get(f"{K}/markets/trades", p)
        if r is None or not r.ok:
            break
        js = r.json()
        for t in js.get("trades") or []:
            rows.append([ticker, t["created_time"], t.get("yes_price_dollars"),
                         t.get("count_fp"), t.get("taker_side")])
        cur = js.get("cursor")
        if not cur:
            break
    return rows


if __name__ == "__main__":
    for s in SERIES:
        t0 = time.time()
        todo = [m["ticker"] for m in markets(s) if float(m.get("volume_fp") or 0) > 0]
        with ThreadPoolExecutor(6) as ex:
            res = list(ex.map(trades, todo))
        with open(OUT / f"tr_{s}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ticker", "time", "yes_price", "count", "taker_side"])
            for rows in res:
                w.writerows(rows)
        print(s, len(todo), "markets", sum(map(len, res)), "trades", f"{time.time()-t0:.0f}s",
              flush=True)
