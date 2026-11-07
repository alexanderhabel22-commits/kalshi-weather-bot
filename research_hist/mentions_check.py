"""Rough replay of the live Mentions NO rule on the unbiased sample.

At each 30-min scan from 168h to 1h before the event (occurrence time), the first
time the last print puts NO at 75-90c and the word has >= 1000 contracts traded,
rest a NO bid at that price until the event. back = filled only if a later print
goes strictly through (YES above 1-p); touch = filled if a print reaches it.
No fees (Mentions makers pay 0). 1 contract per word.

usage: python research_hist/mentions_check.py feats.parquet mt_full.parquet
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd


def main():
    df = pd.read_parquet(sys.argv[1], columns=["mkt", "t", "q", "win", "count"])
    mt = pd.read_parquet(sys.argv[2])
    resyes = mt.result.to_numpy() == "yes"
    sel = np.flatnonzero((mt.cat == "Mentions").to_numpy() & (mt.occ_ts.to_numpy() > 0))
    d = df[df.mkt.isin(sel)]
    mk = d.mkt.to_numpy()
    tyes = (d.win.to_numpy() == 1) == resyes[mk]
    yp = np.where(tyes, d.q.to_numpy(), 1 - d.q.to_numpy())
    t, c = d.t.to_numpy(), d["count"].to_numpy()
    starts = np.flatnonzero(np.r_[True, mk[1:] != mk[:-1]])
    ends = np.r_[starts[1:], len(mk)]
    rows = []
    for a, b in zip(starts, ends):
        m = mk[a]
        occ = int(mt.occ_ts.iloc[m])
        tt, yy, cum = t[a:b], yp[a:b], np.cumsum(c[a:b])
        grid = np.arange(occ - 168 * 3600, occ - 3600, 1800)
        idx = np.searchsorted(tt, grid, side="right") - 1
        ok = idx >= 0
        if not ok.any():
            continue
        no = np.where(ok, 1 - yy[np.maximum(idx, 0)], np.nan)
        vol = np.where(ok, cum[np.maximum(idx, 0)], 0)
        hit = np.flatnonzero((no >= .75) & (no <= .90) & (vol >= 1000))
        if not len(hit):
            continue
        g = grid[hit[0]]
        p = round(float(no[hit[0]]), 2)
        later = (tt > g) & (tt < occ + 6 * 3600)
        nos = 1 - yy[later]
        won = int(not resyes[m])
        back = (np.round(nos, 4) < p - 1e-9).any()
        touch = (np.round(nos, 4) <= p + 1e-9).any()
        rows.append((mt.event.iloc[m], int(occ >= 1782864000), p, won,
                     (won - p) if back else np.nan, (won - p) if touch else np.nan))
    r = pd.DataFrame(rows, columns=["event", "oos", "p", "won", "back", "touch"])
    for o in (0, 1):
        x = r[r.oos == o]
        for col in ("back", "touch"):
            y = x[x[col].notna()]
            g = y.groupby("event")[col].agg(["sum", "count"])
            mu = g["sum"].sum() / max(g["count"].sum(), 1)
            se = np.sqrt(((g["sum"] - mu * g["count"]) ** 2).sum()) / max(g["count"].sum(), 1)
            print(f"{'OOS' if o else 'IS '} {col:5s} fills {len(y):4d}/{len(x):4d} words, "
                  f"{len(g)} events: {mu * 100:+.1f}c ±{196 * se:.1f}  (win rate {y.won.mean():.0%},"
                  f" avg price {y.p.mean():.2f})")


if __name__ == "__main__":
    main()
