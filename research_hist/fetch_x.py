"""Cross-market consistency pull (read-only public data; never trades, never alerts).

For game-winner / spread series pairs (same league), sample settled games that have
both, and save every market of the game plus 1-minute yes bid/ask candles over the
last WINDOW_H hours before the game's markets closed.

Outputs (OUT dir): markets.csv.gz, candles.csv.gz, log.txt
usage: python research_hist/fetch_x.py <shard> <n_shards> <out dir>
"""
from __future__ import annotations

import csv
import gzip
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fetch as F  # noqa: E402  (shared HTTP helpers)

PAIRS = [("KXNBAGAME", "KXNBASPREAD"), ("KXNFLGAME", "KXNFLSPREAD"),
         ("KXMLBGAME", "KXMLBSPREAD"), ("KXNCAAFGAME", "KXNCAAFSPREAD"),
         ("KXNCAAMBGAME", "KXNCAAMBSPREAD"), ("KXWCGAME", "KXWCSPREAD"),
         ("KXWNBAGAME", "KXWNBASPREAD"), ("KXNHLGAME", "KXNHLSPREAD"),
         ("KXLALIGAGAME", "KXLALIGASPREAD"), ("KXUCLGAME", "KXUCLSPREAD"),
         ("KXEPLGAME", "KXEPLSPREAD"), ("KXMLSGAME", "KXMLSSPREAD")]
PER_LEAGUE = int(os.environ.get("PER_LEAGUE", "220"))
WINDOW_H = float(os.environ.get("WINDOW_H", "6"))
START = datetime(2025, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 11, 7, tzinfo=timezone.utc)


def raw_markets(series, lo, hi):
    out, seen = [], set()
    for path, extra in (("/markets", {"status": "settled"}), ("/historical/markets", {})):
        cur = None
        for _ in range(500):
            p = {"series_ticker": series, "limit": 1000, "min_close_ts": lo, "max_close_ts": hi,
                 **extra}
            if cur:
                p["cursor"] = cur
            js = F.get(path, p)
            if not js:
                break
            for m in js.get("markets") or []:
                if m.get("ticker") in seen or m.get("result") not in ("yes", "no"):
                    continue
                seen.add(m["ticker"])
                m["_hist"] = int(path.startswith("/historical"))
                out.append(m)
            cur = js.get("cursor")
            if not cur:
                break
    return out


def candles(m, series, start, end):
    rows = []
    hist = m["_hist"]
    base = (f"/historical/markets/{m['ticker']}/candlesticks" if hist
            else f"/series/{series}/markets/{m['ticker']}/candlesticks")
    a = start
    while a < end:
        b = min(end, a + 2000 * 60)
        js = F.get(base, {"start_ts": a, "end_ts": b, "period_interval": 1})
        if js is None and not hist:
            js = F.get(f"/historical/markets/{m['ticker']}/candlesticks",
                       {"start_ts": a, "end_ts": b, "period_interval": 1})
        for c in (js or {}).get("candlesticks") or []:
            yb, ya = c.get("yes_bid") or {}, c.get("yes_ask") or {}

            def d(o):
                v = F.f(o.get("close_dollars", o.get("close")))
                if v is not None and v > 1.5:
                    v /= 100
                return "" if v is None else round(v, 4)
            rows.append((m["ticker"], c.get("end_period_ts"), d(yb), d(ya),
                         F.f(c.get("volume_fp", c.get("volume"))) or 0))
        a = b
    return rows


def main():
    shard, n, out = int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    lo, hi = int(START.timestamp()), int(END.timestamp())
    rnd = random.Random(7 + shard)
    games = []
    for i, (gs, ss) in enumerate(PAIRS):
        if i % n != shard:
            continue
        g, s = raw_markets(gs, lo, hi), raw_markets(ss, lo, hi)
        by = {}
        for kind, lst in (("g", g), ("s", s)):
            for m in lst:
                suf = (m.get("event_ticker") or "").split("-", 1)[-1]
                by.setdefault(suf, {"g": [], "s": []})[kind].append(m)
        both = [(suf, v) for suf, v in by.items() if v["g"] and v["s"]]
        F.log(gs, "game mkts", len(g), "spread mkts", len(s), "games with both", len(both))
        pick = both if len(both) <= PER_LEAGUE else rnd.sample(both, PER_LEAGUE)
        games += [(gs, ss, suf, v) for suf, v in pick]
    F.log("games", len(games))

    def work(item):
        gs, ss, suf, v = item
        close = max(F.ts(m.get("close_time")) for m in v["g"])
        st = int(close - WINDOW_H * 3600)
        st -= st % 60
        rows = []
        for m in v["g"]:
            rows += candles(m, gs, st, close)
        for m in v["s"]:
            rows += candles(m, ss, st, close)
        return rows

    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(work, games))
    with gzip.open(out / "markets.csv.gz", "wt", newline="") as fm, \
            gzip.open(out / "candles.csv.gz", "wt", newline="") as fc:
        wm, wc = csv.writer(fm), csv.writer(fc)
        wm.writerow(["league", "kind", "game", "ticker", "event", "close_ts", "result",
                     "floor_strike", "yes_sub", "volume"])
        wc.writerow(["ticker", "ts", "yes_bid", "yes_ask", "volume"])
        for (gs, ss, suf, v), rows in zip(games, res):
            for kind in ("g", "s"):
                for m in v[kind]:
                    wm.writerow([gs, kind, suf, m["ticker"], m.get("event_ticker"),
                                 F.ts(m.get("close_time")), m.get("result"),
                                 m.get("floor_strike", ""),
                                 (m.get("yes_sub_title") or "")[:60],
                                 F.f(m.get("volume_fp", m.get("volume"))) or 0])
            for r in rows:
                wc.writerow(r)
    F.log("wrote", sum(len(r) for r in res), "candle rows")


if __name__ == "__main__":
    try:
        main()
    finally:
        Path(sys.argv[3]).mkdir(parents=True, exist_ok=True)
        (Path(sys.argv[3]) / "log.txt").write_text("\n".join(F.LOG) + "\n")
