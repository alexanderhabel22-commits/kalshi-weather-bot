"""Cross-market consistency check on fetch_x.py output (1-minute yes bid/ask).

Within one game, at the same minute, look for prices that cannot all be right:
  win_vs_spread: "T wins by more than x" bid > "T wins" ask
                 -> buy T-wins YES, buy spread NO; pays >= $1 in every outcome
  ladder:        "T wins by more than x_hi" bid > "T wins by more than x_lo" ask
  exclusive:     two outcomes that can't both happen (A wins / B wins / B covers ...),
                 bids summing over $1 -> buy NO on both; pays >= $1
Profit = guaranteed payout minus cost minus taker fees on both legs (10-lot rounding).
Candle closes are quotes at the end of each minute; depth is unknown.

usage: python research_hist/analyze_x.py <run dir> [...]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def fee(p):
    return np.ceil(np.round(0.07 * 10 * p * (1 - p) * 100, 6)) / 1000


def team(ticker):
    return re.sub(r"\d+$", "", ticker.rsplit("-", 1)[-1])


def main():
    runs = sys.argv[1:]
    mk = pd.concat([pd.read_csv(Path(r) / "markets.csv.gz") for r in runs])
    cd = pd.concat([pd.read_csv(Path(r) / "candles.csv.gz") for r in runs])
    cd = cd.dropna(subset=["yes_bid", "yes_ask"])
    cd = cd[(cd.yes_ask > 0) & (cd.yes_ask <= 1) & (cd.yes_bid > 0)]
    mk["team"] = mk.ticker.map(team)
    mk = mk.drop_duplicates("ticker").set_index("ticker")
    cd = cd[cd.ticker.isin(mk.index)]
    cd["game"] = cd.ticker.map(mk.game) + "|" + cd.ticker.map(mk.league)
    print(f"# Cross-market check\n\ngames {cd.game.nunique()}, markets {cd.ticker.nunique()}, "
          f"quote-minutes {len(cd):,}\n")
    hits = []
    for g, d in cd.groupby("game"):
        bid = d.pivot_table(index="ts", columns="ticker", values="yes_bid", aggfunc="last")
        ask = d.pivot_table(index="ts", columns="ticker", values="yes_ask", aggfunc="last")
        tks = list(bid.columns)
        info = mk.loc[tks]
        wins = {r.team: t for t, r in info.iterrows() if r.kind == "g" and r.team != "TIE"}
        spreads = [(t, r.team, float(r.floor_strike)) for t, r in info.iterrows()
                   if r.kind == "s" and pd.notna(r.floor_strike)]
        checks = []  # (kind, buy-yes ticker or None, buy-no tickers)
        for st, tm, x in spreads:
            if tm in wins:
                checks.append(("win_vs_spread", wins[tm], st))  # S bid > W ask
            for st2, tm2, x2 in spreads:
                if tm2 == tm and x2 > x:
                    checks.append(("ladder", st, st2))  # S(x2) bid > S(x) ask
        excl = [(t, r.team) for t, r in info.iterrows() if r.team != "TIE"]
        for i, (t1, a) in enumerate(excl):
            for t2, b in excl[i + 1:]:
                if a != b:
                    checks.append(("exclusive", None, (t1, t2)))
        for kind, lo_t, hi_t in checks:
            if kind == "exclusive":
                b1, b2 = bid[hi_t[0]], bid[hi_t[1]]
                cost = (1 - b1) + (1 - b2)
                prof = 1 - cost - fee(1 - b1) - fee(1 - b2)
                pair = "+".join(hi_t)
            else:
                a, b = ask[lo_t], bid[hi_t]
                cost = a + (1 - b)
                prof = 1 - cost - fee(a) - fee(1 - b)
                pair = f"{lo_t}>{hi_t}"
            prof = prof.dropna()
            pos = prof[prof > 0.0049]
            if len(pos):
                # longest run of consecutive minutes with a profitable gap
                ts = pos.index.to_numpy()
                runs_ = np.split(ts, np.flatnonzero(np.diff(ts) > 60) + 1)
                hits.append((g, kind, pair, len(pos), max(len(r) for r in runs_),
                             float(pos.max()), float(pos.median()), len(prof)))
    h = pd.DataFrame(hits, columns=["game", "kind", "pair", "minutes", "longest_run",
                                    "max_c", "median_c", "minutes_quoted"])
    h.to_csv(Path(runs[0]).parent / "x_hits.csv", index=False)
    ng = cd.game.nunique()
    print("| kind | games with a gap | gap-minutes | games with a gap lasting 5+ min | "
          "median gap | best gap |\n|---|---|---|---|---|---|")
    for k, d in h.groupby("kind"):
        print(f"| {k} | {d.game.nunique()} of {ng} | {d.minutes.sum():,} | "
              f"{d[d.longest_run >= 5].game.nunique()} | {d.median_c.median() * 100:.1f}c | "
              f"{d.max_c.max() * 100:.0f}c |")
    if len(h):
        h["league"] = h.game.str.split("|").str[1]
        print("\nBy league (games with any 5+ minute gap):\n")
        print(h[h.longest_run >= 5].groupby("league").game.nunique().to_string())
        print("\nLargest lasting gaps:\n")
        print(h[h.longest_run >= 5].sort_values("max_c", ascending=False).head(15).to_string())


if __name__ == "__main__":
    main()
