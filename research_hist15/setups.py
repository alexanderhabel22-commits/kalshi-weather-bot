"""Reusable chart-setup scanner: any asset's candles in, setup signals out.

    bars = bars_from_minutes(t, o, h, l, c, size=900)   # or bars_from_windows(npz dict)
    sig  = scan(bars)        # {setup_name: int8 array of +1 (up) / -1 (down) / 0 (no signal)}

Every signal for bar i uses only bars before i (known at bar i's open), so it can be
traded at the open. In-window setups (scan_inside) use the first k minutes of bar i.
Nothing here is Kalshi-specific; evaluate.py judges signals against Kalshi prices.
"""
from __future__ import annotations

import numpy as np


def bars_from_minutes(t, o, h, l, c, size=900):
    t = np.asarray(t, dtype=np.int64)
    b = t // size
    starts = np.flatnonzero(np.r_[True, b[1:] != b[:-1]])
    ends = np.r_[starts[1:], len(t)]
    return dict(t0=b[starts] * size, o=np.asarray(o, float)[starts],
                h=np.maximum.reduceat(np.asarray(h, float), starts),
                l=np.minimum.reduceat(np.asarray(l, float), starts),
                c=np.asarray(c, float)[ends - 1], n=ends - starts)


def bars_from_windows(w):
    p0 = w["p0"]
    path = w["pathf"]
    c = p0 * (1 + path[:, 14])
    return dict(t0=w["t0"], o=p0, h=p0 * (1 + w["hi"] / 1e5), l=p0 * (1 + w["lo"] / 1e5),
                c=c, n=np.isfinite(path).sum(1))


def _roll(x, n, fn):
    """fn over the previous n values, excluding the current one (NaN until filled)."""
    out = np.full(len(x), np.nan)
    if len(x) <= n:
        return out
    from numpy.lib.stride_tricks import sliding_window_view
    v = sliding_window_view(x, n)  # v[j] = x[j:j+n]
    out[n:] = fn(v[:-1], axis=1)
    return out


def _prev(x, k=1):
    out = np.full(len(x), np.nan)
    out[k:] = x[:-k]
    return out


