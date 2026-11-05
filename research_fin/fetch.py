"""Full-history pull for the fade-longshots test in one Kalshi category.

Read-only public data; never trades, never alerts.
usage: python research_fin/fetch.py CATEGORY OUT_DIR

1. Every series in CATEGORY (fee settings, volume).
2. Every settled market in those series that closed Feb 2025 - Oct 2026
   (live + /historical endpoints), kept as a slim index.
3. A random sample of up to PER_MONTH markets per (series, close month) with
   volume >= 10, plus the sampling weight, so totals can be scaled back up.
4. Every trade in the sampled markets (capped), aggregated per
   (ticker, taker side, price, time-to-close bucket, size bucket, trade-through).
   trade-through = within the next hour the market traded at a price beyond
   this one in the taker's direction, i.e. the level was used up. Fills a
   back-of-queue resting order would have got.
"""
from __future__ import annotations

import csv
import gzip
import os
import random
import sys
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

K = "https://api.elections.kalshi.com/trade-api/v2"
CAT = sys.argv[1]
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "out")
OUT.mkdir(parents=True, exist_ok=True)
START = datetime(2025, 2, 1, tzinfo=timezone.utc)
END = datetime(2026, 11, 1, tzinfo=timezone.utc)
PER_MONTH = int(os.environ.get("PER_MONTH", "25"))
TRADE_PAGES = 10
MIN_SERIES_VOL = 20000
BUDGET_S = 4.4 * 3600
TTC = [(15 * 60, "a<15m"), (3600, "b15-60m"), (4 * 3600, "c1-4h"), (86400, "d4-24h"),
       (7 * 86400, "e1-7d"), (30 * 86400, "f7-30d"), (10 ** 12, "g>30d")]
S = requests.Session()
S.headers["User-Agent"] = "kalshi-research/0.1"
_lock = threading.Lock()
_last = [0.0]
T0 = time.time()
LOG = []


def log(*a):
    msg = f"[{time.time() - T0:6.0f}s] " + " ".join(str(x) for x in a)
    print(msg, flush=True)
    LOG.append(msg)


def get(path, params=None):
    for i in range(7):
        with _lock:
            wait = _last[0] + 0.062 - time.time()
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
    if not s:
        return 0
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def size_bucket(c):
    return "1-9" if c < 10 else "10-99" if c < 100 else "100-999" if c < 1000 else "1000+"


def window_extremes(tr, horizon=3600):
    """For each trade j: max and min price of trades after j within `horizon` seconds."""
    n = len(tr)
    fmax, fmin = [-1.0] * n, [9.0] * n
    dmax, dmin = deque(), deque()  # indices, window = (j, k)
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
        if dmax:
            fmax[j] = tr[dmax[0]][1]
        if dmin:
            fmin[j] = tr[dmin[0]][1]
    return fmax, fmin


