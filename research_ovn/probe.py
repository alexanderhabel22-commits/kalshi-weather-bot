"""Overnight idea research (Nov 11, 2026). Read-only public data; never trades, never alerts.
usage: python research_ovn/probe.py OUT_DIR

1. incentives.json: every incentive program Kalshi lists (active + recent).
2. events_open.jsonl.gz: all open events with nested markets, 3 snapshots ~25 min apart
   (mutually-exclusive overround / underround check).
3. feed_names.json + accounts.jsonl.gz: named traders from the public trade feed, their
   lifetime metrics and up to TRADE_PAGES pages of their public trades
   (does an account's past record predict its next trades? fade the losers?).
4. mve_markets.jsonl.gz + mve_trades.jsonl.gz: settled combo (MVE) markets and a sample
   of their trade tapes (what do parlay sellers earn?).
5. results.jsonl.gz: results of every market ticker seen in step 3.
"""
from __future__ import annotations

import gzip
import json
import os
import random
import sys
import threading
import time
from pathlib import Path

import requests

K = "https://api.elections.kalshi.com/trade-api/v2"
SOC = "https://api.elections.kalshi.com/v1/social"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
OUT.mkdir(parents=True, exist_ok=True)
FEED_PAGES = int(os.environ.get("FEED_PAGES", "400"))
MAX_NAMES = int(os.environ.get("MAX_NAMES", "3000"))
TRADE_PAGES = int(os.environ.get("TRADE_PAGES", "10"))
MVE_MARKETS = int(os.environ.get("MVE_MARKETS", "60000"))
MVE_TAPES = int(os.environ.get("MVE_TAPES", "4000"))
BUDGET_S = float(os.environ.get("BUDGET_H", "4.5")) * 3600
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (research)"
_lock = threading.Lock()
_last = [0.0]
T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:6.0f}s]", *a, flush=True)


def get(url, params=None):
    for i in range(6):
        with _lock:
            w = _last[0] + 0.08 - time.time()
            if w > 0:
                time.sleep(w)
            _last[0] = time.time()
        try:
            r = S.get(url, params=params, timeout=30)
        except Exception as e:
            log("ERR", url, e)
            time.sleep(2 + i * 2)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 + i * 3)
            continue
        if r.status_code != 200:
            log("HTTP", r.status_code, url, params, r.text[:200])
            return None
        try:
            return r.json()
        except Exception:
            return None
    return None


def left():
    return BUDGET_S - (time.time() - T0)


def gzw(name):
    return gzip.open(OUT / name, "at")


def step_incentives():
    out = {}
    for st in ("active", "upcoming", "closed", "all"):
        rows, cur = [], None
        for _ in range(50):
            p = {"status": st, "limit": 1000}
            if cur:
                p["cursor"] = cur
            j = get(K + "/incentive_programs", p)
            if not j:
                break
            rows += j.get("incentive_programs", []) or []
            cur = j.get("next_cursor") or j.get("cursor")
            if not cur:
                break
        out[st] = rows
        log("incentives", st, len(rows))
    json.dump(out, open(OUT / "incentives.json", "w"))


def step_events(snap):
    f = gzw("events_open.jsonl.gz")
    cur, n = None, 0
    for _ in range(400):
        p = {"status": "open", "with_nested_markets": "true", "limit": 200}
        if cur:
            p["cursor"] = cur
        j = get(K + "/events", p)
        if not j:
            break
        for e in j.get("events", []):
            e["_snap"] = snap
            e["_t"] = time.time()
            f.write(json.dumps(e) + "\n")
            n += 1
        cur = j.get("cursor")
        if not cur:
            break
    f.close()
    log("events snap", snap, n)


def step_feed():
    names, mve = {}, {}
    cur = None
    for page in range(FEED_PAGES):
        p = {"limit": 100}
        if cur:
            p["cursor"] = cur
        j = get(SOC + "/trades", p)
        if not j:
            break
        for t in j.get("trades", []):
            tk = t.get("ticker", "")
            if tk.startswith("KXMVE"):
                mve[tk.split("-")[0]] = mve.get(tk.split("-")[0], 0) + 1
            for side in ("maker", "taker"):
                nm = t.get(side + "_nickname")
                if nm:
                    names[nm] = names.get(nm, 0) + 1
        cur = j.get("cursor")
        if not cur:
            break
        if page % 100 == 0:
            log("feed page", page, len(names))
    json.dump({"names": names, "mve_series": mve}, open(OUT / "feed_names.json", "w"))
    log("feed names", len(names), "mve series", mve)
    return names, mve


