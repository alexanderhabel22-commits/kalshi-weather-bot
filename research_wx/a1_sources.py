"""How accurate is each forecast source, and how accurate is the market itself?
usage: python research_wx/a1_sources.py data.pkl"""
import sys
from collections import defaultdict
from datetime import date

import numpy as np

sys.path.insert(0, "research_wx")
import lib  # noqa: E402

D = lib.load(sys.argv[1])
days = [d for d in D["days"] if d["day"] >= date(2025, 10, 1)]
print(len(days), "city-days", days[0]["day"], days[-1]["day"])
PER = [("Oct25-Feb26", date(2025, 10, 1), date(2026, 2, 28)), ("Mar-Aug26", date(2026, 3, 1), date(2026, 8, 31)),
       ("Sep-Nov26", date(2026, 9, 1), date(2026, 12, 31))]


def table(H):
    rows = []
    for d in days:
        T = lib.day_T(d, H)
        r = {"d": d, "y": d["actual"]}
        imp = lib.implied(lib.quotes(d, T))
        if imp:
            r["market"] = imp[0]
            r["market_sd"] = imp[1]
        for model, rec in D["fc"].get(d["icao"], {}).items():
            for tag in ("d1", "d2"):
                r[f"{model}.{tag}"] = lib.om_extreme(rec, tag, d["w0"], d["metric"])
        for model, runs in D["mos"].get(d["icao"], {}).items():
            r[model], r[model + ".age"] = lib.mos_value(runs, model, T, d)
        rows.append(r)
    return rows


def debias(rows, key, win=45):
    """Walk-forward: subtract the source's own mean error over the city's last `win` days."""
    hist = defaultdict(list)
    out = {}
    for i, r in enumerate(rows):
        v = r.get(key, np.nan)
        s = r["d"]["series"]
        past = [e for dd, e in hist[s] if 0 < (r["d"]["day"] - dd).days <= win]
        if not np.isnan(v) and len(past) >= 15:
            out[i] = v + float(np.mean(past))
        if not np.isnan(v):
            hist[s].append((r["d"]["day"], r["y"] - v))
    return out


for H in (36, 24):
    rows = table(H)
    keys = sorted({k for r in rows for k in r if k not in ("d", "y", "market_sd") and not k.endswith(".age")})
    fixed = {k: debias(rows, k) for k in keys}
    print(f"\n=== {H}h before close. Mean absolute error (F), each source bias-corrected on its own last 45 days; "
          "same city-days for every source in a row group")
    for metric in ("high", "low"):
        base = [i for i, r in enumerate(rows) if r["d"]["metric"] == metric and i in fixed["market"]
                and all(i in fixed[k] for k in keys if k.endswith(".d1") or k in ("NBS",))]
        print(f"-- {metric}s  n={len(base)}")
        print(f"{'source':22s}" + "".join(f"{p[0]:>14s}" for p in PER) + "   raw bias")
        for k in keys:
            line = f"{k:22s}"
            for _, a, b in PER:
                idx = [i for i in base if a <= rows[i]["d"]["day"] <= b and i in fixed[k]]
                e = [abs(rows[i]["y"] - fixed[k][i]) for i in idx]
                line += f"{np.mean(e):9.2f} ({len(e):4d})" if e else " " * 14
            raw = [rows[i]["y"] - rows[i][k] for i in base if not np.isnan(rows[i].get(k, np.nan))]
            print(line + f"   {np.mean(raw):+.2f}")
    ages = [r["NBS.age"] for r in rows if not np.isnan(r.get("NBS.age", np.nan))]
    print("NBS run age at decision (h): median %.1f" % np.median(ages))
