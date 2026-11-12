"""Tables for the main-bot audit from a replay pickle (research_main/replay.py).

  python research_main/analyze.py <replay.pkl> <section>
Cents per ORDER (every order is one unit). +- is one standard error from resampling
whole events (macro: whole release days), so bets on one event count together.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd


def est(d: pd.DataFrame, col="pnl", by="cluster", B=2000):
    if len(d) == 0:
        return np.nan, np.nan, 0
    e = d.groupby(by)[col].agg(["sum", "count"])
    if len(e) < 2:
        return e["sum"].sum() / e["count"].sum(), np.nan, len(e)
    rng = np.random.default_rng(7)
    idx = rng.integers(0, len(e), (B, len(e)))
    bs = e["sum"].values[idx].sum(1) / e["count"].values[idx].sum(1)
    return e["sum"].sum() / e["count"].sum(), bs.std(), len(e)


def line(label, d, fill="touch"):
    f = d[d[fill].notna()]
    u = d[d[fill].isna()]
    m, se, n = est(f)
    um = u.pnl.mean() if len(u) else np.nan
    return (f"| {label} | {len(d)} | {len(f)} ({len(f) / max(1, len(d)):.0%}) | {n} | "
            f"{m:+.1f} ± {se:.1f} | {f.px.mean() * 100:.1f} | {f.win.mean():.1%} | "
            f"{u.win.mean() if len(u) else float('nan'):.1%} / {um:+.1f} |")


HEAD = ("| slice | orders | filled | events | c per filled order | avg price | won | "
        "unfilled: won / would have made |\n|---|---|---|---|---|---|---|---|")


def prep(df: pd.DataFrame, sec: str) -> pd.DataFrame:
    df = df.copy()
    df["day"] = pd.to_datetime(df.close_ts, unit="s").dt.strftime("%Y-%m-%d")
    df["month"] = pd.to_datetime(df.t0, unit="s").dt.strftime("%Y-%m")
    df["cluster"] = df.day if sec == "macro" else df.event
    df["spread"] = ((df.ask - df.bid) * 100).round()
    return df


def table(title, pairs, fill="touch"):
    print(f"\n**{title}**\n\n{HEAD}")
    for label, d in pairs:
        if len(d):
            print(line(label, d, fill))


if __name__ == "__main__":
    df = prep(pd.read_pickle(sys.argv[1]), sys.argv[2])
    live = df[df.kind == "live"]
    table("Live rule, by month", [("ALL", live)] + [(k, g) for k, g in live.groupby("month")])
    table("Live rule, back-of-queue fills only", [("ALL", live)], "through")
    table("By kind", [(k, g) for k, g in df.groupby("kind")])


def slices(df, sec):
    live = df[df.kind == "live"]
    both = df[df.kind.isin(["live", "skipped"])]
    cut = lambda d, col, edges: [(f"{col} {a:g}-{b:g}", d[(d[col] >= a) & (d[col] < b)])  # noqa: E731
                                 for a, b in zip(edges[:-1], edges[1:])]
    table("All in-band orders (live + skipped) by our price", cut(both, "px", [.6, .7, .75, .8, .85, .9, .93, .97, .99]))
    table("Live rule by spread when placed (cents)", cut(live, "spread", [1, 2, 3, 5, 8, 100]))
    table("Live rule: improved the bid vs joined it", [("improved (bid+1c)", live[live.joined == 0]), ("joined (1c spread)", live[live.joined == 1])])
    table("Live rule by hours before the event/close", cut(live, "hours", [0, 6, 24, 72, 168, 600]))
    table("Live rule by volume traded before the order", cut(live, "vol", [0, 1000, 3000, 10000, 1e5, 1e12]))
    top = live.series.value_counts().head(8).index
    table("Live rule by series (top 8)", [(s, live[live.series == s]) for s in top])
    table("Outside the live band (first time seen there) by price", cut(df[df.kind == "wide"], "px", [.6, .65, .7, .75, .9, .95, .99]))


if __name__ == "__main__":
    slices(df, sys.argv[2])
