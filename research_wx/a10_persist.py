"""Real-time station information at order time: does the market already use it?
dep  = station temperature now minus what the models said for this hour (day-old runs)
lag  = model miss on the last fully settled day for this city
usage: python research_wx/a10_persist.py data.pkl rows36.pkl"""
import pickle
import sys
from collections import defaultdict
from datetime import date, timedelta

import numpy as np

sys.path.insert(0, "research_wx")
import lib  # noqa: E402
import sim  # noqa: E402

D = lib.load(sys.argv[1])
rows = pickle.load(open(sys.argv[2], "rb"))
H = 36
KEYS = ["gfs_seamless.d1", "ecmwf_ifs025.d1", "icon_seamless.d1", "gem_seamless.d1"]
sim.combine_invmse(rows, KEYS)
err = {(r["d"]["series"], r["d"]["day"]): r["y"] - r["mu"] for r in rows if not np.isnan(r["mu"])}
for r in rows:
    d = r["d"]
    T = lib.day_T(d, H)
    o = D["obs"].get(d["icao"])
    r["dep"] = np.nan
    if o is not None:
        k = np.searchsorted(o[:, 0], T, side="right") - 1
        if k >= 0 and T - o[k, 0] <= 5400:
            vals = []
            for m in ("gfs_seamless", "ecmwf_ifs025", "icon_seamless", "gem_seamless"):
                rec = D["fc"].get(d["icao"], {}).get(m)
                if rec is None or "d1" not in rec:
                    continue
                i = int(round((o[k, 0] - rec["t0"]) / 3600))
                if 0 <= i < len(rec["d1"]) and np.isfinite(rec["d1"][i]):
                    vals.append(rec["d1"][i])
            if vals:
                r["dep"] = o[k, 1] - float(np.mean(vals))
    r["lag"] = err.get((d["series"], d["day"] - timedelta(days=2)), np.nan)


def reg(R, ycol, xcols):
    X = np.column_stack([np.ones(len(R))] + [[r[c] for r in R] for c in xcols])
    y = np.array([ycol(r) for r in R])
    g = np.array([r["d"]["day"].toordinal() for r in R])
    b = np.linalg.lstsq(X, y, rcond=None)[0]
    res = y - X @ b
    XtXi = np.linalg.inv(X.T @ X)
    meat = np.zeros((X.shape[1],) * 2)
    for u in np.unique(g):
        s = X[g == u].T @ res[g == u]
        meat += np.outer(s, s)
    se = np.sqrt(np.diag(XtXi @ meat @ XtXi))
    return "  ".join(f"{c} {b[i + 1]:+.3f}+-{se[i + 1]:.3f}" for i, c in enumerate(xcols)) + f"  R2 {1 - res.var() / y.var():.3f}"


for pn, a, b in (("Mar-Aug26", date(2026, 3, 1), date(2026, 8, 31)), ("Sep-Nov26", date(2026, 9, 1), date(2026, 12, 31))):
    for metric in ("high", "low"):
        R = [r for r in rows if a <= r["d"]["day"] <= b and r["d"]["metric"] == metric
             and not any(np.isnan(r[c]) for c in ("dep", "lag", "mu"))]
        print(f"{pn} {metric}s n={len(R)}")
        print("   our model's miss explained by:   ", reg(R, lambda r: r["y"] - r["mu"], ["dep", "lag"]))
        print("   the market's miss explained by:  ", reg(R, lambda r: r["y"] - r["market"], ["dep", "lag"]))
_ = defaultdict
