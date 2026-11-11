"""Is the market itself mispriced anywhere (no forecast needed)?
For every bracket at H hours before close: resting buy 1c inside the spread on each
side, filled if a later hourly candle traded at/through the price. P&L per contract by
price bucket, with day-clustered standard errors.
usage: python research_wx/a3_calib.py data.pkl"""
import sys
from collections import defaultdict
from datetime import date

import numpy as np

sys.path.insert(0, "research_wx")
import lib  # noqa: E402

D = lib.load(sys.argv[1])
days = [d for d in D["days"] if d["day"] >= date(2025, 10, 1)]
PER = [("Oct25-Aug26", date(2025, 10, 1), date(2026, 8, 31)), ("Sep-Nov26", date(2026, 9, 1), date(2026, 12, 31))]
BK = [(0.03, 0.10), (0.10, 0.20), (0.20, 0.35), (0.35, 0.50), (0.50, 0.65), (0.65, 0.80), (0.80, 0.90), (0.90, 0.975)]


def cl(vals, groups):
    """mean and day-clustered standard error."""
    by = defaultdict(float)
    for v, g in zip(vals, groups):
        by[g] += v
    n = len(vals)
    m = sum(vals) / n
    cnt = defaultdict(int)
    for g in groups:
        cnt[g] += 1
    s = sum((by[g] - m * cnt[g]) ** 2 for g in by)
    return m, (s ** 0.5) / n


for H in (36, 24, 12, 6):
    rec = []
    for d in days:
        T = lib.day_T(d, H)
        for lo, hi, b, a, tl, th, yes, tk, _ in lib.quotes(d, T):
            if not (np.isfinite(b) and np.isfinite(a)) or not (0 < b < a < 1):
                continue
            sp = a - b
            py = round(b + 0.01, 2)
            if py < a and np.isfinite(tl) and tl <= py + 1e-9:
                rec.append((d["day"], "yes", py, (1.0 if yes else 0.0) - py, sp, d["metric"]))
            pn = round(1 - (a - 0.01), 2)
            if a - 0.01 > b and np.isfinite(th) and th >= a - 0.01 - 1e-9:
                rec.append((d["day"], "no", pn, (0.0 if yes else 1.0) - pn, sp, d["metric"]))
    print(f"\n=== {H}h before close: resting buy 1c inside the spread, cents per filled contract (+-1 se, n)")
    print(f"{'price paid':12s}" + "".join(f"{p[0] + ' ' + s:>26s}" for p in PER for s in ("YES", "NO")))
    for lo_, hi_ in BK:
        line = f"{lo_:.2f}-{hi_:.2f}  "
        for _, a_, b_ in PER:
            for side in ("yes", "no"):
                R = [r for r in rec if a_ <= r[0] <= b_ and r[1] == side and lo_ <= r[2] < hi_]
                if len(R) < 30:
                    line += " " * 26
                    continue
                m, se = cl([r[3] for r in R], [r[0] for r in R])
                line += f"   {100 * m:+5.1f}+-{100 * se:4.1f} n={len(R):5d}  "
        print(line)
    for name, f in (("all", lambda r: True), ("spread<=2c", lambda r: r[4] <= 0.021), ("spread 3-5c", lambda r: 0.021 < r[4] <= 0.051),
                    ("spread>=6c", lambda r: r[4] > 0.051), ("highs", lambda r: r[5] == "high"), ("lows", lambda r: r[5] == "low")):
        line = f"{name:12s}"
        for _, a_, b_ in PER:
            for side in ("yes", "no"):
                R = [r for r in rec if a_ <= r[0] <= b_ and r[1] == side and f(r)]
                m, se = cl([r[3] for r in R], [r[0] for r in R])
                line += f"   {100 * m:+5.1f}+-{100 * se:4.1f} n={len(R):5d}  "
        print(line)
