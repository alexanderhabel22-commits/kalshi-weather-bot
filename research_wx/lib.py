"""Shared helpers for the weather research: per city-day table with what each
source said at a decision time, market quotes, market-implied temperature."""
import math
import pickle
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

UTC = timezone.utc
MOS_LAG = {"NBS": 75 * 60, "NBE": 75 * 60, "GFS": 4.5 * 3600, "MEX": 5 * 3600, "MET": 3.5 * 3600,
           "NAM": 3.5 * 3600, "ECM": 8 * 3600, "LAV": 45 * 60}
_OFF = {}


def std_off(tz):
    if tz not in _OFF:
        _OFF[tz] = ZoneInfo(tz).utcoffset(datetime(2026, 1, 15)).total_seconds()
    return _OFF[tz]


def window(day, tz):
    w0 = datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() - std_off(tz)
    return w0, w0 + 86400


def load(path):
    D = pickle.load(open(path, "rb"))
    by = defaultdict(list)
    for m in D["markets"]:
        by[(m["series"], m["day"])].append(m)
    for d in D["days"]:
        d["mk"] = sorted(by[(d["series"], d["day"])], key=lambda m: (-1e9 if m["lo"] is None else m["lo"]))
        d["w0"], d["w1"] = window(d["day"], d["tz"])
    D["days"] = sorted((d for d in D["days"] if not np.isnan(d["actual"])), key=lambda d: (d["day"], d["series"]))
    return D


# ---------------------------------------------------------------- forecasts

def om_extreme(rec, tag, w0, metric):
    """Max/min of an Open-Meteo hourly series over the 24 climate-day hours."""
    a = rec.get(tag)
    if a is None:
        return np.nan
    i = int(round((w0 - rec["t0"]) / 3600))
    if i < 0 or i + 24 > len(a):
        return np.nan
    v = a[i:i + 24]
    if np.isfinite(v).sum() < 20:
        return np.nan
    return np.nanmax(v) if metric == "high" else np.nanmin(v)


def mos_value(runs, model, T, d, pick="x"):
    """Latest run available at T: its max/min for the climate day. (value, run age h)."""
    lag = MOS_LAG.get(model, 3 * 3600)
    best = None
    for rt in runs:
        if rt + lag <= T and (best is None or rt > best):
            best = rt
    for _ in range(6):                       # walk back if the newest run lacks the day
        if best is None:
            return np.nan, np.nan
        v = _mos_pick(runs[best][pick], d)
        if not np.isnan(v):
            return v, (T - best) / 3600
        older = [rt for rt in runs if rt < best]
        best = max(older) if older else None
    return np.nan, np.nan


def _mos_pick(rows, d):
    # Max: the row stamped 00Z after the local afternoon. Min: the row stamped 12Z
    # of the local morning. Target = the stamp closest to local 19:00 (max) / 07:00 (min).
    off = std_off(d["tz"])
    day0 = d["w0"]
    want = day0 + (19 if d["metric"] == "high" else 7) * 3600
    best, bd = np.nan, 4 * 3600
    for ft, v in rows:
        hour = datetime.fromtimestamp(ft, UTC).hour
        if (d["metric"] == "high") != (hour == 0):
            continue
        if abs(ft - want) <= bd and not np.isnan(v):
            best, bd = v, abs(ft - want)
    return best
    _ = off


# ---------------------------------------------------------------- market

def quotes(d, T):
    """Per bracket at time T: (lo, hi, bid, ask, later trade low, later trade high, yes)."""
    out = []
    for m in d["mk"]:
        c = m["c"]
        k = np.searchsorted(c[:, 0], T, side="right") - 1
        if k < 0:
            out.append((m["lo"], m["hi"], np.nan, np.nan, np.nan, np.nan, m["yes"], m["ticker"], None))
            continue
        later = c[k + 1:]
        lo_p = np.nanmin(later[:, 3]) if len(later) and np.isfinite(later[:, 3]).any() else np.nan
        hi_p = np.nanmax(later[:, 4]) if len(later) and np.isfinite(later[:, 4]).any() else np.nan
        out.append((m["lo"], m["hi"], c[k, 1], c[k, 2], lo_p, hi_p, m["yes"], m["ticker"], later))
    return out


def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bprob(mu, sd, lo, hi):
    a = 0.0 if lo is None else ncdf((lo - 0.5 - mu) / sd)
    b = 1.0 if hi is None else ncdf((hi + 0.5 - mu) / sd)
    return max(0.0, min(1.0, b - a))


def implied(q):
    """Normal (mu, sd) that best matches the bracket mids; None if quotes are unusable."""
    rows = [(lo, hi, (b + a) / 2) for lo, hi, b, a, *_ in q if np.isfinite(b) and np.isfinite(a) and a >= b]
    if len(rows) < 4 or len(rows) != len(q):
        return None
    tot = sum(p for *_, p in rows)
    if not (0.85 <= tot <= 1.2):
        return None
    ps = [p / tot for *_, p in rows]
    edges = [r[1] + 0.5 for r in rows[:-1] if r[1] is not None]
    if len(edges) != len(rows) - 1:
        return None
    cum = np.cumsum(ps)[:-1]
    best = None
    c = sum(e * p for e, p in zip([edges[0] - 1] + [(a + b) / 2 for a, b in zip(edges, edges[1:])] + [edges[-1] + 1], ps))
    for mu in np.arange(c - 4, c + 4.01, 0.1):
        for sd in (0.8, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0):
            err = sum((ncdf((e - mu) / sd) - cp) ** 2 for e, cp in zip(edges, cum))
            if best is None or err < best[0]:
                best = (err, mu, sd)
    return best[1], best[2]


def day_T(d, H):
    return d["close_ts"] - H * 3600


def month(d):
    return (d["day"].year, d["day"].month)


def iso(t):
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d %H:%M")


_ = timedelta
