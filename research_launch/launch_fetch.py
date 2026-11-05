"""New-series launch backtest: data fetch (read-only public Kalshi data; never trades).

Question: are new Kalshi series mispriced in their first weeks, before market
makers arrive? Steps:
 1. Series catalog.
 2. For every recurring series (not one_off/annual): all its markets from the live
    and /historical endpoints -> first open time = launch date.
 3. Series launched Jan 15 - Oct 5, 2026: sample settled markets by age since
    launch (0-14d, 14-28d, 28-56d, 56-112d) and pull their trades, aggregated per
    (ticker, taker side, price).
Writes into argv[1]: series.csv, launches.csv, markets.csv.gz, trades_agg.csv.gz, log.txt
"""
from __future__ import annotations

import csv
import gzip
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

K = "https://api.elections.kalshi.com/trade-api/v2"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
OUT.mkdir(parents=True, exist_ok=True)
SKIP_FREQ = {"one_off", "annual"}
LAUNCH_MIN = datetime(2026, 1, 15, tzinfo=timezone.utc)
LAUNCH_MAX = datetime(2026, 10, 5, tzinfo=timezone.utc)
BUCKETS = [(0, 14), (14, 28), (28, 56), (56, 112)]
PER_BUCKET = 15
MARKET_PAGES = 8
MARKET_PAGES_FAST = 40  # hourly / 15-min series have thousands of markets
TRADE_PAGES = 10
BUDGET_S = 4.3 * 3600
S = requests.Session()
S.headers["User-Agent"] = "kalshi-research/0.1"
_lock = threading.Lock()
_last = [0.0]
T0 = time.time()
LOG: list[str] = []
META: dict = {}


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


def paginate(path, key, params, max_pages):
    out, cur, capped = [], None, False
    for i in range(max_pages):
        p = dict(params)
        if cur:
            p["cursor"] = cur
        js = get(path, p)
        if not js:
            break
        out += js.get(key) or []
        cur = js.get("cursor")
        if not cur:
            break
        if i == max_pages - 1:
            capped = True
    return out, capped


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def ts(s):
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def series_markets(s):
    rows, capped = {}, False
    pages = MARKET_PAGES_FAST if META.get(s, {}).get("frequency") in ("hourly", "fifteen_min") \
        else MARKET_PAGES
    for path, hist in (("/historical/markets", 1), ("/markets", 0)):
        ms, c = paginate(path, "markets", {"series_ticker": s, "limit": 1000}, pages)
        capped |= c
        for m in ms:
            tk = m.get("ticker")
            if not tk or tk in rows:
                continue
            rows[tk] = {"ticker": tk, "series": s, "event": m.get("event_ticker"),
                        "open": m.get("open_time"), "close": m.get("close_time"),
                        "status": m.get("status"), "result": m.get("result"),
                        "volume": f(m.get("volume_fp", m.get("volume"))) or 0.0,
                        "hist": hist}
    return list(rows.values()), capped


def trades(r):
    out, cur = {}, None
    path = "/historical/trades" if r["hist"] else "/markets/trades"
    n = 0
    for _ in range(TRADE_PAGES):
        p = {"ticker": r["ticker"], "limit": 1000}
        if cur:
            p["cursor"] = cur
        js = get(path, p)
        if not js:
            break
        for t in js.get("trades") or []:
            try:
                k = (t.get("taker_side"), round(float(t.get("yes_price_dollars", t.get("yes_price"))), 2))
                c = float(t.get("count_fp", t.get("count")))
            except (TypeError, ValueError):
                continue
            out[k] = out.get(k, 0.0) + c
            n += 1
        cur = js.get("cursor")
        if not cur:
            break
    return r["ticker"], out, n, bool(cur)


