"""Walk-forward model + trading simulation shared by the experiments.

rows: list of dict from a2_resid (one per city-day at a decision time), each with
  d (city-day incl. brackets), y (actual), market, market_sd, and "<source>*"
  bias-corrected forecasts.
"""
import math
from collections import defaultdict

import numpy as np

import lib


def lg(p):
    p = min(max(p, 0.005), 0.995)
    return math.log(p / (1 - p))


def sig(z):
    return 1 / (1 + math.exp(-max(min(z, 30), -30)))


def combine_invmse(rows, keys, win=45, min_hist=15, sd_floor=1.2):
    """The current live recipe: per city, weights = 1/MSE over the last `win` days,
    sd = RMS error of the combination over the same days. Sets r['mu'], r['sd']."""
    hist = defaultdict(list)
    for r in rows:
        s, day = r["d"]["series"], r["d"]["day"]
        past = [h for h in hist[s] if 0 < (day - h[0]).days <= win]
        vals = [r.get(k + "*", np.nan) for k in keys]
        r["mu"] = r["sd"] = np.nan
        if len(past) >= min_hist and not any(np.isnan(vals)):
            E = np.array([h[1] for h in past])            # errors per source
            w = 1 / np.maximum((E ** 2).mean(axis=0), 0.25)
            comb = (E * w).sum(axis=1) / w.sum()
            r["mu"] = float(np.dot(w, vals) / w.sum())
            r["sd"] = max(float(np.sqrt((comb ** 2).mean())), sd_floor)
        if not any(np.isnan(vals)):
            hist[s].append((day, [r["y"] - v for v in vals]))
    return rows


def combine_ridge(rows, keys, win=120, lam=30.0, min_n=300, sd_floor=1.0, by_metric=True, sd_win=60):
    """Pooled weights: least squares of (actual - mean) on (source - mean) over the
    last `win` days of every city (ridge toward equal weights), then a per-city
    residual bias and sd. Sets r['mu'], r['sd']."""
    done = []
    cache = {}
    for r in rows:
        day, metric = r["d"]["day"], r["d"]["metric"]
        vals = np.array([r.get(k + "*", np.nan) for k in keys])
        r["mu"] = r["sd"] = np.nan
        ok = not np.isnan(vals).any()
        if ok:
            ck = (day, metric if by_metric else "")
            if ck not in cache:
                P = [h for h in done if 0 < (day - h[0]).days <= win and (not by_metric or h[1] == metric)]
                if len(P) >= min_n:
                    X = np.array([h[2] - h[2].mean() for h in P])
                    yy = np.array([h[3] - h[2].mean() for h in P])
                    A = X.T @ X + lam * np.eye(len(keys))
                    cache[ck] = np.linalg.solve(A, X.T @ yy)
                else:
                    cache[ck] = None
            beta = cache[ck]
            if beta is not None:
                base = vals.mean() + float((vals - vals.mean()) @ beta)
                s = r["d"]["series"]
                res = [h[4] for h in done if h[5] == s and 0 < (day - h[0]).days <= sd_win and h[4] is not None]
                if len(res) >= 15:
                    r["mu"] = base + float(np.mean(res)) * 0.5
                    r["sd"] = max(float(np.std(np.array(res) - np.mean(res) * 0.5)), sd_floor)
                r["_base"] = base
            done.append((day, metric, vals, r["y"], (r["y"] - r["_base"]) if beta is not None else None, r["d"]["series"]))
    return rows


def brackets(rows, H, mu="mu", sd="sd"):
    """One record per bracket with a usable quote: market logit, model logit, outcome."""
    out = []
    for r in rows:
        if np.isnan(r.get(mu, np.nan)):
            continue
        T = lib.day_T(r["d"], H)
        for lo, hi, b, a, tl, th, yes, tk, later in lib.quotes(r["d"], T):
            if not (np.isfinite(b) and np.isfinite(a)) or not (0 < b < a < 1):
                continue
            p = lib.bprob(r[mu], r[sd], lo, hi)
            out.append(dict(day=r["d"]["day"], series=r["d"]["series"], metric=r["d"]["metric"], city=r["d"]["city"],
                            b=b, a=a, tl=tl, th=th, y=1.0 if yes else 0.0, pm=p, m=lg((a + b) / 2), f=lg(p), tk=tk,
                            lo=lo, hi=hi, later=later, row=r))
    return out


