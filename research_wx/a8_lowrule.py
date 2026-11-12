"""Candidate paper rule for late-day lows and its day-to-day risk.
usage: python research_wx/a8_lowrule.py data.pkl.late_low.pkl"""
import pickle
import sys
from collections import defaultdict
from datetime import date

import numpy as np

sys.path.insert(0, "research_wx")
import sim  # noqa: E402

rec = pickle.load(open(sys.argv[1], "rb"))
# the records hold one row per (bracket, side, hour); a real order is placed once
PER = [("Oct25-Aug26", date(2025, 10, 1), date(2026, 8, 31)), ("Sep-Nov26", date(2026, 9, 1), date(2026, 12, 31))]


def rule(hours, min_sp, lo_px, hi_px, pos=("need",), side="no", max_gap=99):
    seen, out = set(), []
    for H in hours:
        for r in rec:
            if r["H"] != H or r["pos"] not in pos or r["side"] != side:
                continue
            tk = (r["tk"], r["side"])
            if tk in seen or r["sp"] < min_sp - 1e-9 or not (lo_px <= r["px"] <= hi_px) or r["gap"] > max_gap:
                continue
            seen.add(tk)
            out.append(r)
    return out


def line(R, label):
    s = f"{label:46s}"
    for n, a, b in PER:
        T = [r for r in R if a <= r["day"] <= b]
        if len(T) < 20:
            s += f" | {n}: n={len(T)}"
            continue
        m, se = sim.cl([r["pnl"] for r in T], [r["day"] for r in T])
        nd = (max(r["day"] for r in T) - min(r["day"] for r in T)).days + 1
        s += f" | {n}: {100 * m:+5.1f}c +-{100 * se:.1f} n={len(T):4d} ({len(T) / nd:.1f}/day, ${sum(r['pnl'] for r in T) / nd * 30:+.2f}/mo per contract)"
    return s


print("NO on brackets below the low so far (resting 1c inside the offer, held to settlement)")
for hours, lab in (((12,), "at 12h"), ((9,), "at 9h"), ((6,), "at 6h"), ((12, 10, 9, 8, 7, 6), "first chance 12h..6h"),
                   ((9, 8, 7, 6, 5, 4), "first chance 9h..4h")):
    for min_sp in (0.0, 0.04, 0.06, 0.08):
        print(line(rule(hours, min_sp, 0.03, 0.97), f"{lab}, spread >= {int(min_sp * 100)}c"))
print()
for lo_px, hi_px in ((0.03, 0.97), (0.30, 0.97), (0.50, 0.97), (0.03, 0.90), (0.50, 0.90)):
    print(line(rule((12, 10, 9, 8, 7, 6), 0.04, lo_px, hi_px), f"first 12h..6h, spread>=4c, price {lo_px:.2f}-{hi_px:.2f}"))
print(line(rule((12, 10, 9, 8, 7, 6), 0.04, 0.03, 0.97, max_gap=2), "first 12h..6h, spread>=4c, adjacent only"))
print("\nYES on the bracket holding the low so far")
for min_sp in (0.0, 0.04, 0.08):
    print(line(rule((12, 10, 9, 8, 7, 6), min_sp, 0.03, 0.97, pos=("holds",), side="yes"), f"first 12h..6h, spread >= {int(min_sp * 100)}c"))

R = rule((12, 10, 9, 8, 7, 6), 0.04, 0.03, 0.97)
by = defaultdict(float)
for r in R:
    by[r["day"]] += r["pnl"]
v = np.array([by[k] for k in sorted(by)])
print(f"\nChosen rule, 1 contract per order: {len(v)} days, mean ${v.mean():+.3f}/day, sd ${v.std():.2f}, worst day ${v.min():+.2f}, "
      f"best ${v.max():+.2f}, losing days {100 * (v < 0).mean():.0f}%, worst 7-day run ${min(v[i:i + 7].sum() for i in range(len(v) - 6)):+.2f}")
print("avg cost per fill $%.2f, fills on worst day %d" % (np.mean([r["px"] for r in R]), max(sum(1 for r in R if r["day"] == d) for d in by)))
wk = defaultdict(float)
for r in R:
    wk[r["day"].isocalendar()[:2]] += r["pnl"]
w = np.array(list(wk.values()))
print(f"weeks: {len(w)}, winning weeks {100 * (w > 0).mean():.0f}%, worst week ${w.min():+.2f}")
