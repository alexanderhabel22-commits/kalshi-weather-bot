"""Leader-hold model: chance the side that leads now still wins at the 15-minute close.

Inputs: <SYM>.npz windows from fetch.py. Features at minute k (k minutes elapsed):
  d    = current price vs strike (strike = typical price of the minute before the window)
  vol  = realized 1-min volatility over the previous 60 minutes (4 prior windows)
  z    = d / (vol * sqrt(15 - k))     (the textbook random-walk score)
Target: settle (typical price of the last minute) > strike.

The empirical table P(up | z bin, minutes left) is fit on the train years and checked
out of sample. Gaussian Phi(z) is the baseline.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

ZB = np.array([-9, -4, -3, -2.5, -2, -1.75, -1.5, -1.25, -1, -0.75, -0.5, -0.25, 0,
               0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 9])


def phi(x):
    return 0.5 * (1 + np.vectorize(math.erf)(np.asarray(x) / math.sqrt(2)))


def load(path: Path):
    z = np.load(path)
    w = {k: z[k] for k in z.files}
    path_ = w["path"].astype(float)
    path_[w["path"] == -32768] = np.nan
    w["pathf"] = path_ / 1e5
    st = w["settle"].astype(float)
    st[w["settle"] == -32768] = np.nan
    w["settlef"] = st / 1e5
    w["kf"] = w["strike"] / w["p0"] - 1  # strike vs p0
    # 1-min returns inside each window (minute 1 vs p0, then diffs)
    lp = np.log1p(np.concatenate([np.zeros((len(path_), 1)), w["pathf"]], axis=1))
    r = np.diff(lp, axis=1)  # 15 one-minute log returns
    # previous 4 windows must be contiguous
    t0 = w["t0"]
    idx = {int(t): i for i, t in enumerate(t0)}
    vol = np.full(len(t0), np.nan)
    for i, t in enumerate(t0):
        js = [idx.get(int(t) - 900 * m) for m in (1, 2, 3, 4)]
        if any(j is None for j in js):
            continue
        rr = r[js].ravel()
        rr = rr[np.isfinite(rr)]
        if len(rr) >= 45:
            vol[i] = np.sqrt(np.mean(rr ** 2))
    w["vol"] = vol
    w["r"] = r
    w["up"] = (w["settlef"] > w["kf"]).astype(float)
    w["up"][~np.isfinite(w["settlef"])] = np.nan
    return w


def features(w, k):
    """k = minutes elapsed (1..14). Returns z, d, ok mask."""
    d = w["pathf"][:, k - 1] - w["kf"]
    left = 15 - k - 2 / 3  # settle is a 60 s average: the last minute counts ~1/3
    with np.errstate(divide="ignore", invalid="ignore"):
        z = d / (w["vol"] * math.sqrt(left))
    ok = np.isfinite(z) & np.isfinite(w["up"]) & (w["vol"] > 0)
    return z, d, ok


def fit_table(w, mask_rows, ks=range(1, 15)):
    """Empirical P(up) per (k, z bin) with counts. mask_rows: bool rows to use."""
    tab = {}
    for k in ks:
        z, d, ok = features(w, k)
        m = ok & mask_rows
        b = np.clip(np.digitize(z[m], ZB) - 1, 0, len(ZB) - 2)
        up = w["up"][m]
        n = np.bincount(b, minlength=len(ZB) - 1)
        s = np.bincount(b, weights=up, minlength=len(ZB) - 1)
        tab[k] = (n, s)
    return tab


def table_prob(tab, k, z, prior=20):
    n, s = tab[k]
    b = np.clip(np.digitize(z, ZB) - 1, 0, len(ZB) - 2)
    mid = phi(np.clip((ZB[:-1] + ZB[1:]) / 2, -4, 4))
    p = (s + prior * mid) / (n + prior)
    return p[b]