def main():
    js = get("/series", {"include_volume": "true"}) or {}
    series = js.get("series") or []
    with open(OUT / "series.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["series", "category", "title", "frequency", "fee_type", "fee_multiplier",
                    "volume", "tags"])
        for s in series:
            w.writerow([s.get("ticker"), s.get("category"), s.get("title"), s.get("frequency"),
                        s.get("fee_type"), s.get("fee_multiplier"),
                        s.get("volume_fp", s.get("volume")), "|".join(s.get("tags") or [])])
    meta = {s["ticker"]: s for s in series if s.get("ticker")}
    META.update(meta)
    todo = [t for t, s in meta.items() if (s.get("frequency") or "") not in SKIP_FREQ]
    log("series", len(series), "recurring", len(todo))

    launches, cand_markets = [], []
    with ThreadPoolExecutor(6) as ex:
        for i, (s, (ms, capped)) in enumerate(zip(todo, ex.map(series_markets, todo))):
            opens = [ts(m["open"]) for m in ms if ts(m["open"])]
            first = min(opens) if opens else None
            last = max(opens) if opens else None
            n_settled = sum(1 for m in ms if m["result"] in ("yes", "no"))
            launches.append({"series": s, "category": meta[s].get("category"),
                             "frequency": meta[s].get("frequency"), "title": meta[s].get("title"),
                             "n_markets": len(ms), "n_settled": n_settled,
                             "n_hist": sum(m["hist"] for m in ms), "capped": int(capped),
                             "first_open": first.isoformat() if first else "",
                             "last_open": last.isoformat() if last else "",
                             "volume": round(sum(m["volume"] for m in ms), 2)})
            if first and LAUNCH_MIN <= first <= LAUNCH_MAX and not capped and n_settled >= 8:
                for m in ms:
                    m["age_days"] = round((ts(m["open"]) - first).total_seconds() / 86400, 3) \
                        if ts(m["open"]) else ""
                cand_markets += ms
            if i % 500 == 0:
                log("launch scan", i, "/", len(todo), "candidate markets", len(cand_markets))
    with open(OUT / "launches.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(launches[0]))
        w.writeheader()
        w.writerows(launches)
    with gzip.open(OUT / "markets.csv.gz", "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["ticker", "series", "event", "open", "close", "status",
                                           "result", "volume", "hist", "age_days"])
        w.writeheader()
        w.writerows(cand_markets)
    log("launch scan done; candidate series",
        len({m["series"] for m in cand_markets}), "markets", len(cand_markets))

    # sample settled, traded markets per series x age bucket
    rnd = random.Random(11)
    by = {}
    for m in cand_markets:
        if m["result"] not in ("yes", "no") or m["volume"] < 5 or m["age_days"] == "":
            continue
        for lo, hi in BUCKETS:
            if lo <= m["age_days"] < hi:
                by.setdefault((m["series"], lo), []).append(m)
    sample = []
    for lst in by.values():
        sample += rnd.sample(lst, min(PER_BUCKET, len(lst)))
    rnd.shuffle(sample)  # so a budget cut-off doesn't drop whole series/buckets
    log("trade sample markets", len(sample))
    rows, nt = [], 0
    with ThreadPoolExecutor(6) as ex:
        futs = []
        for r in sample:
            futs.append(ex.submit(trades, r))
        for i, fu in enumerate(futs):
            if time.time() - T0 > BUDGET_S:
                log("budget hit at", i)
                ex.shutdown(cancel_futures=True)
                break
            tk, agg, n, trunc = fu.result()
            nt += n
            for (side, yp), c in agg.items():
                rows.append([tk, side, yp, round(c, 2), int(trunc)])
            if i % 2000 == 0:
                log("trades", i, "/", len(sample), "trades so far", nt)
    with gzip.open(OUT / "trades_agg.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "taker_side", "yes_price", "count", "truncated"])
        w.writerows(rows)
    log("done trades", nt)


if __name__ == "__main__":
    try:
        main()
    finally:
        (OUT / "log.txt").write_text("\n".join(LOG))
