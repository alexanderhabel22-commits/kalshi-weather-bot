"""15-minute history mine: multi-year 1-minute prices + Kalshi 15-min candlesticks.

Research only: read-only public data, never trades, never alerts.

usage: python research_hist15/fetch.py MODE OUT_DIR [ARGS]
  binance SYM...   1-min klines from data.binance.vision (monthly + daily zips), 2017->now.
                   Falls back to Coinbase candles if Binance is unreachable.
  duka SYM...      Dukascopy 1-min BID candles (daily .bi5 files), 2016->now.
  kalshi SERIES... every settled market (Jan 2025->now) + 1-min candlesticks.

Price modes write <SYM>.npz with one row per 15-minute window (aligned :00/:15/:30/:45):
  t0     window start (unix s)
  p0     price at window start (close of the minute before), raw units
  strike typical price (o+h+l+c)/4 of the minute before the window (Kalshi uses the
         60 s average before open)
  path   int16[15]: close of each minute k=1..15 vs p0, in 0.1 bps (-32768 = missing)
  settle typical price of the last minute vs p0, 0.1 bps
  hi/lo  int16: window high/low vs p0, 0.1 bps
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import lzma
import os
import struct
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

MODE = sys.argv[1]
OUT = Path(sys.argv[2])
ARGS = sys.argv[3:]
OUT.mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers["User-Agent"] = "research/0.1"
T0 = time.time()
LOG: list[str] = []
NOW = datetime.now(timezone.utc)


def log(*a):
    msg = f"[{time.time() - T0:6.0f}s] " + " ".join(str(x) for x in a)
    print(msg, flush=True)
    LOG.append(msg)
    (OUT / f"log-{MODE}-{ARGS[0] if ARGS else 'x'}.txt").write_text("\n".join(LOG) + "\n")


def http(url, params=None, tries=6, ok404=True):
    for i in range(tries):
        try:
            r = S.get(url, params=params, timeout=60)
        except requests.RequestException as e:
            if i == tries - 1:
                log("ERR", url, e)
            time.sleep(1.5 * (i + 1))
            continue
        if r.status_code == 404 and ok404:
            return None
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (i + 1))
            continue
        if not r.ok:
            log("HTTP", r.status_code, url, r.text[:120])
            return None
        return r
    return None


# ---------------------------------------------------------------- windows

def to_windows(t, o, h, l, c):
    """t: minute open times (s), arrays sorted. Returns dict of window arrays."""
    t = np.asarray(t, dtype=np.int64)
    idx = {int(x): i for i, x in enumerate(t)}
    first = (t[0] // 900 + 1) * 900
    last = (t[-1] // 900) * 900
    rows = []
    for w in range(first, last, 900):
        j = idx.get(w - 60)
        if j is None:
            continue
        p0 = c[j]
        if not p0 or p0 <= 0:
            continue
        strike = (o[j] + h[j] + l[j] + c[j]) / 4
        path = np.full(15, -32768, dtype=np.int16)
        hi = lo = 0.0
        got = 0
        settle = -32768
        for k in range(15):
            m = idx.get(w + 60 * k)
            if m is None:
                continue
            got += 1
            path[k] = int(np.clip(round((c[m] / p0 - 1) * 1e5), -32767, 32767))
            hi = max(hi, h[m] / p0 - 1)
            lo = min(lo, l[m] / p0 - 1)
            if k == 14:
                settle = int(np.clip(round(((o[m] + h[m] + l[m] + c[m]) / 4 / p0 - 1) * 1e5),
                                     -32767, 32767))
        if got < 12:
            continue
        rows.append((w, p0, strike, path, settle,
                     int(np.clip(round(hi * 1e5), 0, 32767)),
                     int(np.clip(round(lo * 1e5), -32767, 0))))
    if not rows:
        return None
    return dict(
        t0=np.array([r[0] for r in rows], dtype=np.int64),
        p0=np.array([r[1] for r in rows], dtype=np.float64),
        strike=np.array([r[2] for r in rows], dtype=np.float64),
        path=np.stack([r[3] for r in rows]),
        settle=np.array([r[4] for r in rows], dtype=np.int16),
        hi=np.array([r[5] for r in rows], dtype=np.int16),
        lo=np.array([r[6] for r in rows], dtype=np.int16),
    )


def save(sym, t, o, h, l, c, src):
    order = np.argsort(t)
    t, o, h, l, c = (np.asarray(x)[order] for x in (t, o, h, l, c))
    keep = np.concatenate([[True], np.diff(t) > 0])
    t, o, h, l, c = t[keep], o[keep], h[keep], l[keep], c[keep]
    w = to_windows(t, o.astype(float), h.astype(float), l.astype(float), c.astype(float))
    if w is None:
        log(sym, "no windows")
        return
    np.savez_compressed(OUT / f"{sym}.npz", **w)
    # last 120 days of raw minutes too (for matching Kalshi settlements exactly)
    cut = t >= int((NOW - timedelta(days=120)).timestamp())
    np.savez_compressed(OUT / f"{sym}_recent_1m.npz", t=t[cut], o=o[cut], h=h[cut],
                        l=l[cut], c=c[cut])
    log(sym, src, "minutes", len(t), "windows", len(w["t0"]),
        datetime.fromtimestamp(int(t[0]), timezone.utc).date(), "->",
        datetime.fromtimestamp(int(t[-1]), timezone.utc))


# ---------------------------------------------------------------- binance / coinbase

def binance_zip(url):
    r = http(url)
    if r is None:
        return None
    z = zipfile.ZipFile(io.BytesIO(r.content))
    rows = list(csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))))
    out = []
    for x in rows:
        try:
            ts = int(x[0])
        except ValueError:
            continue  # header
        if ts > 1e14:
            ts //= 1000  # microseconds (2025+)
        out.append((ts // 1000, float(x[1]), float(x[2]), float(x[3]), float(x[4])))
    return out


def binance(sym):
    pair = sym + "USDT"
    base = "https://data.binance.vision/data/spot"
    jobs = []
    d = date(2017, 7, 1)
    this_month = date(NOW.year, NOW.month, 1)
    while d < this_month:
        jobs.append(f"{base}/monthly/klines/{pair}/1m/{pair}-1m-{d:%Y-%m}.zip")
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    d = this_month
    while d < NOW.date():
        jobs.append(f"{base}/daily/klines/{pair}/1m/{pair}-1m-{d:%Y-%m-%d}.zip")
        d += timedelta(days=1)
    with ThreadPoolExecutor(8) as ex:
        parts = list(ex.map(binance_zip, jobs))
    rows = [r for p in parts if p for r in p]
    got = sum(1 for p in parts if p)
    log(sym, "binance files", got, "of", len(jobs), "rows", len(rows))
    if len(rows) < 100000:
        return False
    a = np.array(rows)
    save(sym, a[:, 0].astype(np.int64), a[:, 1], a[:, 2], a[:, 3], a[:, 4], "binance")
    return True


def coinbase(sym, start=datetime(2017, 1, 1, tzinfo=timezone.utc)):
    rows = []
    t = int(start.timestamp())
    end = int(NOW.timestamp())
    lock = threading.Lock()

    def chunk(a):
        r = http(f"https://api.exchange.coinbase.com/products/{sym}-USD/candles",
                 {"granularity": 60, "start": datetime.fromtimestamp(a, timezone.utc).isoformat(),
                  "end": datetime.fromtimestamp(a + 299 * 60, timezone.utc).isoformat()})
        time.sleep(0.4)
        if r is None:
            return
        with lock:
            for x in r.json():
                rows.append((x[0], x[3], x[2], x[1], x[4]))  # t, low, high, open, close -> o h l c
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(chunk, range(t, end, 300 * 60)))
    log(sym, "coinbase rows", len(rows))
    if not rows:
        return
    a = np.array(rows)
    save(sym, a[:, 0].astype(np.int64), a[:, 1], a[:, 2], a[:, 3], a[:, 4], "coinbase")


# ---------------------------------------------------------------- dukascopy

def duka_day(sym, d):
    url = (f"https://datafeed.dukascopy.com/datafeed/{sym}/{d.year}/{d.month - 1:02d}/"
           f"{d.day:02d}/BID_candles_min_1.bi5")
    r = http(url)
    if r is None or not r.content:
        return []
    try:
        raw = lzma.decompress(r.content)
    except lzma.LZMAError:
        return []
    day0 = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())
    out = []
    for i in range(0, len(raw) - 23, 24):
        ts, o, c, lo, hi, v = struct.unpack(">5if", raw[i:i + 24])
        if v <= 0:
            continue  # flat filler minutes while the market is shut
        out.append((day0 + ts, o, hi, lo, c))
    return out


def duka(sym, start=None):
    start = start or (date(2017, 1, 1) if sym == "XAGUSD" else date(2019, 1, 1))
    days = []
    d = start
    while d < NOW.date():
        if d.weekday() != 5:  # Saturday is always shut
            days.append(d)
        d += timedelta(days=1)
    rows = []
    with ThreadPoolExecutor(4) as ex:
        for i, part in enumerate(ex.map(lambda x: duka_day(sym, x), days)):
            rows.extend(part)
            if i % 500 == 0:
                log(sym, "day", i, "of", len(days), "rows", len(rows))
    log(sym, "dukascopy rows", len(rows))
    if not rows:
        return
    a = np.array(rows, dtype=np.float64)
    save(sym, a[:, 0].astype(np.int64), a[:, 1], a[:, 2], a[:, 3], a[:, 4], "dukascopy")


# ---------------------------------------------------------------- kalshi

K = "https://api.elections.kalshi.com/trade-api/v2"
_klock = threading.Lock()
_klast = [0.0]


def kget(path, params=None, gap=0.055):
    with _klock:
        wait = _klast[0] + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _klast[0] = time.time()
    r = http(K + path, params)
    return r.json() if r is not None else None


def ts(s):
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except (AttributeError, ValueError):
        return 0


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def list_markets(s):
    lo = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
    hi = int(NOW.timestamp())
    out, seen = [], set()
    for path, extra in (("/markets", {"status": "settled"}), ("/historical/markets", {})):
        cur = None
        for _ in range(5000):
            p = {"series_ticker": s, "limit": 1000, "min_close_ts": lo, "max_close_ts": hi, **extra}
            if cur:
                p["cursor"] = cur
            js = kget(path, p)
            if not js:
                break
            for m in js.get("markets") or []:
                tk = m.get("ticker")
                if tk in seen or m.get("result") not in ("yes", "no"):
                    continue
                seen.add(tk)
                out.append(dict(ticker=tk, series=s, open_ts=ts(m.get("open_time")),
                                close_ts=ts(m.get("close_time")), result=m.get("result"),
                                volume=fnum(m.get("volume_fp", m.get("volume"))) or 0.0,
                                hist=int(path.startswith("/historical")),
                                floor=fnum(m.get("floor_strike")),
                                expiration_value=m.get("expiration_value") or ""))
            cur = js.get("cursor")
            if not cur:
                break
    return out


def cents(d, key):
    """candle sub-dict -> close in dollars (handles *_dollars strings and cents ints)."""
    x = d.get(key) or {}
    for k in ("close_dollars", "close"):
        v = fnum(x.get(k))
        if v is not None:
            return v / 100 if k == "close" and v > 1.5 else v
    return None


_shown = [0]


_stop = [0.0]


def candles(m):
    if _stop[0] and time.time() > _stop[0]:
        return []
    p = {"start_ts": m["open_ts"] - 60, "end_ts": m["close_ts"], "period_interval": 1}
    paths = [f"/series/{m['series']}/markets/{m['ticker']}/candlesticks",
             f"/historical/markets/{m['ticker']}/candlesticks"]
    if m["hist"]:
        paths.reverse()
    for path in paths:
        js = kget(path, p)
        cs = (js or {}).get("candlesticks") or []
        if cs:
            if _shown[0] < 2:
                _shown[0] += 1
                log("sample candle", path, json.dumps(cs[-1])[:600])
            out = []
            for c in cs:
                out.append((m["ticker"], c.get("end_period_ts"), cents(c, "yes_bid"),
                            cents(c, "yes_ask"), cents(c, "price"),
                            fnum(c.get("volume_fp", c.get("volume"))) or 0.0))
            return out
    return []


def kalshi(series):
    ms = list_markets(series)
    log(series, "markets", len(ms))
    with gzip.open(OUT / f"{series}_markets.csv.gz", "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(ms[0].keys()) if ms else ["ticker"])
        w.writeheader()
        w.writerows(ms)
    n = 0
    # newest markets first, and a per-series time cap so one series can't eat the job
    ms.sort(key=lambda m: -m["close_ts"])
    _stop[0] = time.time() + float(os.environ.get("SERIES_MIN", "24")) * 60
    with gzip.open(OUT / f"{series}_candles.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "t", "bid", "ask", "last", "vol"])
        with ThreadPoolExecutor(6) as ex:
            for i, rows in enumerate(ex.map(candles, ms)):
                w.writerows(rows)
                n += bool(rows)
                if i % 1000 == 0:
                    log(series, "candles", i, "of", len(ms), "with data", n)
    log(series, "done, markets with candles", n)


if __name__ == "__main__":
    for a in ARGS:
        try:
            if MODE == "binance":
                if not binance(a):
                    coinbase(a)
            elif MODE == "coinbase":
                coinbase(a)
            elif MODE == "duka":
                duka(a)
            elif MODE == "kalshi":
                kalshi(a)
        except Exception as e:  # keep going with the next symbol
            log("FAILED", a, repr(e))
    log("all done")
