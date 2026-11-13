"""Score the exact live rules on the coverage-sweep data (Nov 13, 2026).

  python research_cover/analyze_cover.py <run dir> macro|favorites
Uses research_main/replay.py unchanged (the live rule), keeps the orders the rule
would place (kind == live), fills on a later print at our price (touch) and the
pessimistic back-of-queue case (through). One order = one unit; 1c maker fee
charged on every fill (pessimistic; the true 1-contract fee is 0-1c).
± = one standard error with all orders on one event counted together.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "research_main"))
import replay as R  # noqa: E402

FEE = 1.0


def clustered(df: pd.DataFrame, col: str = "net"):
    if len(df) == 0:
        return np.nan, np.nan, 0, 0
    g = df.groupby("event")[col].agg(["sum", "count"])
    n, k = g["count"].sum(), len(g)
    mu = g["sum"].sum() / n
    if k < 2:
        return mu, np.nan, n, k
    resid = g["sum"] - mu * g["count"]
    se = np.sqrt((resid ** 2).sum() * k / (k - 1)) / n
    return mu, se, n, k


def fmt(df, col="net"):
    mu, se, n, k = clustered(df, col)
    return f"{mu:+6.1f}c ±{se:4.1f} ({n} fills, {k} events)" if n else "no fills"


def main(run, sec):
    m, c, t = R.load(run, sec)
    df = R.replay(m, c, t, R.RULES[sec])
    df.to_pickle(f"{run}/{sec}_replay.pkl")
    live = df[df.kind == "live"].copy()
    live["filled"] = np.isfinite(live.touch)
    live["filled_thru"] = np.isfinite(live.through)
    f = live[live.filled].copy()
    f["net"] = f.pnl - FEE
    ft = live[live.filled_thru].copy()
    ft["net"] = ft.pnl - FEE
    f["month"] = pd.to_datetime(f.close_ts, unit="s").dt.strftime("%Y-%m")
    cut = f.close_ts.max() - 90 * 86400
    key = "cat" if sec == "favorites" else "series"
    print(f"## {sec}: {len(live)} orders placed, {live.filled.mean():.0%} filled")
    print(f"ALL           {fmt(f)}   back-of-queue {fmt(ft)}   last 90d {fmt(f[f.close_ts >= cut])}")
    print()
    rows = []
    for s, g in f.groupby(key if sec == "macro" else "series"):
        mu, se, n, k = clustered(g)
        mu90, se90, n90, _ = clustered(g[g.close_ts >= cut])
        mt, _, _, _ = clustered(ft[ft[key if sec == "macro" else "series"] == s])
        rows.append((s, n, k, mu, se, mt, n90, mu90, g.cat.iloc[0] if sec == "favorites" else ""))
    rows.sort(key=lambda r: -r[1])
    print(f"{'series':24s} {'fills':>5s} {'ev':>4s} {'net c/fill':>10s} {'±':>5s} {'thru':>6s} {'n90':>4s} {'last90':>6s}")
    for r in rows:
        print(f"{r[0]:24s} {r[1]:5d} {r[2]:4d} {r[3]:+10.1f} {r[4]:5.1f} {r[5]:+6.1f} {r[6]:4d} {r[7]:+6.1f} {r[8]}")
    print()
    if sec == "favorites":
        for cat, g in f.groupby("cat"):
            print(f"cat {cat:24s} {fmt(g)}")
    print()
    for mo, g in f.groupby("month"):
        print(f"month {mo}  {fmt(g)}")
    f.to_csv(f"{run}/{sec}_fills.csv", index=False)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
