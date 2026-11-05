"""Stage B: Kalshi pre-game price history for matched game markets.

Reads research_sports/matched.csv.gz (ticker, series, hist, start_ts) and pulls
candlesticks: 1-minute bars for the 90 minutes before the scheduled start and
hourly bars for the 30 hours before. Read-only public data; never trades.
Writes candles.csv.gz into argv[1].
"""
from __future__ import annotations

import csv
import gzip
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from kalshi_api import LOG, get, log  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
OUT.mkdir(parents=True, exist_ok=True)


def d(x, k):
    v = (x or {}).get(k) or {}
    return v.get("close_dollars", v.get("close"))


def candles(r):
    tk, s, start = r["ticker"], r["series"], int(r["start_ts"])
    out = []
    for kind, interval, lo, hi in (("m", 1, start - 5400, start + 60), ("h", 60, start - 30 * 3600, start)):
        p = {"start_ts": lo, "end_ts": hi, "period_interval": interval}
        paths = [f"/series/{s}/markets/{tk}/candlesticks", f"/historical/markets/{tk}/candlesticks"]
        if r["hist"] == "1":
            paths.reverse()
        js = None
        for path in paths:
            js = get(path, p, quiet=True)
            if js and js.get("candlesticks"):
                break
        for c in (js or {}).get("candlesticks") or []:
            out.append([tk, kind, c.get("end_period_ts"), d(c, "yes_bid"), d(c, "yes_ask"),
                        d(c, "price"), c.get("volume_fp", c.get("volume"))])
    return out


def main():
    src = Path(__file__).parent / "matched.csv.gz"
    rows = [r for r in csv.DictReader(gzip.open(src, "rt")) if r["hist"] == "1"]  # live ones done
    log("markets", len(rows))
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(candles, rows))
    with gzip.open(OUT / "candles.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "kind", "end_ts", "yes_bid", "yes_ask", "price", "volume"])
        for x in res:
            w.writerows(x)
    log("candles", sum(map(len, res)), "markets with candles", sum(1 for x in res if x))


if __name__ == "__main__":
    try:
        main()
    finally:
        (OUT / "log.txt").write_text("\n".join(LOG))
