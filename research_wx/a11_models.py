"""Bigger model set (Nov 11 download: ~20 Open-Meteo models, NYC + Chicago only, because the
download stopped after two stations). For each model: bias-corrected MAE next to the market's
own implied temperature on the SAME city-days, and the residual slope of (actual - market) on
(model - market), day-clustered. usage: python research_wx/a11_models.py data.pkl [H ...]"""
import sys
from collections import defaultdict
from datetime import date

import numpy as np

sys.path.insert(0, "research_wx")
import lib  # noqa: E402

D = lib.load(sys.argv[1])
HS = [int(x) for x in sys.argv[2:]] or [36, 24]
ST = ("KNYC", "KMDW")
days = [d for d in D["days"] if d["day"] >= date(2025, 10, 1) and d["icao"] in ST]
PER = [("Oct25-Feb26", date(2025, 10, 1), date(2026, 2, 28)), ("Mar-Aug26", date(2026, 3, 1), date(2026, 8, 31)),
       ("Sep-Nov26", date(2026, 9, 1), date(2026, 12, 31))]


def debias(rows, key, win=45):
    hist, out = defaultdict(list), {}
    for i, r in enumerate(rows):
        v = r.get(key, np.nan)
        s = r["d"]["series"]
        past = [e for dd, e in hist[s] if 0 < (r["d"]["day"] - dd).days <= win]
        if not np.isnan(v) and len(past) >= 15:
            out[i] = v + float(np.mean(past))
        if not np.isnan(v):
            hist[s].append((r["d"]["day"], r["y"] - v))
    return out


def slope(x, y, g):
    x, y = np.asarray(x), np.asarray(y)
    if len(x) < 30 or np.var(x) == 0:
        return np.nan, np.nan
    b = np.sum(x * y) / np.sum(x * x)
    e = y - b * x
    G = defaultdict(float)
    for xi, ei, gi in zip(x, e, g):
        G[gi] += xi * ei
    se = np.sqrt(sum(v * v for v in G.values())) / np.sum(x * x)
    return b, se


for H in HS:
    rows = []
    for d in days:
        if np.isnan(d["actual"]):
            continue
        imp = lib.implied(lib.quotes(d, lib.day_T(d, H)))
        if not imp:
            continue
        r = {"d": d, "y": d["actual"], "market": imp[0]}
        for model, rec in D["fc"].get(d["icao"], {}).items():
            r[model] = lib.om_extreme(rec, "d1", d["w0"], d["metric"])
        rows.append(r)
    rows.sort(key=lambda r: r["d"]["day"])
    keys = sorted({k for r in rows for k in r if k not in ("d", "y", "market")})
    fx = {k: debias(rows, k) for k in keys + ["market"]}
    keys.append("mean_all")
    fx["mean_all"] = {i: float(np.mean([fx[k][i] for k in keys[:-1] if i in fx[k]]))
                      for i in range(len(rows)) if sum(i in fx[k] for k in keys[:-1]) >= 5}
    print(f"\n=== {H}h before close, NYC + Chicago, highs and lows pooled. Day-old model runs (previous_day1).")
    print("MAE model vs market on the same city-days (market raw, model bias-corrected); slope = (actual-mkt) on (model-mkt)")
    for name, a, b in PER:
        print(f"-- {name}")
        res = []
        for k in keys:
            idx = [i for i in fx[k] if a <= rows[i]["d"]["day"] <= b]
            if len(idx) < 40:
                continue
            em = np.mean([abs(rows[i]["y"] - rows[i]["market"]) for i in idx])
            ek = np.mean([abs(rows[i]["y"] - fx[k][i]) for i in idx])
            sl, se = slope([fx[k][i] - rows[i]["market"] for i in idx], [rows[i]["y"] - rows[i]["market"] for i in idx],
                           [rows[i]["d"]["day"] for i in idx])
            res.append((ek - em, k, len(idx), em, ek, sl, se))
        for dlt, k, n, em, ek, sl, se in sorted(res):
            print(f"  {k:26s} n={n:4d}  market {em:.2f}  model {ek:.2f}  ({dlt:+.2f})  slope {sl:+.2f} +-{se:.2f}")
