"""Pooled families of the macro coverage replay, clustered by release DAY (not event),
so the U3 / ECONSTATU3 / UE versions of one jobs report count as one bet."""
import sys
import numpy as np
import pandas as pd

f = pd.read_csv(sys.argv[1])
f["day"] = pd.to_datetime(f.close_ts, unit="s").dt.strftime("%Y-%m-%d")
FAM = {
    "foreign central banks": lambda s: s.startswith("KXCBD"),
    "US jobs (U3/ECONSTATU3/UE/ADP/NFP/combo)": lambda s: s in ("KXU3", "KXECONSTATU3", "KXUE", "KXADP", "KXUSNFP", "KXEMPLOYMENTCOMBO"),
    "US CPI variants": lambda s: s.startswith("KXECONSTATC") or s in ("KXCPICOREYOY", "KXUSGASCPI", "KXCPINDEX", "KXCPIGAS", "KXCPIUSEDCAR", "KXCPICOMBO"),
    "GDP/ISM/retail/housing/PPI": lambda s: s in ("KXGDP", "KXISMPMI", "KXUSRETAIL", "KXHOUSINGSTART", "KXUSPPI", "KXUSPPIYOY"),
    "Fed extras (combo/dissent)": lambda s: s in ("KXFEDCOMBO", "KXFOMCDISSENTCOUNT", "KXFEDDISSENT"),
    "daily/weekly numbers": lambda s: s in ("KXTRUEV", "KXTNOTED", "KXTNOTEW", "KXTXERCOTPEAKD", "KXSOFRD", "KX30YMORTW", "KXFRM", "KXSPRLVL", "KXAAAGASWNJ", "KXGAS", "KXUSFLYCAN"),
    "monthly prices (gas/diesel/eggs/oil)": lambda s: s in ("KXAAAGASM", "KXAAAGASMTX", "KXDIESELMON", "KXEGGS", "KXOIL"),
}


def cl(g, key="day"):
    if len(g) == 0:
        return "no fills"
    a = g.groupby(key).net.agg(["sum", "count"])
    n, k = a["count"].sum(), len(a)
    mu = a["sum"].sum() / n
    se = np.sqrt(((a["sum"] - mu * a["count"]) ** 2).sum() * k / max(k - 1, 1)) / n
    return f"{mu:+6.1f}c ±{se:4.1f} ({n} fills, {k} release days)"


cut = f.close_ts.max() - 90 * 86400
h = f.close_ts.max() - 183 * 86400
for name, fn in FAM.items():
    g = f[f.series.map(fn)]
    print(f"{name:42s} all {cl(g)}\n{'':42s} last 6 mo {cl(g[g.close_ts >= h])}  last 90d {cl(g[g.close_ts >= cut])}  win {g.win.mean():.0%} avg px {g.px.mean():.2f}")
print("ALL", cl(f))
g = f[f.series.isin(["KXU3"])]
print("\nKXU3 by half-year"); g["half"] = pd.to_datetime(g.close_ts, unit="s").dt.year.astype(str) + np.where(pd.to_datetime(g.close_ts, unit="s").dt.month < 7, "H1", "H2")
for k, x in g.groupby("half"): print(" ", k, cl(x))