def scan(bars):
    """Pre-bar setups. Returns {name: int8 signal}."""
    o, h, l, c, t0 = bars["o"], bars["h"], bars["l"], bars["c"], bars["t0"]
    contiguous = np.r_[False, np.diff(t0) == (t0[1] - t0[0] if len(t0) > 1 else 0)]
    ret = np.log(c / o)
    vol = np.sqrt(_roll(ret ** 2, 96, np.mean))  # ~1 day of 15-min bars
    r1, h1, l1, c1, o1 = (_prev(x) for x in (ret, h, l, c, o))
    sig = {}

    def put(name, cond, direction):
        s = np.where(cond, direction, 0).astype(np.int8)
        s[~contiguous | ~np.isfinite(vol)] = 0
        sig[name] = s

    z1 = r1 / vol
    for n in (4, 16, 96):  # 1 hour, 4 hours, 1 day
        hh, ll = _roll(h, n, np.max), _roll(l, n, np.min)
        hh1, ll1 = _prev(hh), _prev(ll)
        put(f"breakout_{n}bars_follow", c1 > hh1, 1)
        sig[f"breakout_{n}bars_follow"] += np.where(c1 < ll1, -1, 0).astype(np.int8)
        put(f"breakout_{n}bars_fade", c1 > hh1, -1)
        sig[f"breakout_{n}bars_fade"] += np.where(c1 < ll1, 1, 0).astype(np.int8)
    # volatility squeeze: last 4 bars' range is in the bottom 10% of the past day; trade the
    # direction of the bar that ends the squeeze
    rng4 = (_roll(h, 4, np.max) - _roll(l, 4, np.min)) / c1
    q10 = _roll(np.nan_to_num(rng4, nan=np.inf), 96, lambda v, axis: np.quantile(v, 0.1, axis=axis))
    sq = rng4 <= q10
    put("squeeze_follow", sq & (r1 > 0), 1)
    sig["squeeze_follow"] += np.where(sq & (r1 < 0), -1, 0).astype(np.int8)
    for thr in (2, 3, 4):
        big = np.abs(z1) > thr
        put(f"big_candle_{thr}sd_fade", big, -np.sign(np.nan_to_num(z1)))
        put(f"big_candle_{thr}sd_follow", big, np.sign(np.nan_to_num(z1)))
    # streaks (momentum)
    sgn = np.sign(ret)
    for n in (3, 5):
        same = _roll(sgn, n, np.sum)
        put(f"streak_{n}_follow", np.abs(same) == n, np.sign(np.nan_to_num(same)))
        put(f"streak_{n}_fade", np.abs(same) == n, -np.sign(np.nan_to_num(same)))
    # hammer / shooting star on the previous bar
    body = np.abs(c1 - o1)
    lw = np.minimum(o1, c1) - l1
    uw = h1 - np.maximum(o1, c1)
    full = (h1 - l1)
    with np.errstate(invalid="ignore", divide="ignore"):
        ham = (lw > 2 * body) & (lw > 0.6 * full) & (full / c1 > vol)
        star = (uw > 2 * body) & (uw > 0.6 * full) & (full / c1 > vol)
    put("hammer_star", ham, 1)
    sig["hammer_star"] += np.where(star, -1, 0).astype(np.int8)
    # engulfing
    r2, o2, c2 = _prev(ret, 2), _prev(o, 2), _prev(c, 2)
    eng_up = (r2 < 0) & (r1 > 0) & (c1 > o2) & (o1 < c2)
    eng_dn = (r2 > 0) & (r1 < 0) & (c1 < o2) & (o1 > c2)
    put("engulfing", eng_up, 1)
    sig["engulfing"] += np.where(eng_dn, -1, 0).astype(np.int8)
    # stretch from the 1-day average (mean reversion)
    ma = _roll(c, 96, np.mean)
    dev = (c1 / _prev(ma) - 1) / (vol * np.sqrt(96))
    for thr in (1.5, 2.5):
        put(f"stretch_{thr}_fade", np.abs(dev) > thr, -np.sign(np.nan_to_num(dev)))
    # 4-hour trend follow
    tr = np.log(c1 / _prev(c, 16)) / (vol * 4)
    put("trend_4h_follow", np.abs(tr) > 1.5, np.sign(np.nan_to_num(tr)))
    # time of day (UTC hour) and day of week: direction = always up; evaluation flips if needed
    hour = (t0 // 3600) % 24
    for hr in range(24):
        put(f"hour_{hr:02d}utc_up", hour == hr, 1)
    dow = ((t0 // 86400) + 3) % 7  # 0 = Monday
    for d in range(7):
        put(f"weekday_{d}_up", dow == d, 1)
    return sig


def scan_inside(w, k):
    """In-window setups at minute k, from fetch.py windows. Signal = +1 back the leader
    (expect it to hold more than the hold model says), -1 back the trailing side."""
    path = w["pathf"]
    lead = np.sign(path[:, k - 1] - w["kf"])
    r = w["r"][:, :k]
    vol = w["vol"]
    sig = {}
    first = path[:, k - 1]
    burst = np.abs(first) > 2.5 * vol * np.sqrt(k)
    sig["burst_back_leader"] = np.where(burst, 1, 0).astype(np.int8)
    # leader built in the last 2 minutes (acceleration)
    last2 = path[:, k - 1] - (path[:, k - 3] if k >= 3 else 0)
    acc = (np.sign(last2) == lead) & (np.abs(last2) > 1.5 * vol * np.sqrt(2))
    sig["late_acceleration_back_leader"] = np.where(acc, 1, 0).astype(np.int8)
    # choppy: both up and down excursions large inside the window
    cum = np.nancumsum(r, axis=1)
    swing = (np.nanmax(cum, 1) > 1.5 * vol * np.sqrt(k)) & (np.nanmin(cum, 1) < -1.5 * vol * np.sqrt(k))
    sig["choppy_back_trailer"] = np.where(swing, -1, 0).astype(np.int8)
    # vol spike: this window's minutes much wilder than the past hour
    with np.errstate(invalid="ignore"):
        rv = np.sqrt(np.nanmean(r ** 2, 1))
        spike = rv > 2 * vol
    sig["vol_spike_back_trailer"] = np.where(spike, -1, 0).astype(np.int8)
    # reversal: leader now, but the price moved against it over the last 3 minutes
    if k >= 4:
        back = path[:, k - 1] - path[:, k - 4]
        rev = (np.sign(back) == -lead) & (np.abs(back) > 1.0 * vol * np.sqrt(3))
        sig["fading_leader_back_trailer"] = np.where(rev, -1, 0).astype(np.int8)
    return sig
