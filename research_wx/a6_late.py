"""Late in the climate day: resting orders 1c inside the spread, grouped by where the
bracket sits relative to the low/high already recorded by the station (hourly METAR).
usage: python research_wx/a6_late.py data.pkl"""
import sys
from datetime import date

import numpy as np

sys.path.insert(0, "research_wx")
import lib  # noqa: E402
import sim  # noqa: E402

D = lib.load(sys.argv[1])
days = [d for d in D["days"] if d["day"] >= date(2025, 10, 1)]
PER = [("Oct25-Aug26", date(2025, 10, 1), date(2026, 8, 31)), ("Sep26", date(2026, 9, 1), date(2026, 9, 30)),
       ("Oct26", date(2026, 10, 1), date(2026, 10, 31)), ("Nov26", date(2026, 11, 1), date(2026, 12, 31))]


def running(d, T):
    o = D["obs"].get(d["icao"])
    if o is None:
        return None
    m = (o[:, 0] >= d["w0"]) & (o[:, 0] <= T)
    if m.sum() < 3:
        return None
    v = o[m, 1]
    return (v.max() if d["metric"] == "high" else v.min()), o[m, 1][-1]


for metric in ("low", "high"):
    for H in (12, 9, 6, 4, 2):
        rec = []
        for d in days:
            if d["metric"] != metric:
                continue
            T = lib.day_T(d, H)
            run = running(d, T)
            if run is None:
                continue
            ext, cur = run
            k = int(np.floor(ext + 0.5))
            for lo, hi, b, a, tl, th, yes, tk, later in lib.quotes(d, T):
                if not (np.isfinite(b) and np.isfinite(a)) or not (0 < b < a < 1):
                    continue
                # position of the bracket relative to the extreme so far
                if (lo is None or k >= lo) and (hi is None or k <= hi):
                    pos = "holds extreme"
                elif (metric == "low" and hi is not None and hi < k) or (metric == "high" and lo is not None and lo > k):
                    pos = "needs more"
                else:
                    pos = "passed"
                r = {"later": later}
                for side, px, win in (("yes", round(b + 0.01, 2), 1.0 if yes else 0.0),
                                      ("no", round(1 - (a - 0.01), 2), 0.0 if yes else 1.0)):
                    other = a if side == "yes" else 1 - b
                    if px < other - 1e-9 and 0.03 <= px <= 0.97 and sim.filled(r, side, px):
                        rec.append((d["day"], side, pos, win - px, a - b, px, abs(cur - ext)))
        print(f"\n=== {metric}s, {H}h before close: cents per filled contract (+-1 se, n)")
        print(f"{'bracket / side':24s}" + "".join(f"{p[0]:>24s}" for p in PER))
        for pos in ("holds extreme", "needs more", "passed"):
            for side in ("yes", "no"):
                line = f"{pos + ' ' + side.upper():24s}"
                for _, a_, b_ in PER:
                    R = [r for r in rec if a_ <= r[0] <= b_ and r[1] == side and r[2] == pos]
                    if len(R) < 20:
                        line += " " * 24
                        continue
                    m, se = sim.cl([r[3] for r in R], [r[0] for r in R])
                    line += f"  {100 * m:+5.1f}+-{100 * se:4.1f} n={len(R):5d}"
                print(line)
