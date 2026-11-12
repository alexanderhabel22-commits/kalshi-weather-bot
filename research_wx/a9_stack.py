"""Can a bigger, better-weighted set of forecast sources beat the market's implied forecast?
usage: python research_wx/a9_stack.py rows36.pkl [H]"""
import pickle
import sys
from datetime import date

import numpy as np

sys.path.insert(0, "research_wx")
import sim  # noqa: E402

rows = pickle.load(open(sys.argv[1], "rb"))
H = int(sys.argv[2]) if len(sys.argv) > 2 else 36
PER = [("Mar-Aug26", date(2026, 3, 1), date(2026, 8, 31)), ("Sep-Nov26", date(2026, 9, 1), date(2026, 12, 31))]
allk = sorted({k[:-1] for r in rows for k in r if k.endswith("*")})
cover = {k: np.mean([not np.isnan(r.get(k + "*", np.nan)) for r in rows if r["d"]["day"] >= date(2026, 3, 1)]) for k in allk}
print("coverage since Mar 2026:", {k: round(v, 2) for k, v in cover.items()})
good = [k for k in allk if cover[k] > 0.9]
SETS = {
    "live recipe (4 models, per-city 1/MSE)": ("inv", ["gfs_seamless.d1", "ecmwf_ifs025.d1", "icon_seamless.d1", "gem_seamless.d1"]),
    "same 4 models, pooled weights": ("ridge", ["gfs_seamless.d1", "ecmwf_ifs025.d1", "icon_seamless.d1", "gem_seamless.d1"]),
    "NWS guidance only (clean timing)": ("ridge", [k for k in good if "." not in k]),
    "every day-ahead source": ("ridge", [k for k in good if k.endswith(".d1") or "." not in k]),
    "every source incl. 2-day-old runs": ("ridge", good),
}


def slope(R):
    x = np.array([r["mu"] - r["market"] for r in R])
    y = np.array([r["y"] - r["market"] for r in R])
    g = np.array([r["d"]["day"].toordinal() for r in R])
    X = np.column_stack([np.ones(len(x)), x])
    b = np.linalg.lstsq(X, y, rcond=None)[0]
    res = y - X @ b
    XtXi = np.linalg.inv(X.T @ X)
    meat = sum(np.outer(X[g == u].T @ res[g == u], X[g == u].T @ res[g == u]) for u in np.unique(g))
    return b[1], float(np.sqrt((XtXi @ meat @ XtXi)[1, 1]))


for name, (kind, keys) in SETS.items():
    if len(keys) < 2:
        continue
    for r in rows:
        r.pop("_base", None)
    (sim.combine_invmse if kind == "inv" else sim.combine_ridge)(rows, keys)
    print(f"\n== {name}: {len(keys)} sources")
    B = sim.brackets(rows, H)
    tr, _ = sim.walk(B, 0.06)
    for pn, a, b in PER:
        R = [r for r in rows if a <= r["d"]["day"] <= b and not np.isnan(r["mu"])]
        if len(R) < 200:
            continue
        s, se = slope(R)
        for metric in ("high", "low"):
            M = [r for r in R if r["d"]["metric"] == metric]
            print(f"   {pn} {metric}s: model miss {np.mean([abs(r['y'] - r['mu']) for r in M]):.2f}F, "
                  f"market miss {np.mean([abs(r['y'] - r['market']) for r in M]):.2f}F (n={len(M)})")
        print(f"   {pn}: extra information beyond the price {s:+.3f} +-{se:.3f} | "
              + sim.summary([t for t in tr if a <= t[0] <= b], "resting orders, 6c edge"))
