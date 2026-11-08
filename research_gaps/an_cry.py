"""Gap test B: KXBTCD / KXETHD hourly above/below vs a spot-price model.

usage: python research_gaps/an_cry.py RUN_DIR
Model at time t for strike K closing at T: P(S_T > K) = Phi(ln(S_t/K) / (sig*sqrt(T-t))),
S_t = last Coinbase 1-min close, sig = realized 1-min vol over the prior 6 hours.
(Kalshi settles on the CF Benchmarks index averaged over the last minute; Coinbase is a proxy.)
Bot snapshot: every 5 min, from 55 to 5 min before close, if model minus last print >= EDGE,
buy that side at its next taker print within 2 min (taker fee). One entry per market.
"""
import math
import sys

import numpy as np
import pandas as pd

RUN = sys.argv[1]
EDGES = (0.05, 0.10, 0.20)
sm = pd.read_csv(f"{RUN}/sample.csv.gz")
tr = pd.read_csv(f"{RUN}/trades.csv.gz")
sp = pd.read_csv(f"{RUN}/spot.csv.gz")
SPOT = {}
for prod, g in sp.groupby("product"):
    g = g.drop_duplicates("t").sort_values("t")
    lr = np.diff(np.log(g.close.values), prepend=np.nan)
    vol = pd.Series(lr).rolling(360, min_periods=200).std().values
    SPOT[prod] = (g.t.values, g.close.values, vol)


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def model(prod, t, K, T):
    ts, px, vol = SPOT[prod]
    j = np.searchsorted(ts, t - 60, side="right") - 1  # last complete minute
    if j < 0 or not np.isfinite(vol[j]) or vol[j] <= 0:
        return None
    tau = max((T - t) / 60, 1)
    return phi(math.log(px[j] / K) / (vol[j] * math.sqrt(tau)))


trg = {k: g.sort_values("t") for k, g in tr.groupby("ticker")}
cut = 1790812800  # Oct 1 2026
rows, br = [], []
for m in sm.itertuples():
    if m.ticker not in trg or not np.isfinite(m.floor):
        continue
    prod = "BTC-USD" if m.series == "KXBTCD" else "ETH-USD"
    g = trg[m.ticker]
    t, y, side = g.t.values, g.yes_price.values, g.taker_side.values
    T, o = m.close_ts, float(m.result == "yes")
    # Brier at 30 min before close
    j = np.searchsorted(t, T - 1800, side="right") - 1
    q = model(prod, T - 1800, m.floor, T)
    if j >= 0 and q is not None:
        br.append((m.series, T >= cut, (q - o) ** 2, (y[j] - o) ** 2))
    for edge in EDGES:
        for gt in range(T - 55 * 60, T - 5 * 60 + 1, 300):
            j = np.searchsorted(t, gt, side="right") - 1
            q = model(prod, gt, m.floor, T)
            if j < 0 or q is None:
                continue
            want = "yes" if q - y[j] >= edge else "no" if y[j] - q >= edge else None
            if not want:
                continue
            k = np.where((t > gt) & (t <= gt + 120) & (side == want))[0]
            if not len(k):
                continue
            p = y[k[0]] if want == "yes" else 1 - y[k[0]]
            won = (o == 1) == (want == "yes")
            rows.append((m.series, edge, T >= cut, 100 * (won - p - 0.07 * p * (1 - p))))
            break
r = pd.DataFrame(rows, columns=["series", "edge", "oct_nov", "c"])
out = r.groupby(["series", "edge", "oct_nov"]).c.agg(["count", "mean", "std"])
out["ci95"] = 1.96 * out["std"] / np.sqrt(out["count"])
print(out.drop(columns="std").round(2).to_string())
b = pd.DataFrame(br, columns=["series", "oct_nov", "model", "market"])
print("Brier 30 min before close (lower is better):")
print(b.groupby(["series", "oct_nov"]).agg(["mean", "count"]).round(4).to_string())
