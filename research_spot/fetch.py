"""BTC 15-minute spot-signal replay: history pull (read-only public data; never trades).

usage: python research_spot/fetch.py SERIES:PRODUCT:SOURCE OUT_DIR
  SOURCE cb = Coinbase 1-min candles (from Sep 25), yf = Yahoo futures 1-min (last ~29 days)
  - every settled SERIES market in the span (strike = floor_strike, result)
  - trade tapes of the last LAST_S seconds of each market (shuffled, so a budget cutoff
    still leaves a random sample)
  - Coinbase BTC-USD 1-minute candles over the same span
Outputs: markets.csv.gz, sample.csv.gz, trades.csv.gz, spot.csv.gz, log.txt
"""
from __future__ import annotations

import csv
import gzip
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

K = "https://api.elections.kalshi.com/trade-api/v2"
SERIES, PRODUCT, SRC = sys.argv[1].split(":")
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
END = datetime.now(timezone.utc)
START = (datetime(2026, 9, 25, tzinfo=timezone.utc) if SRC == "cb"
         else datetime.fromtimestamp(END.timestamp() - 29 * 86400, timezone.utc))
LAST_S = 660
TRADE_PAGES = 40
BUDGET_S = float(os.environ.get("BUDGET_H", "4.0")) * 3600
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


def get(path, params=None, base=K, gap=0.06):
    for i in range(8):
        with _lock:
            wait = _last[0] + gap - time.time()
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            r = S.get(base + path, params=params, timeout=60)
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
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


COLS = ["ticker", "open_ts", "close_ts", "result", "volume", "hist", "strike_type", "floor"]


def list_markets(lo, hi):
    out, seen = [], set()
    for path, extra in (("/markets", {"status": "settled"}), ("/historical/markets", {})):
        cur = None
        for _ in range(3000):
            p = {"series_ticker": SERIES, "limit": 1000, "min_close_ts": lo,
                 "max_close_ts": hi, **extra}
            if cur:
                p["cursor"] = cur
            js = get(path, p)
            if not js:
                break
            for m in js.get("markets") or []:
                tk = m.get("ticker")
                if tk in seen or m.get("result") not in ("yes", "no"):
                    continue
                ct = ts(m.get("close_time"))
                if not lo <= ct < hi:
                    continue
                seen.add(tk)
                out.append({"ticker": tk, "open_ts": ts(m.get("open_time")), "close_ts": ct,
                            "result": m.get("result"),
                            "volume": f(m.get("volume_fp", m.get("volume"))) or 0.0,
                            "hist": int(path.startswith("/historical")),
                            "strike_type": m.get("strike_type") or "",
                            "floor": f(m.get("floor_strike"))})
            cur = js.get("cursor")
            if not cur:
                break
    return out


def tape(m):
    out, cur, full = [], None, False
    path = "/historical/trades" if m["hist"] else "/markets/trades"
    for i in range(TRADE_PAGES):
        p = {"ticker": m["ticker"], "limit": 1000, "min_ts": m["close_ts"] - LAST_S}
        if cur:
            p["cursor"] = cur
        js = get(path, p)
        if not js and i == 0:
            path = "/markets/trades" if m["hist"] else "/historical/trades"
            js = get(path, p)
        if not js:
            break
        for t in js.get("trades") or []:
            yp = f(t.get("yes_price_dollars", t.get("yes_price")))
            c = f(t.get("count_fp", t.get("count")))
            tt = ts(t.get("created_time"))
            if yp is None or c is None or tt < m["close_ts"] - LAST_S:
                continue
            if yp > 1.5:
                yp /= 100
            out.append((tt, round(yp, 4), c, t.get("taker_side") or ""))
        cur = js.get("cursor")
        if not cur:
            full = True
            break
    out.sort()
    return out, full


def spot(product):
    rows, t = [], int(START.timestamp()) - 86400
    end = int(END.timestamp())
    while t < end:
        t2 = min(t + 300 * 60, end)
        js = get(f"/products/{product}/candles",
                 {"granularity": 60,
                  "start": datetime.fromtimestamp(t, timezone.utc).isoformat(),
                  "end": datetime.fromtimestamp(t2, timezone.utc).isoformat()},
                 base="https://api.exchange.coinbase.com", gap=0.2)
        for c in js or []:
            rows.append((product, int(c[0]), c[4]))
        t = t2
    log("spot", product, len(rows))
    return rows


def spot_yf(sym):
    rows, t = [], int(START.timestamp())
    end = int(END.timestamp())
    while t < end:
        t2 = min(t + 7 * 86400, end)
        js = get(f"/v8/finance/chart/{sym}", {"interval": "1m", "period1": t, "period2": t2,
                                              "includePrePost": "true"},
                 base="https://query1.finance.yahoo.com", gap=0.5)
        try:
            r = js["chart"]["result"][0]
            for tt, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
                if c is not None:
                    rows.append((sym, int(tt), c))
        except (TypeError, KeyError, IndexError):
            log("yahoo empty", sym, t, str(js)[:200])
        t = t2
    log("spot", sym, len(rows))
    return rows


def main():
    S.headers["User-Agent"] = "Mozilla/5.0 (research script)" if SRC == "yf" else S.headers["User-Agent"]
    rows = spot(PRODUCT) if SRC == "cb" else spot_yf(PRODUCT)
    S.headers["User-Agent"] = "kalshi-research/0.1"
    with gzip.open(OUT / "spot.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["product", "t", "close"])
        w.writerows(rows)
    allm = list_markets(int(START.timestamp()), int(END.timestamp()))
    log("listed markets", len(allm))
    with gzip.open(OUT / "markets.csv.gz", "wt", newline="") as fh:
        w = csv.DictWriter(fh, COLS)
        w.writeheader()
        w.writerows(allm)
    sample = [m for m in allm if m["volume"] >= 200]
    random.Random(7).shuffle(sample)
    deadline = T0 + BUDGET_S
    done = [0]

    def tw(m):
        if time.time() > deadline:
            return None
        r = tape(m)
        done[0] += 1
        if done[0] % 250 == 0:
            log("tapes", done[0])
        return r

    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(tw, sample))
    n = 0
    with gzip.open(OUT / "sample.csv.gz", "wt", newline="") as fs, \
            gzip.open(OUT / "trades.csv.gz", "wt", newline="") as ft:
        ws, wt = csv.writer(fs), csv.writer(ft)
        ws.writerow(COLS + ["n_trades", "full"])
        wt.writerow(["ticker", "t", "yes_price", "count", "taker_side"])
        for m, r in zip(sample, res):
            if r is None:
                continue
            tr, full = r
            ws.writerow([m[c] for c in COLS] + [len(tr), int(full)])
            for x in tr:
                wt.writerow((m["ticker"],) + x)
            n += len(tr)
    log("tapes fetched", sum(r is not None for r in res), "of", len(sample), "trades", n)
    (OUT / "log.txt").write_text("\n".join(LOG) + "\n")


if __name__ == "__main__":
    main()
