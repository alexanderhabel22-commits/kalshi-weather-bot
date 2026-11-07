"""Summarise favs.py output: c/contract (1 contract per entry), event-clustered 95% CI.

usage: python research_hist/favs_report.py favs.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd


def st(d, col):
    d = d[d[col].notna()]
    if len(d) == 0:
        return "—"
    g = d.groupby("event")[col].agg(["sum", "count"])
    mu = g["sum"].sum() / g["count"].sum()
    se = np.sqrt(((g["sum"] - mu * g["count"]) ** 2).sum()) / g["count"].sum()
    return f"{mu * 100:+.1f} ±{196 * se:.1f} (n{len(d)}, {len(g)}ev)"


def main():
    f = pd.read_csv(sys.argv[1])
    f["key"] = np.where(f.cat == "Sports", "Sports|" + f.grp.str.split("|").str[2], f.cat)
    for by in (["key", "band"], ["key", "band", "win_ttc"]):
        print(f"\n### by {' x '.join(by)}\n")
        print("| " + " | ".join(by) + " | taker IS | taker OOS | maker IS | maker OOS | touch IS | touch OOS |")
        print("|" + "---|" * (len(by) + 6))
        for k, d in f.groupby(by):
            if d.event.nunique() < 30:
                continue
            cells = [st(d[d.oos == o], c) for c in ("taker_r", "maker_r", "touch_r") for o in (0, 1)]
            print("| " + " | ".join(map(str, k)) + " | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
