"""Coverage-map gap tests: history pull (read-only public data; never trades, never alerts).

usage: python research_gaps/fetch.py MODE OUT_DIR
  m15  every live 15-minute series (crypto controls, commodities, FX, rates):
       2 markets per series-day, full trade tapes.
  gas  every daily AAA gas series (national + states): every settled market
       (with strikes, to rebuild each day's AAA price) + tapes of the traded ones.
  cry  KXBTCD / KXETHD hourly above/below: every settled market (strikes) +
       tapes of the 4 busiest strikes in 1 of every 3 hourly events, plus
       Coinbase 1-minute candles for BTC-USD / ETH-USD.

Outputs: markets.csv.gz (all listed), sample.csv.gz, trades.csv.gz, [spot.csv.gz], log.txt
"""
from __future__ import annotations

import csv
import gzip
import os
import random
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

K = "https://api.elections.kalshi.com/trade-api/v2"
MODE = sys.argv[1]
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
START = datetime(2026, 7 if MODE == "gas" else 8, 1, tzinfo=timezone.utc)
END = datetime(2026, 11, 7, tzinfo=timezone.utc)
TRADE_PAGES = 10
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


def get(path, params=None, base=K, gap=0.065):
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


COLS = ["ticker", "series", "event", "open_ts", "close_ts", "result", "volume", "hist",
        "strike_type", "floor", "cap"]


def list_series(s, lo, hi):
    out, seen = [], set()
    for path, extra in (("/markets", {"status": "settled", "mve_filter": "exclude"}),
                        ("/historical/markets", {})):
        cur = None
        for _ in range(3000):
            p = {"series_ticker": s, "limit": 1000, "min_close_ts": lo, "max_close_ts": hi,
                 **extra}
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
                out.append({
                    "ticker": tk, "series": s,
                    "event": m.get("event_ticker") or tk.rsplit("-", 1)[0],
                    "open_ts": ts(m.get("open_time")), "close_ts": ct,
                    "result": m.get("result"),
                    "volume": f(m.get("volume_fp", m.get("volume"))) or 0.0,
                    "hist": int(path.startswith("/historical")),
                    "strike_type": m.get("strike_type") or "",
                    "floor": f(m.get("floor_strike")), "cap": f(m.get("cap_strike")),
                })
            cur = js.get("cursor")
            if not cur:
                break
    return out


def tape(m):
    out, cur = [], None
    path = "/historical/trades" if m["hist"] else "/markets/trades"
    for i in range(TRADE_PAGES):
        p = {"ticker": m["ticker"], "limit": 1000}
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
            if yp is None or c is None:
                continue
            if yp > 1.5:
                yp /= 100
            out.append((ts(t.get("created_time")), round(yp, 4), c, t.get("taker_side") or ""))
        cur = js.get("cursor")
        if not cur:
            break
    out.sort()
    return out


def spot(product):
    rows, t = [], int(START.timestamp()) - 2 * 86400
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


def main():
    rnd = random.Random(7)
    js = get("/series", {"include_volume": "true"}) or {}
    allser = js.get("series") or []
    if MODE == "m15":
        ser = [s["ticker"] for s in allser if s.get("ticker", "").endswith("15M")
               and (f(s.get("volume_fp", s.get("volume"))) or 0) >= 300000]
    elif MODE == "gas":
        ser = [s["ticker"] for s in allser if s.get("ticker", "").startswith("KXAAAGASD")]
    else:
        ser = ["KXBTCD", "KXETHD"]
    log(MODE, "series", len(ser), ser[:40])
    lo, hi = int(START.timestamp()), int(END.timestamp())
    with ThreadPoolExecutor(6) as ex:
        allm = [m for lst in ex.map(lambda s: list_series(s, lo, hi), ser) for m in lst]
    log("listed markets", len(allm))
    with gzip.open(OUT / "markets.csv.gz", "wt", newline="") as fh:
        w = csv.DictWriter(fh, COLS)
        w.writeheader()
        w.writerows(allm)

    sample = []
    if MODE == "m15":
        cell = defaultdict(list)
        for m in allm:
            if m["volume"] >= 200:
                day = datetime.fromtimestamp(m["close_ts"], timezone.utc).strftime("%Y-%m-%d")
                cell[(m["series"], day)].append(m)
        for lst in cell.values():
            sample += lst if len(lst) <= 2 else rnd.sample(lst, 2)
    elif MODE == "gas":
        by = defaultdict(list)
        for m in allm:
            if m["volume"] >= 100:
                by[m["series"]].append(m)
        for s, lst in by.items():
            cap = 4000 if s == "KXAAAGASD" else 350
            sample += lst if len(lst) <= cap else rnd.sample(lst, cap)
    else:
        ev = defaultdict(list)
        for m in allm:
            ev[m["event"]].append(m)
        for e, lst in ev.items():
            if rnd.random() < 1 / 3:
                lst = sorted([m for m in lst if m["volume"] >= 500], key=lambda m: -m["volume"])
                sample += lst[:4]
    rnd.shuffle(sample)
    log("sample", len(sample))
    deadline = T0 + BUDGET_S
    done = [0]

    def tw(m):
        if time.time() > deadline:
            return None
        r = tape(m)
        done[0] += 1
        if done[0] % 1000 == 0:
            log("tapes", done[0])
        return r

    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(tw, sample))
    n = 0
    with gzip.open(OUT / "sample.csv.gz", "wt", newline="") as fs, \
            gzip.open(OUT / "trades.csv.gz", "wt", newline="") as ft:
        ws, wt = csv.writer(fs), csv.writer(ft)
        ws.writerow(COLS + ["n_trades"])
        wt.writerow(["ticker", "t", "yes_price", "count", "taker_side"])
        for m, tr in zip(sample, res):
            if tr is None:
                continue
            ws.writerow([m[c] for c in COLS] + [len(tr)])
            for r in tr:
                wt.writerow((m["ticker"],) + r)
            n += len(tr)
    log("tapes fetched", sum(r is not None for r in res), "of", len(sample), "trades", n)
    if MODE == "cry":
        rows = spot("BTC-USD") + spot("ETH-USD")
        with gzip.open(OUT / "spot.csv.gz", "wt", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["product", "t", "close"])
            w.writerows(rows)
    (OUT / "log.txt").write_text("\n".join(LOG) + "\n")


if __name__ == "__main__":
    main()
