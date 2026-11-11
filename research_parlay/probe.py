"""Parlay (MVE combo) deep pull, Nov 11 2026. Read-only public data; never trades, never alerts.
usage: python research_parlay/probe.py OUT_DIR

Week by week over the last WEEKS weeks, for every combo series:
1. markets.jsonl.gz: settled combo markets with their legs (event, market, side), open/close times.
2. trades.jsonl.gz: trade tapes for a sample of those markets (TAPES_PER_WEEK per series-week).
3. legs.jsonl.gz: results + category of every leg event seen in the tape sample.
Older weeks fall back to the /historical endpoints when the live ones return nothing.
"""
from __future__ import annotations

import gzip
import json
import os
import random
import sys
import time
from pathlib import Path

import requests

K = "https://api.elections.kalshi.com/trade-api/v2"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
OUT.mkdir(parents=True, exist_ok=True)
WEEKS = int(os.environ.get("WEEKS", "16"))
PAGES = int(os.environ.get("PAGES_PER_WEEK", "6"))          # x1000 markets per series-week
TAPES = int(os.environ.get("TAPES_PER_WEEK", "500"))
BUDGET_S = float(os.environ.get("BUDGET_H", "5.0")) * 3600
SERIES = ["KXMVECROSSCATEGORY", "KXMVESPORTSMULTIGAMEEXTENDED", "KXMVEMULTIGAMEEXTENDED",
          "KXMVESPORTSMULTIGAME", "KXMVENFLMULTIGAME", "KXMVENBAMULTIGAME", "KXMVECBBMULTIGAME",
          "KXMVENFLSINGLEGAME", "KXMVENBASINGLEGAME", "KXMVECFBMULTIGAME", "KXMVENHLMULTIGAME"]
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (research)"
_last = [0.0]
T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:6.0f}s]", *a, flush=True)


def get(path, params=None):
    for i in range(6):
        w = _last[0] + 0.08 - time.time()
        if w > 0:
            time.sleep(w)
        _last[0] = time.time()
        try:
            r = S.get(K + path, params=params, timeout=30)
        except Exception as e:
            log("ERR", path, e)
            time.sleep(2 + i * 2)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 + i * 3)
            continue
        if r.status_code != 200:
            log("HTTP", r.status_code, path, params, r.text[:200])
            return None
        try:
            return r.json()
        except Exception:
            return None
    return None


def left():
    return BUDGET_S - (time.time() - T0)


def series_list():
    """Combo series Kalshi lists (falls back to the known names)."""
    j = get("/series", {"limit": 1000}) or {}
    found = [s.get("ticker") for s in j.get("series") or [] if str(s.get("ticker", "")).startswith("KXMVE")]
    out = list(dict.fromkeys(SERIES + found))
    log("series", out)
    return out


def pull_week(s, lo, hi, fm):
    rows = []
    for base in ("/markets", "/historical/markets"):
        cur = None
        for _ in range(PAGES):
            p = {"series_ticker": s, "status": "settled", "limit": 1000, "min_close_ts": lo, "max_close_ts": hi}
            if base != "/markets":
                p.pop("status")
            if cur:
                p["cursor"] = cur
            j = get(base, p)
            if not j:
                break
            for m in j.get("markets", []):
                if m.get("result") not in ("yes", "no"):
                    continue
                slim = {k: m.get(k) for k in ("ticker", "event_ticker", "result", "close_time", "open_time",
                        "last_price_dollars", "volume_fp", "volume", "settlement_ts", "expected_expiration_time")}
                slim["series"] = s
                slim["legs"] = [{"e": lg.get("event_ticker"), "m": lg.get("market_ticker"), "s": lg.get("side")}
                                for lg in m.get("mve_selected_legs") or []]
                slim["src"] = base
                fm.write(json.dumps(slim) + "\n")
                rows.append(slim)
            cur = j.get("cursor")
            if not cur:
                break
        if rows:
            break
    return rows


def vol(m):
    try:
        return float(m.get("volume_fp") or m.get("volume") or 0)
    except Exception:
        return 0.0


def tape(m, ft):
    n = 0
    for base in ("/markets/trades", "/historical/trades"):
        cur = None
        for _ in range(3):
            p = {"ticker": m["ticker"], "limit": 1000}
            if cur:
                p["cursor"] = cur
            j = get(base, p)
            if not j:
                break
            for t in j.get("trades", []):
                ft.write(json.dumps(t) + "\n")
                n += 1
            cur = j.get("cursor")
            if not cur:
                break
        if n:
            return n
    return n


if __name__ == "__main__":
    random.seed(7)
    now = int(time.time())
    fm = gzip.open(OUT / "markets.jsonl.gz", "at")
    ft = gzip.open(OUT / "trades.jsonl.gz", "at")
    legs_seen = set()
    stats = []
    series = series_list()
    for w in range(WEEKS):
        hi, lo = now - w * 7 * 86400, now - (w + 1) * 7 * 86400
        for s in series:
            if left() < 1800:
                break
            rows = pull_week(s, lo, hi, fm)
            cand = [m for m in rows if vol(m) > 0]
            random.shuffle(cand)
            nt = 0
            for m in cand[:TAPES]:
                if left() < 1800:
                    break
                if tape(m, ft):
                    nt += 1
                    legs_seen.update(lg["e"] for lg in m["legs"] if lg.get("e"))
            stats.append({"week": w, "series": s, "markets": len(rows), "with_volume": len(cand), "tapes": nt})
            log("week", w, s, len(rows), len(cand), nt)
    fm.close()
    ft.close()
    fl = gzip.open(OUT / "legs.jsonl.gz", "at")
    for i, ev in enumerate(sorted(legs_seen)):
        if left() < 300:
            log("legs budget stop", i, len(legs_seen))
            break
        j = get(f"/events/{ev}", {"with_nested_markets": "true"}) or {}
        e = j.get("event") or {}
        mk = j.get("markets") or e.get("markets") or []
        fl.write(json.dumps({"event": ev, "category": e.get("category"), "series": e.get("series_ticker"),
                             "markets": [{"t": m.get("ticker"), "r": m.get("result"), "close": m.get("close_time")}
                                         for m in mk]}) + "\n")
    fl.close()
    json.dump(stats, open(OUT / "stats.json", "w"), indent=1)
    log("DONE")