def main():
    js = get("/series", {"category": CAT, "include_volume": "true"}) or {}
    series = [s for s in js.get("series") or [] if s.get("category") == CAT]
    with open(OUT / "series.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["series", "category", "title", "frequency", "fee_type", "fee_multiplier",
                    "volume", "tags"])
        for s in series:
            w.writerow([s.get("ticker"), s.get("category"), s.get("title"), s.get("frequency"),
                        s.get("fee_type"), s.get("fee_multiplier"),
                        s.get("volume_fp", s.get("volume")), "|".join(s.get("tags") or [])])
    keep = [s["ticker"] for s in series
            if (f(s.get("volume_fp", s.get("volume"))) or 0) >= MIN_SERIES_VOL]
    log(CAT, "series", len(series), "with volume >=", MIN_SERIES_VOL, len(keep))

    lo, hi = int(START.timestamp()), int(END.timestamp())

    def list_series(s):
        out, seen = [], set()
        for path, extra in (("/markets", {"status": "settled", "mve_filter": "exclude"}),
                            ("/historical/markets", {})):
            cur = None
            for _ in range(1500):
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
                    out.append((tk, s, m.get("event_ticker") or tk.rsplit("-", 1)[0],
                                m.get("open_time") or "", m.get("close_time"), ct,
                                m.get("result"), f(m.get("volume_fp", m.get("volume"))) or 0.0,
                                int(path.startswith("/historical")), m.get("strike_type") or "",
                                (m.get("title") or "")[:100]))
                cur = js.get("cursor")
                if not cur:
                    break
        return out

    with ThreadPoolExecutor(6) as ex:
        allm = [m for lst in ex.map(list_series, keep) for m in lst]
    log("settled markets in window", len(allm))

    # index of every market (for capacity: total volume per series-month)
    cell = defaultdict(list)
    vol_cell = defaultdict(lambda: [0, 0.0])
    for m in allm:
        month = m[4][:7]
        vol_cell[(m[1], month)][0] += 1
        vol_cell[(m[1], month)][1] += m[7]
        if m[7] >= 10:
            cell[(m[1], month)].append(m)
    with open(OUT / "volume_by_series_month.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["series", "month", "markets", "volume"])
        for (s, mo), (n, v) in sorted(vol_cell.items()):
            w.writerow([s, mo, n, round(v)])

    rnd = random.Random(11)
    sample = []
    for key, lst in cell.items():
        pick = lst if len(lst) <= PER_MONTH else rnd.sample(lst, PER_MONTH)
        wgt = len(lst) / len(pick)
        sample += [(m, wgt) for m in pick]
    rnd.shuffle(sample)  # so a time-budget cutoff drops a random subset
    log("sampled markets", len(sample))

    deadline = T0 + BUDGET_S

    def trades(item):
        m, _ = item
        if time.time() > deadline:
            return None, False
        out, cur, trunc = [], None, False
        path = "/historical/trades" if m[8] else "/markets/trades"
        for i in range(TRADE_PAGES):
            p = {"ticker": m[0], "limit": 1000}
            if cur:
                p["cursor"] = cur
            js = get(path, p)
            if not js:
                if m[8] and i == 0:  # some historical markets live on the live endpoint
                    path = "/markets/trades"
                    js = get(path, p)
                if not js:
                    break
            for t in js.get("trades") or []:
                yp, c = f(t.get("yes_price_dollars", t.get("yes_price"))), f(
                    t.get("count_fp", t.get("count")))
                if yp is None or c is None:
                    continue
                if yp > 1.5:  # cents
                    yp /= 100
                out.append((ts(t.get("created_time")), round(yp, 2), c, t.get("taker_side")))
            cur = js.get("cursor")
            if not cur:
                break
            if i == TRADE_PAGES - 1:
                trunc = True
        return out, trunc

    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(trades, sample))

    agg = defaultdict(lambda: [0.0, 0])
    with gzip.open(OUT / "sample.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "series", "event", "open", "close", "result", "volume", "hist",
                    "strike_type", "title", "weight", "fetched", "truncated", "n_trades"])
        for (m, wgt), (tr, trunc) in zip(sample, res):
            w.writerow([m[0], m[1], m[2], m[3], m[4], m[6], m[7], m[8], m[9], m[10],
                        round(wgt, 3), int(tr is not None), int(trunc), len(tr or [])])
            if not tr:
                continue
            tr.sort()
            fmax, fmin = window_extremes(tr)
            for j, (t0, yp, c, side) in enumerate(tr):
                if side not in ("yes", "no"):
                    continue
                thr = int(fmax[j] > yp) if side == "yes" else int(fmin[j] < yp)
                left = m[5] - t0
                tb = next(lab for lim, lab in TTC if left < lim)
                key = (m[0], side, yp, tb, size_bucket(c), thr)
                a = agg[key]
                a[0] += c
                a[1] += 1
    with gzip.open(OUT / "trades_agg.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "taker_side", "yes_price", "ttc", "size", "through", "count",
                    "n"])
        for k, (c, n) in agg.items():
            w.writerow([*k, round(c, 2), n])
    done = sum(1 for r in res if r[0] is not None)
    log("fetched", done, "of", len(sample), "truncated", sum(1 for r in res if r[1]),
        "trades", sum(len(r[0] or []) for r in res))


if __name__ == "__main__":
    try:
        main()
    finally:
        (OUT / "log.txt").write_text("\n".join(LOG))
