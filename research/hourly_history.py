"""Pull history for the hourly-market backtest (read-only, public data).

1. Kalshi 1-minute candlesticks (yes bid/ask close, volume) for every
   finalized hourly market with volume since START.
2. IEM 1-minute ASOS temperatures (NCEI archive) for all index members.
Writes compact CSVs into argv[1].
"""
from __future__ import annotations

import csv
import io
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

K = "https://api.elections.kalshi.com/trade-api/v2"
START = datetime(2026, 11, 5, tzinfo=timezone.utc)
SERIES = ["KXTEMPMIAH", "KXTEMPNYCHS", "KXTEMPCHIHS", "KXTEMPLAXHS"]
MEMBERS = ["MIA", "OPF", "FLL", "FXE", "PMP", "LGA", "EWR", "TEB", "CDW", "HPN", "FRG",
           "SMQ", "ISP", "MDW", "ORD", "PWK", "DPA", "ARR", "HHR", "LAX", "SMO", "LGB", "SNA"]
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
OUT.mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers["User-Agent"] = "kalshi-weather-research/0.1"
_lock = threading.Lock()
_last = [0.0]


def get(url, params=None):
    for i in range(6):
        with _lock:  # global throttle ~12 req/s
            wait = _last[0] + 0.085 - time.time()
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


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


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


def candles(series, m):
    ct = ts(m["close_time"])
    r = get(f"{K}/series/{series}/markets/{m['ticker']}/candlesticks",
            {"start_ts": int((ct - timedelta(minutes=70)).timestamp()),
             "end_ts": int(ct.timestamp()) + 60, "period_interval": 1})
    if r is None or not r.ok:
        return []
    rows = []
    for c in r.json().get("candlesticks") or []:
        mins = (int(ct.timestamp()) - c["end_period_ts"]) / 60
        b = (c.get("yes_bid") or {}).get("close_dollars")
        a = (c.get("yes_ask") or {}).get("close_dollars")
        rows.append([m["ticker"], int(mins), b, a, c.get("volume_fp"), c.get("open_interest_fp")])
    return rows


def kalshi():
    for s in SERIES:
        ms = markets(s)
        with open(OUT / f"mk_{s}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ticker", "event", "close", "strike_type", "floor", "cap", "result",
                        "value", "volume"])
            for m in ms:
                w.writerow([m["ticker"], m["event_ticker"], m["close_time"], m.get("strike_type"),
                            m.get("floor_strike"), m.get("cap_strike"), m.get("result"),
                            m.get("expiration_value"), m.get("volume_fp")])
        todo = [m for m in ms if float(m.get("volume_fp") or 0) > 0]
        t0 = time.time()
        with ThreadPoolExecutor(6) as ex:
            res = list(ex.map(lambda m: candles(s, m), todo))
        with open(OUT / f"cd_{s}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ticker", "min_before_close", "bid", "ask", "volume", "oi"])
            for rows in res:
                w.writerows(rows)
        print(s, len(ms), "markets", len(todo), "with volume", f"{time.time()-t0:.0f}s", flush=True)


def iem():
    end = datetime.now(timezone.utc)
    with open(OUT / "iem1min.csv", "w") as f:
        for st in MEMBERS:
            d = START
            first = True
            while d < end:
                e = min(d + timedelta(days=10), end)
                r = None
                for i in range(4):
                    try:
                        r = S.get("https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py",
                                  params={"station": st, "vars": "tmpf", "sts": d.strftime("%Y-%m-%dT%H:%MZ"),
                                          "ets": e.strftime("%Y-%m-%dT%H:%MZ"), "sample": "1min",
                                          "what": "download", "tz": "UTC"}, timeout=120)
                        if r.ok:
                            break
                    except requests.RequestException:
                        pass
                    time.sleep(5)
                if r is not None and r.ok:
                    lines = io.StringIO(r.text).read().splitlines()
                    if not first:
                        lines = lines[1:]
                    f.write("\n".join(lines) + "\n")
                    first = False
                d = e
            print("iem", st, flush=True)


if __name__ == "__main__":
    th = threading.Thread(target=iem)
    th.start()
    kalshi()
    th.join()
