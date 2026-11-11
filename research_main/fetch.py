"""Main-bot strategy audit pull (Nov 11, 2026). Read-only public data; never trades.

For the three non-weather strategies of the main bot (Mentions NO, big-event
favorites, Fed/CPI fade) this pulls, for every settled market in each strategy's
universe: the market row, HOURLY bid/ask candles for the days before the close
(what the bot's 30-minute scan would have seen) and the raw trade tape in the
price range a resting order of ours could fill in. That allows a bot-faithful
replay (place at best bid + 1c at scan time, rest, fill when a later trade
prints at our price) instead of scoring "the average maker fill".

  python research_main/fetch.py OUT [section ...]     sections: macro mentions favorites
Writes OUT/<section>_markets.csv.gz, _candles.csv.gz, _trades.csv.gz and log.txt.
"""
from __future__ import annotations

import csv
import gzip
import json
import os
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
SECTIONS = sys.argv[2:] or ["macro", "mentions", "favorites"]
S = requests.Session()
S.headers["User-Agent"] = "kalshi-research/0.1"
_lock = threading.Lock()
_last = [0.0]
T0 = time.time()
LOG: list[str] = []
_errs = [0]
MACRO = ["KXFED", "KXFEDDECISION", "KXCPI", "KXCPIYOY", "KXCPICORE", "KXPCECORE"]
FAV_CATS = ["Elections", "Politics", "Entertainment"]
# per section: first close date, candle window (days before close), yes-price ranges of
# trades worth keeping, minimum market volume, time budget in minutes
CFG = {
    "macro": dict(start="2025-01-01", days=22, keep=[(0.02, 0.33)], min_vol=100, budget=40),
    "mentions": dict(start="2026-03-01", days=8, keep=[(0.04, 0.36)], min_vol=100, budget=150),
    "favorites": dict(start="2025-01-01", days=8, keep=[(0.02, 0.19), (0.81, 0.98)], min_vol=100,
                      budget=90),
}
FAV_MIN_SERIES = 1_000_000
FAV_MIN_EVENT = 300_000


def log(*a):
    msg = f"[{time.time() - T0:6.0f}s] " + " ".join(str(x) for x in a)
    print(msg, flush=True)
    LOG.append(msg)
    (OUT / "log.txt").write_text("\n".join(LOG) + "\n")


def get(path, params=None):
    for i in range(7):
        with _lock:
            wait = _last[0] + 0.06 - time.time()
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
            if r.status_code != 404 and _errs[0] < 30:
                _errs[0] += 1
                log("HTTP", r.status_code, path, params, r.text[:200])
            return None
        try:
            return r.json()
        except ValueError:
            return None
    return None


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def ts(s):
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def day(s):
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())


def list_markets(series, lo):
    """Settled markets of one series closing at/after lo (live + historical endpoints)."""
    out, seen = [], set()
    for path, extra in (("/markets", {"status": "settled"}), ("/historical/markets", {})):
        cur = None
        for _ in range(400):
            p = {"series_ticker": series, "limit": 1000, "min_close_ts": lo, **extra}
            if cur:
                p["cursor"] = cur
            js = get(path, p)
            if not js:
                break
            for m in js.get("markets") or []:
                tk = m.get("ticker")
                ct = ts(m.get("close_time"))
                if not tk or tk in seen or m.get("result") not in ("yes", "no") or not ct or ct < lo:
                    continue
                seen.add(tk)
                out.append(dict(
                    ticker=tk, series=series, event=m.get("event_ticker") or tk.rsplit("-", 1)[0],
                    hist=int(path.startswith("/historical")), open_ts=ts(m.get("open_time")) or 0,
                    close_ts=ct, occ_ts=ts(m.get("occurrence_datetime")) or 0,
                    exp_ts=ts(m.get("expected_expiration_time")) or 0, result=m.get("result"),
                    volume=f(m.get("volume_fp", m.get("volume"))) or 0.0,
                    early=int(bool(m.get("can_close_early"))),
                    strike=m.get("floor_strike") if m.get("floor_strike") is not None else "",
                    title=(m.get("title") or "")[:140], sub=(m.get("yes_sub_title") or "")[:80]))
            cur = js.get("cursor")
            if not cur:
                break
    return out


def px(d, key, which="close"):
    """candle sub-dict -> price as the API gives it (dollars string or cents int)."""
    x = d.get(key) or {}
    v = f(x.get(which + "_dollars"))
    if v is not None:
        return v
    v = f(x.get(which))
    if v is None:
        return None
    return v / 100 if v > 1.5 else v


_shown = [0]
_stop = [0.0]


def start_of(m, days):
    """Start of the window we replay: `days` before the event (or the close)."""
    ref = min(x for x in (m["occ_ts"], m["close_ts"]) if x) if m["occ_ts"] else m["close_ts"]
    return max(m["open_ts"] - 3600, ref - days * 86400)


