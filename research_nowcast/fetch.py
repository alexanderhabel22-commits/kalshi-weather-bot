"""Nowcast research pull (read-only public data; never trades, never alerts).

Kalshi: every settled market (live + /historical) in the series listed in SERIES,
with rules text and hourly yes bid/ask candles over its last 8 days.
Outside data saved raw for the analysis: TSA daily checkpoint counts and
FlixPatrol daily Netflix US ranks (best effort; status logged).
usage: python research_nowcast/fetch.py <out dir>
"""
from __future__ import annotations

import csv
import gzip
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

K = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = os.environ.get("SERIES", "").split(",") if os.environ.get("SERIES") else ["KXTSAW", "KXTSA", "KXNETFLIXRANKSHOW", "KXNETFLIXRANKSHOWRUNNERUP", "KXNETFLIXRANKMOVIE",
          "KXNETFLIXTOPVIEWSTV", "KXNETFLIXTOPVIEWSMOVIE", "KXSPOTIFYW", "KXTOPALBUM", "KXTOPSONG",
          "KXBILLBOARDRUNNERUPALBUM"]
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
OUT.mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (research script)"
LOG = []
T0 = time.time()


def log(*a):
    msg = f"[{time.time() - T0:6.0f}s] " + " ".join(str(x) for x in a)
    print(msg, flush=True)
    LOG.append(msg)


def get(url, params=None, js=True):
    for i in range(6):
        time.sleep(0.08)
        try:
            r = S.get(url, params=params, timeout=60)
        except requests.RequestException as e:
            log("ERR", url, e)
            time.sleep(2 * (i + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (i + 1))
            continue
        if not r.ok:
            log("HTTP", r.status_code, url, str(params)[:100])
            return None
        return r.json() if js else r.text
    return None


def ts(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()) if s else 0


def px(o):
    """close price in dollars; /historical candles use cents-style keys (sometimes holding dollars)."""
    v = o.get("close_dollars")
    if v is None:
        v = o.get("close")
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return v / 100 if v > 1.0 else v
    return v


def kalshi():
    mrows, crow = [], []
    for s in SERIES:
        got = {}
        for path, extra, hist in (("/markets", {"status": "settled"}, 0), ("/historical/markets", {}, 1)):
            cur = None
            for _ in range(60):
                p = {"series_ticker": s, "limit": 1000, **extra}
                if cur:
                    p["cursor"] = cur
                j = get(K + path, p)
                if not j:
                    break
                for m in j.get("markets") or []:
                    if m["ticker"] not in got:
                        got[m["ticker"]] = (m, hist)
                cur = j.get("cursor")
                if not cur:
                    break
        log(s, "markets", len(got))
        for tk, (m, hist) in got.items():
            if m.get("result") not in ("yes", "no"):
                continue
            mrows.append([tk, s, m.get("event_ticker"), m.get("open_time"), m.get("close_time"), m.get("result"),
                          m.get("volume_fp", m.get("volume")), m.get("strike_type"), m.get("floor_strike"),
                          m.get("cap_strike"), m.get("yes_sub_title"), (m.get("rules_primary") or "")[:600], hist])
            end = ts(m.get("close_time"))
            start = max(ts(m.get("open_time")), end - 8 * 86400)
            url = (f"{K}/historical/markets/{tk}/candlesticks" if hist
                   else f"{K}/series/{s}/markets/{tk}/candlesticks")
            j = get(url, {"start_ts": start, "end_ts": end, "period_interval": 60})
            for c in (j or {}).get("candlesticks") or []:
                yb, ya = c.get("yes_bid") or {}, c.get("yes_ask") or {}
                crow.append([tk, c.get("end_period_ts"), px(yb), px(ya), px(c.get("price") or {}),
                             c.get("volume_fp", c.get("volume"))])
    with gzip.open(OUT / "markets.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "series", "event", "open", "close", "result", "volume", "strike_type", "floor",
                    "cap", "sub", "rules", "hist"])
        w.writerows(mrows)
    with gzip.open(OUT / "candles.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "end_ts", "yes_bid", "yes_ask", "last", "volume"])
        w.writerows(crow)
    log("kalshi rows", len(mrows), len(crow))


def tsa():
    raw = OUT / "tsa"
    raw.mkdir(exist_ok=True)
    for y in ("", "2025", "2024"):
        t = get("https://www.tsa.gov/travel/passenger-volumes" + (f"/{y}" if y else ""), js=False)
        log("tsa", y or "current", "ok" if t else "FAILED", len(t or ""))
        if t:
            (raw / f"tsa_{y or 'current'}.html").write_text(t)


def flix():
    raw = OUT / "flix"
    raw.mkdir(exist_ok=True)
    d, ok = date(2026, 11, 5), 0
    for i in range(0, 400, 1):
        day = d - timedelta(days=i)
        t = get(f"https://flixpatrol.com/top10/netflix/united-states/{day.isoformat()}/", js=False)
        if t:
            ok += 1
            (raw / f"{day.isoformat()}.html.gz").write_bytes(gzip.compress(t.encode()))
        elif i == 3 and ok == 0:
            log("flixpatrol blocked, stopping")
            break
    log("flixpatrol days", ok)
    for name in ("all-weeks-countries.tsv", "all-weeks-global.tsv"):
        t = get(f"https://www.netflix.com/tudum/top10/data/{name}", js=False)
        log("netflix", name, "ok" if t else "FAILED")
        if t:
            (raw / (name + ".gz")).write_bytes(gzip.compress(t.encode()))


if __name__ == "__main__":
    for fn in ((tsa, kalshi) if os.environ.get("SERIES") else (tsa, flix, kalshi)):
        try:
            fn()
        except Exception as e:  # keep going; log it
            log("FAILED", fn.__name__, repr(e))
    (OUT / "log.txt").write_text("\n".join(LOG) + "\n")