def fit_blend(R, cols=("m", "f")):
    X = np.array([[1.0] + [r[c] for c in cols] for r in R])
    y = np.array([r["y"] for r in R])
    w = np.zeros(X.shape[1])
    w[1] = 1.0
    for _ in range(30):
        p = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))
        g = X.T @ (y - p)
        Hm = (X * (p * (1 - p))[:, None]).T @ X + 1e-6 * np.eye(X.shape[1])
        step = np.linalg.solve(Hm, g)
        w = w + step
        if np.abs(step).max() < 1e-8:
            break
    return w


def filled(r, side, price, life=None):
    """Did a resting buy at `price` fill within `life` hours (None = until close)?"""
    L = r["later"]
    if L is None or not len(L):
        return False
    if life is not None:
        L = L[:life]
    if side == "yes":
        v = L[:, 3]
        return bool(np.isfinite(v).any() and np.nanmin(v) <= price + 1e-9)
    v = L[:, 4]
    return bool(np.isfinite(v).any() and np.nanmax(v) >= (1 - price) - 1e-9)


def maker_trades(R, w, thr, cols=("m", "f"), life=None, inside=0.01, all_orders=False):
    """Resting buys `inside` better than the best bid/offer where blend - price >= thr;
    filled on a later trade at/through the price within `life` hours.
    Returns list of (day, side, price, pnl, record, filled)."""
    out = []
    for r in R:
        x = [1.0] + [r[c] for c in cols]
        p = sig(float(np.dot(w, x)))
        for side, px, q, win in (("yes", round(r["b"] + inside, 2), p, r["y"]),
                                 ("no", round(1 - (r["a"] - inside), 2), 1 - p, 1 - r["y"])):
            other = r["a"] if side == "yes" else 1 - r["b"]
            if not (px < other - 1e-9 and 0.03 <= px <= 0.97 and q - px >= thr):
                continue
            f = filled(r, side, px, life)
            if f or all_orders:
                out.append((r["day"], side, px, win - px, r, f))
    return out


def cl(vals, groups):
    if not len(vals):
        return float("nan"), float("nan")
    by, cnt = defaultdict(float), defaultdict(int)
    for v, g in zip(vals, groups):
        by[g] += v
        cnt[g] += 1
    n = len(vals)
    m = sum(vals) / n
    return m, (sum((by[g] - m * cnt[g]) ** 2 for g in by) ** 0.5) / n


def walk(R, thr=0.06, cols=("m", "f"), start=None, train_days=183, **kw):
    """Monthly walk-forward: fit the blend on the previous `train_days`, trade the month."""
    months = sorted({(r["day"].year, r["day"].month) for r in R})
    trades, coefs = [], {}
    for ym in months:
        if start and ym < start:
            continue
        first = min(r["day"] for r in R if (r["day"].year, r["day"].month) == ym)
        tr = [r for r in R if 0 < (first - r["day"]).days <= train_days]
        if len(tr) < 1500:
            continue
        w = fit_blend(tr, cols)
        coefs[ym] = w
        te = [r for r in R if (r["day"].year, r["day"].month) == ym]
        trades += maker_trades(te, w, thr, cols, **kw)
    return trades, coefs


def summary(trades, label=""):
    if not trades:
        return f"{label}: no trades"
    m, se = cl([t[3] for t in trades], [t[0] for t in trades])
    return f"{label}: n={len(trades):5d}  {100 * m:+5.1f}c +-{100 * se:.1f} per contract  total ${sum(t[3] for t in trades):+.2f}"