def candles(m, days):
    if time.time() > _stop[0]:
        return None
    p = {"start_ts": start_of(m, days), "end_ts": m["close_ts"] + 3600, "period_interval": 60}
    paths = [f"/series/{m['series']}/markets/{m['ticker']}/candlesticks",
             f"/historical/markets/{m['ticker']}/candlesticks"]
    if m["hist"]:
        paths.reverse()
    for path in paths:
        cs = (get(path, p) or {}).get("candlesticks") or []
        if cs:
            if _shown[0] < 4:
                _shown[0] += 1
                log("sample candle", path, json.dumps(cs[len(cs) // 2])[:700])
            return [(m["ticker"], c.get("end_period_ts"), px(c, "yes_bid"), px(c, "yes_ask"),
                     px(c, "yes_bid", "low"), px(c, "yes_bid", "high"), px(c, "yes_ask", "low"),
                     px(c, "yes_ask", "high"), px(c, "price"),
                     f(c.get("volume_fp", c.get("volume"))) or 0.0) for c in cs]
    return []


def trades(m, days, keep):
    if time.time() > _stop[0]:
        return None
    lo = start_of(m, days)
    paths = ["/markets/trades", "/historical/trades"]
    if m["hist"]:
        paths.reverse()
    for path in paths:
        out, cur, got = [], None, False
        for _ in range(300):
            p = {"ticker": m["ticker"], "limit": 1000, "min_ts": lo}
            if cur:
                p["cursor"] = cur
            js = get(path, p)
            tr = (js or {}).get("trades") or []
            if not tr:
                break
            got = True
            oldest = None
            for t in tr:
                yp, c = f(t.get("yes_price_dollars", t.get("yes_price"))), f(
                    t.get("count_fp", t.get("count")))
                t0 = ts(t.get("created_time"))
                if yp is None or c is None or t0 is None:
                    continue
                if yp > 1.5:
                    yp /= 100
                oldest = t0 if oldest is None else min(oldest, t0)
                if t0 >= lo and any(a <= yp <= b for a, b in keep):
                    out.append((m["ticker"], t0, round(yp, 4), c, t.get("taker_side")))
            cur = (js or {}).get("cursor")
            if not cur or (oldest is not None and oldest < lo):
                break
        if got:
            return out
    return []


def pull(name, ms):
    cfg = CFG[name]
    ms.sort(key=lambda m: -m["close_ts"])        # newest first: a time cut drops the oldest
    with gzip.open(OUT / f"{name}_markets.csv.gz", "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(ms[0].keys()) if ms else ["ticker"])
        w.writeheader()
        w.writerows(ms)
    _stop[0] = time.time() + cfg["budget"] * 60 * float(os.environ.get("BUDGET_X", "1"))
    nc = nt = cut = 0
    with gzip.open(OUT / f"{name}_candles.csv.gz", "wt", newline="") as fc, \
            gzip.open(OUT / f"{name}_trades.csv.gz", "wt", newline="") as ft:
        wc, wt = csv.writer(fc), csv.writer(ft)
        wc.writerow(["ticker", "t", "yb", "ya", "yb_lo", "yb_hi", "ya_lo", "ya_hi", "last", "vol"])
        wt.writerow(["ticker", "t", "yes_price", "count", "taker_side"])

        def one(m):
            return candles(m, cfg["days"]), trades(m, cfg["days"], cfg["keep"])

        with ThreadPoolExecutor(8) as ex:
            for i, (cs, tr) in enumerate(ex.map(one, ms)):
                if cs is None or tr is None:
                    cut += 1
                wc.writerows(cs or [])
                wt.writerows(tr or [])
                nc += bool(cs)
                nt += bool(tr)
                if i % 2000 == 0:
                    log(name, i, "of", len(ms), "with candles", nc, "with trades", nt)
    log(name, "done: markets", len(ms), "with candles", nc, "with trades", nt, "cut by time", cut)


def macro():
    lo = day(CFG["macro"]["start"])
    ms = [m for s in MACRO for m in list_markets(s, lo)]
    ms = [m for m in ms if m["volume"] >= CFG["macro"]["min_vol"]]
    log("macro markets", len(ms))
    pull("macro", ms)


def mentions():
    js = get("/series", {"category": "Mentions"}) or {}
    series = sorted(s["ticker"] for s in js.get("series") or [] if s.get("category") == "Mentions")
    log("mentions series", len(series))
    lo = day(CFG["mentions"]["start"])
    with ThreadPoolExecutor(6) as ex:
        ms = [m for lst in ex.map(lambda s: list_markets(s, lo), series) for m in lst]
    log("mentions settled markets", len(ms))
    ms = [m for m in ms if m["volume"] >= CFG["mentions"]["min_vol"]]
    log("mentions markets with volume", len(ms))
    pull("mentions", ms)


def favorites():
    series = {}
    for cat in FAV_CATS:
        js = get("/series", {"category": cat, "include_volume": "true"}) or {}
        for s in js.get("series") or []:
            if s.get("category") == cat and (f(s.get("volume_fp", s.get("volume"))) or 0) >= FAV_MIN_SERIES:
                series[s["ticker"]] = cat
    log("favorites big series", len(series))
    lo = day(CFG["favorites"]["start"])
    with ThreadPoolExecutor(6) as ex:
        ms = [m for lst in ex.map(lambda s: list_markets(s, lo), sorted(series)) for m in lst]
    ev: dict = {}
    for m in ms:
        ev[m["event"]] = ev.get(m["event"], 0.0) + m["volume"]
    for m in ms:
        m["cat"] = series[m["series"]]
        m["event_volume"] = round(ev[m["event"]])
    log("favorites settled markets", len(ms), "events", len(ev),
        "events over floor", sum(v >= FAV_MIN_EVENT for v in ev.values()))
    ms = [m for m in ms if m["event_volume"] >= FAV_MIN_EVENT and m["volume"] >= CFG["favorites"]["min_vol"]]
    log("favorites markets kept", len(ms))
    pull("favorites", ms)


if __name__ == "__main__":
    for name in SECTIONS:
        try:
            {"macro": macro, "mentions": mentions, "favorites": favorites}[name]()
        except Exception as e:      # keep going with the next section
            log("FAILED", name, repr(e))
    log("all done")
