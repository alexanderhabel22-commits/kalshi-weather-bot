"""Gap test C: daily AAA gas markets (KXAAAGASD national + states), model vs market.

usage: python research_gaps/an_gas.py RUN_DIR
1. Rebuild each day's AAA price from the settled strike ladder (highest strike that
   settled YES, lowest that settled NO; midpoint when they are <= 2c apart).
2. Model for day D, known while the market trades (day D-1): P(D) = P(D-1) + a*chg(D-1) + e.
   a and the residuals e are fit per series on events before Oct 1 (in-sample);
   Oct 1 - Nov 6 is out of sample.
3. Bot snapshot: every 5 min, if model prob minus last print >= EDGE, buy that side at
   its next taker print within 10 min (taker fee). One entry per market.
"""
import re
import sys
from datetime import datetime

import numpy as np
import pandas as pd

RUN = sys.argv[1]
EDGES = (0.05, 0.10, 0.20)
mk = pd.read_csv(f"{RUN}/markets.csv.gz")
sm = pd.read_csv(f"{RUN}/sample.csv.gz")
tr = pd.read_csv(f"{RUN}/trades.csv.gz")
MON = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}


def day(ev):
    m = re.search(r"-(\d\d)([A-Z]{3})(\d\d)$", ev)
    return datetime(2000 + int(m[1]), MON[m[2]], int(m[3])) if m else None


mk = mk[mk.strike_type == "greater"].copy()
mk["day"] = mk.event.map(day)
px = {}
for (s, d), g in mk.groupby(["series", "day"]):
    yes, no = g[g.result == "yes"].floor, g[g.result == "no"].floor
    lo = yes.max() if len(yes) else np.nan
    hi = no.min() if len(no) else np.nan
    if np.isfinite(lo) and np.isfinite(hi) and hi - lo <= 0.0201:
        px[(s, d)] = (lo + hi) / 2
P = pd.Series(px).rename("p").reset_index()
P.columns = ["series", "day", "p"]
P = P.sort_values(["series", "day"])
one = pd.Timedelta(days=1)
look = {(r.series, r.day): r.p for r in P.itertuples()}
P["prev"] = [look.get((r.series, r.day - one)) for r in P.itertuples()]
P["prev2"] = [look.get((r.series, r.day - 2 * one)) for r in P.itertuples()]
P = P.dropna()
P["chg"], P["pchg"] = P.p - P.prev, P.prev - P.prev2
cut = datetime(2026, 10, 1)
model = {}
for s, g in P.groupby("series"):
    ins = g[g.day < cut]
    if len(ins) < 20:
        continue
    a = (ins.chg * ins.pchg).sum() / max((ins.pchg ** 2).sum(), 1e-9)
    a = float(np.clip(a, 0, 1.2))
    model[s] = (a, (ins.chg - a * ins.pchg).values)
print("days with a rebuilt price:", len(px), " series with a model:", len(model))
allr = np.concatenate([v[1] for v in model.values()])
print("pooled in-sample residual sd (cents): %.2f" % (100 * allr.std()))
print("median a: %.2f" % np.median([v[0] for v in model.values()]))
info = {(r.series, r.day): (r.prev, r.pchg) for r in P.itertuples()}

sm["day"] = sm.event.map(day)
rows = []
trg = {k: g.sort_values("t") for k, g in tr.groupby("ticker")}
for m in sm.itertuples():
    if m.series not in model or (m.series, m.day) not in info or m.ticker not in trg:
        continue
    a, resid = model[m.series]
    prev, pchg = info[(m.series, m.day)]
    q = float((prev + a * pchg + resid > m.floor).mean())  # model P(yes)
    g = trg[m.ticker]
    t, y, side = g.t.values, g.yes_price.values, g.taker_side.values
    for edge in EDGES:
        for gt in np.arange(m.open_ts + 300, m.close_ts, 300):
            j = np.searchsorted(t, gt, side="right") - 1
            if j < 0:
                continue
            want = "yes" if q - y[j] >= edge else "no" if y[j] - q >= edge else None
            if not want:
                continue
            k = np.where((t > gt) & (t <= gt + 600) & (side == want))[0]
            if not len(k):
                continue
            p = y[k[0]] if want == "yes" else 1 - y[k[0]]
            won = (m.result == "yes") == (want == "yes")
            rows.append((m.series == "KXAAAGASD", edge, m.day >= cut,
                         100 * (won - p - 0.07 * p * (1 - p)), p, q))
            break
r = pd.DataFrame(rows, columns=["national", "edge", "oos", "c", "price", "model"])
out = r.groupby(["national", "edge", "oos"]).c.agg(["count", "mean", "std"])
out["ci95"] = 1.96 * out["std"] / np.sqrt(out["count"])
print(out.drop(columns="std").round(2).to_string())

# calibration: model vs last print at 6h before close
br = []
for m in sm.itertuples():
    if m.series not in model or (m.series, m.day) not in info or m.ticker not in trg:
        continue
    a, resid = model[m.series]
    prev, pchg = info[(m.series, m.day)]
    q = float((prev + a * pchg + resid > m.floor).mean())
    g = trg[m.ticker]
    g = g[g.t <= m.close_ts - 6 * 3600]
    if len(g):
        o = float(m.result == "yes")
        br.append((m.day >= cut, (q - o) ** 2, (g.yes_price.values[-1] - o) ** 2))
b = pd.DataFrame(br, columns=["oos", "model", "market"]).groupby("oos").agg(["mean", "count"])
print("Brier at 6h before close (lower is better):")
print(b.round(4).to_string())
