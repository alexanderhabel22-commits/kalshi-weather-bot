"""Summarise research_poly/fetch.py rows: P&L per contract for the favourite bid.

usage: python research_poly/analyze.py rows.csv [rows2.csv ...]
Maker fills pay no fee on Polymarket; taker entries pay 0.07*p*(1-p) per share.
"""
import csv
import math
import sys
from collections import defaultdict


def stats(xs):
    n = len(xs)
    if not n:
        return "n=0"
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / max(1, n - 1))
    return f"n={n:5d}  mean={m * 100:+5.2f}c  ±{1.96 * sd / math.sqrt(n) * 100:4.2f}c(95%)"


def main(paths):
    rows = []
    for p in paths:
        rows += list(csv.DictReader(open(p)))
    print(f"{len(rows)} windows")
    for lb in ("600", "300"):
        for kind in ("strict", "touch", "taker"):
            by = defaultdict(list)
            band = defaultdict(list)
            month = defaultdict(list)
            for r in rows:
                side = r[f"{kind}{lb}_side"]
                if not side:
                    continue
                px = float(r[f"{kind}{lb}_px"])
                pnl = (1.0 if side == r["winner"] else 0.0) - px
                if kind == "taker":
                    pnl -= 0.07 * px * (1 - px)
                by[r["coin"]].append(pnl)
                by["ALL"].append(pnl)
                band[f"{int(px * 100) // 5 * 5}c"].append(pnl)
                if r["coin"] == "btc":
                    import datetime as dt
                    month[dt.datetime.utcfromtimestamp(int(r["end"])).strftime("%Y-%m")].append(pnl)
            print(f"\n== last {int(lb) // 60} min, {kind} ==")
            for k in sorted(by):
                n_win = sum(1 for r in rows if r["coin"] == k) if k != "ALL" else len(rows)
                print(f"  {k:5s} {stats(by[k])}  fill rate {len(by[k]) / max(1, n_win):.0%}")
            if kind == "strict":
                for k in sorted(band):
                    print(f"    band {k}: {stats(band[k])}")
                for k in sorted(month):
                    print(f"    btc {k}: {stats(month[k])}")
    usd = defaultdict(list)
    for r in rows:
        usd[r["coin"]].append(float(r["usd_10m"]))
    print("\nmedian $ traded by takers in the last 10 min per window:")
    for k in sorted(usd):
        v = sorted(usd[k])
        print(f"  {k}: ${v[len(v) // 2]:,.0f}")


if __name__ == "__main__":
    main(sys.argv[1:])
