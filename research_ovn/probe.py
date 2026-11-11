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
7. fast.jsonl.gz: 1-second KXBTC15M book + Coinbase spot + request round-trip times.
6. mm_books.jsonl.gz + mm_trades.jsonl.gz: order books (~every 60 s) and trade tapes for
   ~150 active markets across categories (plus the live 15-min BTC/silver/ETH windows) for
   MM_HOURS, to replay resting two-sided quotes with real queue positions.
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


MM_HOURS = float(os.environ.get("MM_HOURS", "3.5"))
MM_SERIES = ("KXBTC15M", "KXSILVER15M", "KXETH15M")
_mm_pick: list = []


def _f(m, *ks):
    for k in ks:
        v = m.get(k)
        if v not in (None, ""):
            try:
                v = float(v)
                return v / 100 if k in ("yes_bid", "yes_ask") else v
            except Exception:
                pass
    return None


def pick_mm(events):
    now = time.time()
    by = {}
    for e in events:
        cat = e.get("category") or "?"
        for m in e.get("markets") or []:
            try:
                ct = time.mktime(time.strptime(m["close_time"][:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
            except Exception:
                continue
            if ct < now + MM_HOURS * 3600 + 1800:
                continue
            v = _f(m, "volume_24h_fp", "volume_24h") or 0
            b = _f(m, "yes_bid_dollars", "yes_bid")
            a = _f(m, "yes_ask_dollars", "yes_ask")
            if v < 200 or b is None or a is None or b <= 0 or a >= 1:
                continue
            by.setdefault(cat, []).append((v, a - b, m["ticker"]))
    pick = []
    for cat, lst in by.items():
        lst.sort(reverse=True)
        pick += [t for _, _, t in lst[:10]]                      # busiest
        wide = sorted([x for x in lst[10:] if x[1] >= 0.04], reverse=True)
        pick += [t for _, _, t in wide[:6]]                      # busy-ish but wide spread
    random.seed(2)
    random.shuffle(pick)
    return pick[:170]


def _ct(m):
    try:
        return time.mktime(time.strptime(m["close_time"][:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except Exception:
        return 0.0


def current_market(series):
    """The open market of a rolling series that closes soonest in the future
    (the API's open list also holds stale windows, Nov 11 check)."""
    j = get(K + "/markets", {"series_ticker": series, "status": "open", "limit": 200})
    now = time.time()
    ms = [m for m in (j or {}).get("markets", []) if _ct(m) > now]
    return min(ms, key=_ct) if ms else None


def step_mm():
    while not _mm_pick and time.time() - T0 < 1800:
        time.sleep(10)
    static = list(_mm_pick)
    log("mm markets", len(static))
    fb, ft = gzw("mm_books.jsonl.gz"), gzw("mm_trades.jsonl.gz")
    last_ts = {}
    end = time.time() + MM_HOURS * 3600
    cyc = 0
    while time.time() < end and left() > 900:
        t0 = time.time()
        live = []
        for s in MM_SERIES:
            m = current_market(s)
            if m:
                live.append(m["ticker"])
        for tk in static + live:
            ob = get(K + f"/markets/{tk}/orderbook", {"depth": 30})
            if ob is not None:
                fb.write(json.dumps({"t": time.time(), "ticker": tk, "ob": ob}) + "\n")
            p = {"ticker": tk, "limit": 1000}
            if tk in last_ts:
                p["min_ts"] = int(last_ts[tk])
            else:
                p["min_ts"] = int(time.time() - 600)
            j = get(K + "/markets/trades", p)
            for tr in (j or {}).get("trades", []):
                ft.write(json.dumps(tr) + "\n")
            last_ts[tk] = time.time() - 5
        cyc += 1
        if cyc % 10 == 0:
            log("mm cycle", cyc, "secs", round(time.time() - t0))
            fb.flush()
            ft.flush()
    fb.close()
    ft.close()
    log("mm done", cyc)


def step_fast():
    """1-second loop: current KXBTC15M book + Coinbase BTC spot, and our own request round-trip
    times (how stale do resting Kalshi quotes get after a spot move?)."""
    ss = requests.Session()
    ss.headers["User-Agent"] = "Mozilla/5.0 (research)"
    f = gzw("fast.jsonl.gz")
    end = time.time() + MM_HOURS * 3600
    cur, cur_until, n = None, 0, 0
    while time.time() < end and left() > 900:
        t0 = time.time()
        if t0 > cur_until:
            m = current_market("KXBTC15M")
            if m:
                cur, cur_until = m["ticker"], _ct(m)
            else:
                cur_until = t0 + 30
        rec = {"t": t0, "ticker": cur}
        try:
            a = time.time()
            r = ss.get("https://api.exchange.coinbase.com/products/BTC-USD/ticker", timeout=5)
            rec["cb_rtt"] = time.time() - a
            jj = r.json()
            rec["cb"] = [jj.get("bid"), jj.get("ask"), jj.get("price"), jj.get("time")]
        except Exception as e:
            rec["cb_err"] = str(e)[:80]
        if cur:
            try:
                a = time.time()
                r = ss.get(K + f"/markets/{cur}/orderbook", params={"depth": 5}, timeout=5)
                rec["k_rtt"] = time.time() - a
                rec["ob"] = r.json() if r.status_code == 200 else r.status_code
            except Exception as e:
                rec["k_err"] = str(e)[:80]
        f.write(json.dumps(rec) + "\n")
        n += 1
        if n % 600 == 0:
            f.flush()
            log("fast", n)
        time.sleep(max(0.0, 1.0 - (time.time() - t0)))
    f.close()


def step_events(snap):
    f = gzw("events_open.jsonl.gz")
    cur, n = None, 0
    evs = []
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
            evs.append(e)
            n += 1
        cur = j.get("cursor")
        if not cur:
            break
    f.close()
    log("events snap", snap, n)
    if snap == 0:
        _mm_pick.extend(pick_mm(evs))


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
    mm = threading.Thread(target=step_mm, daemon=True)
    mm.start()
    fast = threading.Thread(target=step_fast, daemon=True)
    fast.start()
    names, mve = step_feed()
    th = threading.Thread(target=lambda: step_mve(mve), daemon=True)
    th.start()
    tick = step_accounts(names)
    step_results(tick)
    th.join(timeout=max(60, left() - 600))
    mm.join(timeout=max(60, left() - 300))
    log("DONE")
