"""Non-weather Kalshi edge probe (read-only public data; never trades).

1. Series catalog (category, fee settings).
2. Every settled non-parlay market that closed in the last DAYS days.
3. A volume-weighted sample of those markets per category, with every trade,
   so we can see who wins (taker vs maker) at each price level per category.
4. Snapshot of open markets (spreads, 24h volume) for market-making sizing.

Writes CSV/JSON files into argv[1].
"""
from __future__ import annotations

import csv
import gzip
import json
import random
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

K = "https://api.elections.kalshi.com/trade-api/v2"
DAYS = 45
PER_CAT_TOP = 120
PER_CAT_RANDOM = 180
TRADE_PAGES = 12
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
OUT.mkdir(parents=True, exist_ok=True)
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
            log("HTTP", r.status_code, path, params, r.text[:200])
            return None
        return r.json()
    return None


def paginate(path, key, params, max_pages, budget_s=None):
    out, cur, t = [], None, time.time()
    for _ in range(max_pages):
        p = dict(params)
        if cur:
            p["cursor"] = cur
        js = get(path, p)
        if not js:
            break
        out += js.get(key) or []
        cur = js.get("cursor")
        if not cur or (budget_s and time.time() - t > budget_s):
            break
    return out


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def series_of(m):
    return (m.get("event_ticker") or m.get("ticker") or "").split("-")[0]


def main():
    since = datetime.now(timezone.utc) - timedelta(days=DAYS)

    # 1. series catalog
    js = get("/series", {"include_volume": "true"}) or {}
    series = js.get("series") or []
    cat = {s["ticker"]: s.get("category") or "?" for s in series}
    with open(OUT / "series.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["series", "category", "title", "frequency", "fee_type", "fee_multiplier",
                    "volume", "tags"])
        for s in series:
            w.writerow([s.get("ticker"), s.get("category"), s.get("title"), s.get("frequency"),
                        s.get("fee_type"), s.get("fee_multiplier"),
                        s.get("volume_fp", s.get("volume")), "|".join(s.get("tags") or [])])
    if series:
        (OUT / "series_example.json").write_text(json.dumps(series[:3], indent=1))
    log("series", len(series))

    # 2. settled markets
    ms = paginate("/markets", "markets",
                  {"status": "settled", "limit": 1000, "mve_filter": "exclude",
                   "min_close_ts": int(since.timestamp())}, 2500, budget_s=3000)
    if ms:
        (OUT / "market_example.json").write_text(json.dumps(ms[:3], indent=1))
    log("settled markets", len(ms))
    rows = []
    for m in ms:
        s = series_of(m)
        rows.append({
            "ticker": m.get("ticker"), "series": s, "category": cat.get(s, "?"),
            "open": m.get("open_time"), "close": m.get("close_time"),
            "result": m.get("result"),
            "volume": f(m.get("volume_fp", m.get("volume"))) or 0.0,
            "settle_value": m.get("settlement_value_dollars", m.get("settlement_value")),
            "title": (m.get("title") or "")[:120],
        })
    with gzip.open(OUT / "settled.csv.gz", "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["ticker"])
        w.writeheader()
        w.writerows(rows)

    # 3. sample per category, fetch trades
    by_cat = defaultdict(list)
    for r in rows:
        if r["result"] in ("yes", "no") and r["volume"] >= 10:
            by_cat[r["category"]].append(r)
    rnd = random.Random(7)
    sample = []
    for c, lst in by_cat.items():
        lst.sort(key=lambda r: -r["volume"])
        top = lst[:PER_CAT_TOP]
        rest = lst[PER_CAT_TOP:]
        sample += top + rnd.sample(rest, min(PER_CAT_RANDOM, len(rest)))
        log("category", c, "markets>=10vol", len(lst), "volume", int(sum(r["volume"] for r in lst)))

    def trades(r):
        out, cur, trunc = [], None, False
        for i in range(TRADE_PAGES):
            p = {"ticker": r["ticker"], "limit": 1000}
            if cur:
                p["cursor"] = cur
            js = get("/markets/trades", p)
            if not js:
                break
            for t in js.get("trades") or []:
                out.append([r["ticker"], t.get("created_time"),
                            t.get("yes_price_dollars", t.get("yes_price")),
                            t.get("count_fp", t.get("count")), t.get("taker_side")])
            cur = js.get("cursor")
            if not cur:
                break
            if i == TRADE_PAGES - 1:
                trunc = True
        return out, trunc

    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(trades, sample))
    ntr = 0
    with gzip.open(OUT / "trades.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "time", "yes_price", "count", "taker_side"])
        for rows_, _ in res:
            w.writerows(rows_)
            ntr += len(rows_)
    with open(OUT / "sample.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "truncated", "in_top"])
        tops = set()
        for c, lst in by_cat.items():
            tops |= {r["ticker"] for r in lst[:PER_CAT_TOP]}
        for r, (_, tr) in zip(sample, res):
            w.writerow([r["ticker"], int(tr), int(r["ticker"] in tops)])
    log("sample", len(sample), "trades", ntr)

    # 4. open-market snapshot
    om = paginate("/markets", "markets", {"status": "open", "limit": 1000, "mve_filter": "exclude"},
                  800, budget_s=1500)
    with gzip.open(OUT / "open.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "series", "category", "close", "bid", "ask", "last", "volume_24h",
                    "volume", "open_interest", "liquidity", "title"])
        for m in om:
            s = series_of(m)
            w.writerow([m.get("ticker"), s, cat.get(s, "?"), m.get("close_time"),
                        m.get("yes_bid_dollars"), m.get("yes_ask_dollars"),
                        m.get("last_price_dollars"), m.get("volume_24h_fp"),
                        m.get("volume_fp"), m.get("open_interest_fp"),
                        m.get("liquidity_dollars"), (m.get("title") or "")[:120]])
    log("open markets", len(om))


if __name__ == "__main__":
    try:
        main()
    finally:
        (OUT / "log.txt").write_text("\n".join(LOG))
