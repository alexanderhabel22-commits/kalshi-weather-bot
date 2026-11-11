"""Does any forecast source know something the market price doesn't?
Regress (actual - market-implied temperature) on (source - market-implied temperature).
A slope near 0 means the market already contains that source.
usage: python research_wx/a2_resid.py data.pkl [H ...]"""
import pickle
import sys
from collections import defaultdict
from datetime import date

import numpy as np

sys.path.insert(0, "research_wx")
import lib  # noqa: E402

D = lib.load(sys.argv[1])
HS = [int(x) for x in sys.argv[2:]] or [36, 24]
days = [d for d in D["days"] if d["day"] >= date(2025, 10, 1)]
PER = [("Oct25-Feb26", date(2025, 10, 1), date(2026, 2, 28)), ("Mar-Aug26", date(2026, 3, 1), date(2026, 8, 31)),
       ("Sep-Nov26", date(2026, 9, 1), date(2026, 12, 31))]


def table(H):
    rows = []
    for d in days:
        T = lib.day_T(d, H)
        imp = lib.implied(lib.quotes(d, T))
        if not imp:
            continue
        r = {"d": d, "y": d["actual"], "market": imp[0], "market_sd": imp[1]}
        for model, rec in D["fc"].get(d["icao"], {}).items():
            for tag in ("d1", "d2"):
                r[f"{model}.{tag}"] = lib.om_extreme(rec, tag, d["w0"], d["metric"])
        for model, runs in D["mos"].get(d["icao"], {}).items():
            r[model], _ = lib.mos_value(runs, model, T, d)
        rows.append(r)
    return rows


def debias(rows, key, win=45):
    hist = defaultdict(list)
    for r in rows:
        v = r.get(key, np.nan)
        s = r["d"]["series"]
        past = [e for dd, e in hist[s] if 0 < (r["d"]["day"] - dd).days <= win]
        r[key + "*"] = v + float(np.mean(past)) if (not np.isnan(v) and len(past) >= 15) else np.nan
        if not np.isnan(v):
            hist[s].append((r["d"]["day"], r["y"] - v))


def ols(X, y, groups):
    """OLS with day-clustered standard errors."""
    X = np.column_stack([np.ones(len(y)), X])
    b = np.linalg.lstsq(X, y, rcond=None)[0]
    res = y - X @ b
    XtXi = np.linalg.inv(X.T @ X)
    meat = np.zeros((X.shape[1], X.shape[1]))
    for g in np.unique(groups):
        m = groups == g
        s = X[m].T @ res[m]
        meat += np.outer(s, s)
    se = np.sqrt(np.diag(XtXi @ meat @ XtXi))
    return b, se


for H in HS:
    rows = table(H)
    keys = sorted({k for r in rows for k in r if k not in ("d", "y", "market", "market_sd")})
    for k in keys:
        debias(rows, k)
    pickle.dump(rows, open(f"{sys.argv[1]}.rows{H}.pkl", "wb"))
    print(f"\n=== {H}h before close: slope of (actual - market) on (source - market), +-1 standard error")
    print(f"{'source':20s}" + "".join(f"{p[0]:>22s}" for p in PER))
    use = [k for k in keys if not k.endswith(".d2")]
    for k in use + ["ALL"]:
        line = f"{k:20s}"
        for _, a, b in PER:
            cols = use if k == "ALL" else [k]
            R = [r for r in rows if a <= r["d"]["day"] <= b and all(not np.isnan(r[c + "*"]) for c in cols)]
            if len(R) < 100:
                line += " " * 22
                continue
            y = np.array([r["y"] - r["market"] for r in R])
            g = np.array([r["d"]["day"].toordinal() for r in R])
            if k == "ALL":
                X = np.array([[np.mean([r[c + "*"] for c in cols]) - r["market"]] for r in R])
            else:
                X = np.array([[r[k + "*"] - r["market"]] for r in R])
            bb, se = ols(X, y, g)
            line += f"  {bb[1]:+.3f}+-{se[1]:.3f} n={len(R):4d}"
        print(line)