def step_accounts(names):
    f = gzw("accounts.jsonl.gz")
    tickers = set()
    order = sorted(names, key=lambda n: -names[n])[:MAX_NAMES]
    for i, n in enumerate(order):
        if left() < 5400:
            log("accounts budget stop", i)
            break
        rec = {"nickname": n, "metrics": get(SOC + "/profile/metrics", {"nickname": n}),
               "profile": get(SOC + "/profile", {"nickname": n}), "trades": []}
        cur = None
        for _ in range(TRADE_PAGES):
            p = {"nickname": n, "limit": 100}
            if cur:
                p["cursor"] = cur
            j = get(SOC + "/trades", p)
            if not j:
                break
            tr = j.get("trades", []) or []
            rec["trades"] += tr
            for t in tr:
                tickers.add(t.get("ticker"))
            cur = j.get("cursor")
            if not cur or not tr:
                break
        f.write(json.dumps(rec) + "\n")
        if i % 200 == 0:
            log("accounts", i, len(tickers))
            f.flush()
    f.close()
    return tickers


def step_results(tickers):
    f = gzw("results.jsonl.gz")
    tl = sorted(t for t in tickers if t)
    for i in range(0, len(tl), 100):
        if left() < 1800:
            log("results budget stop", i)
            break
        j = get(K + "/markets", {"tickers": ",".join(tl[i:i + 100]), "limit": 100})
        for m in (j or {}).get("markets", []):
            f.write(json.dumps({k: m.get(k) for k in ("ticker", "event_ticker", "status", "result",
                     "close_time", "expiration_time", "last_price_dollars", "volume_fp", "volume",
                     "title", "yes_sub_title")}) + "\n")
        if i % 5000 == 0:
            log("results", i, len(tl))
    f.close()


def step_mve(mve_series):
    f = gzw("mve_markets.jsonl.gz")
    mk = []
    series = sorted(mve_series, key=lambda s: -mve_series[s]) or ["KXMVECROSSCATEGORY"]
    per = max(2000, MVE_MARKETS // max(1, len(series)))
    for s in series:
        cur, n = None, 0
        while n < per:
            p = {"series_ticker": s, "status": "settled", "limit": 1000}
            if cur:
                p["cursor"] = cur
            j = get(K + "/markets", p)
            if not j:
                break
            for m in j.get("markets", []):
                slim = {k: m.get(k) for k in ("ticker", "event_ticker", "result", "close_time",
                        "last_price_dollars", "volume_fp", "volume", "title", "open_time",
                        "previous_price_dollars", "settlement_value_dollars")}
                slim["series"] = s
                slim["legs"] = len(m.get("mve_selected_legs") or [])
                f.write(json.dumps(slim) + "\n")
                mk.append(slim)
                n += 1
            cur = j.get("cursor")
            if not cur:
                break
        log("mve series", s, n)
    f.close()
    random.seed(1)
    cand = [m for m in mk if m.get("result") in ("yes", "no")]

    def vol(m):
        try:
            return float(m.get("volume_fp") or m.get("volume") or 0)
        except Exception:
            return 0.0
    cand = [m for m in cand if vol(m) > 0]
    random.shuffle(cand)
    f = gzw("mve_trades.jsonl.gz")
    for i, m in enumerate(cand[:MVE_TAPES]):
        if left() < 600:
            log("mve tapes budget stop", i)
            break
        cur = None
        for _ in range(3):
            p = {"ticker": m["ticker"], "limit": 1000}
            if cur:
                p["cursor"] = cur
            j = get(K + "/markets/trades", p)
            if not j:
                break
            for t in j.get("trades", []):
                f.write(json.dumps(t) + "\n")
            cur = j.get("cursor")
            if not cur:
                break
        if i % 500 == 0:
            log("mve tapes", i)
    f.close()


if __name__ == "__main__":
    step_incentives()
    def snaps():
        for k in range(3):
            step_events(k)
            time.sleep(1500)
    threading.Thread(target=snaps, daemon=True).start()
    names, mve = step_feed()
    th = threading.Thread(target=lambda: step_mve(mve), daemon=True)
    th.start()
    tick = step_accounts(names)
    step_results(tick)
    th.join(timeout=max(60, left() - 300))
    log("DONE")
