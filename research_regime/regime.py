"""Regime-filter search with a strict time holdout. Research only.

For each strategy ledger (one row per trade: t, pnl in cents per contract after fees,
features known before the trade, a cluster key), this:
  1. adds the strategy's OWN recent form: mean P&L of its last 20 / 100 settled trades,
     whether its last settled trade won, its P&L the previous calendar day, and its
     current losing streak (only trades settled before this one was placed);
  2. splits by time: first 60% of trades = TRAIN, last 40% = HOLDOUT;
  3. cuts every feature at its TRAIN terciles and tries 6 filters per feature
     (keep low / mid / high, drop low / mid / high) -> every combination is counted;
  4. picks the best filter on TRAIN only (>= 25% of trades kept, lift t >= 2,
     clustered by day/event) and reports what it does on the HOLDOUT;
  5. reports, across ALL candidates, how often a TRAIN lift kept its sign on the
     HOLDOUT (50% = pure noise).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FILTERS = ["keep_low", "keep_mid", "keep_high", "drop_low", "drop_mid", "drop_high"]


def add_form(df: pd.DataFrame, settle_col: str = "close") -> pd.DataFrame:
    """Own-history features. Uses only trades settled strictly before this trade's time."""
    df = df.sort_values("t").reset_index(drop=True)
    st = df.sort_values(settle_col)
    s_ts = st[settle_col].values
    s_pnl = st.pnl.values
    s_win = st.win.values
    cs = np.concatenate([[0], np.cumsum(s_pnl)])
    n = np.searchsorted(s_ts, df.t.values, side="left")          # settled before t
    def trail(k):
        lo = np.maximum(n - k, 0)
        out = (cs[n] - cs[lo]) / np.maximum(n - lo, 1)
        out[n < k] = np.nan
        return out
    df["form20"] = trail(20)
    df["form100"] = trail(100)
    df["last_win"] = np.where(n > 0, s_win[np.maximum(n - 1, 0)], np.nan)
    # losing streak among the settled trades before t
    streak = np.zeros(len(s_win) + 1)
    for i, w in enumerate(s_win):
        streak[i + 1] = 0 if w else streak[i] + 1
    df["loss_streak"] = streak[n]
    # previous ET calendar day's mean P&L (trades placed that day)
    daymean = df.groupby("day").pnl.mean()
    days = sorted(daymean.index)
    prev = {d: daymean[days[i - 1]] for i, d in enumerate(days) if i > 0}
    df["yday"] = df.day.map(prev)
    return df


def clustered(p: np.ndarray, g: np.ndarray):
    if len(p) < 2:
        return np.nan, np.nan
    s = pd.DataFrame({"p": p, "g": g}).groupby("g").p.agg(["sum", "count"])
    m = p.mean()
    k = len(s)
    if k < 2:
        return m, np.nan
    resid = s["sum"] - m * s["count"]
    se = np.sqrt((resid ** 2).sum() * k / (k - 1)) / len(p)
    return m, se


def mask(x: np.ndarray, cuts, f: str):
    lo = x <= cuts[0]
    hi = x > cuts[1]
    mid = ~lo & ~hi
    ok = np.isfinite(x)
    return ok & {"keep_low": lo, "keep_mid": mid, "keep_high": hi,
                 "drop_low": ~lo, "drop_mid": ~mid, "drop_high": ~hi}[f]


def search(df: pd.DataFrame, feats: list[str], cluster: str, min_keep=0.25):
    df = df.sort_values("t").reset_index(drop=True)
    cut = int(len(df) * 0.6)
    tr, te = df.iloc[:cut], df.iloc[cut:]
    base_tr = clustered(tr.pnl.values, tr[cluster].values)
    base_te = clustered(te.pnl.values, te[cluster].values)
    cands = []
    for f in feats:
        xtr = tr[f].values.astype(float)
        if np.isfinite(xtr).sum() < 30:
            continue
        cuts = np.nanquantile(xtr, [1 / 3, 2 / 3])
        if cuts[0] == cuts[1]:                     # binary-ish feature: split at the value
            cuts = [cuts[0], cuts[0]]
        for fl in FILTERS:
            mtr = mask(xtr, cuts, fl)
            mte = mask(te[f].values.astype(float), cuts, fl)
            if mtr.sum() < max(30, min_keep * len(tr)) or mte.sum() < 10:
                continue
            a = clustered(tr.pnl.values[mtr], tr[cluster].values[mtr])
            # lift = filtered minus all, on the same rows; its s.e. ~ s.e. of the excluded part's gap
            b = clustered(tr.pnl.values[~mtr & np.isfinite(xtr)], tr[cluster].values[~mtr & np.isfinite(xtr)]) \
                if (~mtr & np.isfinite(xtr)).sum() > 10 else (np.nan, np.nan)
            gap_t = (a[0] - b[0]) / np.hypot(a[1], b[1]) if np.isfinite(b[0]) else np.nan
            c = clustered(te.pnl.values[mte], te[cluster].values[mte])
            cands.append(dict(feature=f, filter=fl, cut1=round(cuts[0], 4), cut2=round(cuts[1], 4),
                              keep_tr=mtr.mean(), train=a[0], train_se=a[1], train_lift=a[0] - base_tr[0],
                              gap_t=gap_t, keep_te=mte.mean(), test=c[0], test_se=c[1],
                              test_lift=c[0] - base_te[0], n_te=int(mte.sum())))
    cd = pd.DataFrame(cands)
    info = dict(n_train=len(tr), n_test=len(te), base_train=base_tr, base_test=base_te,
                train_span=(tr.day.min(), tr.day.max()), test_span=(te.day.min(), te.day.max()),
                n_candidates=len(cd))
    if len(cd):
        pos = cd[cd.train_lift > 0]
        info["sign_kept"] = float((pos.test_lift > 0).mean()) if len(pos) else np.nan
        info["corr"] = float(cd[["train_lift", "test_lift"]].corr(method="spearman").iloc[0, 1])
        ok = cd[cd.gap_t >= 2].sort_values("train", ascending=False)
        info["n_sig_train"] = len(ok)
        info["pick"] = ok.iloc[0].to_dict() if len(ok) else None
    return cd, info


def streak_stats(df: pd.DataFrame):
    """Does a good day follow a good day? Correlation of consecutive daily means, and
    correlation of form20 with the next trade."""
    d = df.groupby("day").pnl.mean()
    ac = float(np.corrcoef(d.values[:-1], d.values[1:])[0, 1]) if len(d) > 5 else np.nan
    x = df.dropna(subset=["form20"])
    c20 = float(np.corrcoef(x.form20, x.pnl)[0, 1]) if len(x) > 30 else np.nan
    return dict(days=len(d), day_autocorr=ac, form20_corr=c20, n=len(df))
