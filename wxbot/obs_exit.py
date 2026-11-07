"""Value of a held weather bracket during the day, from the readings so far plus
the forecast for the hours still left (research_exit/obs_exit_bt.py tests it).

  final extreme = max(observed so far + intra-hour gap, rest-of-day forecast)
  rest-of-day   = weighted, bias-corrected hourly model forecast, shifted by
                  today's forecast error over the last 3 observed hours (x0.6),
                  sd = 1 + 0.12 * hours left (max 4)
Lows use the same code with every temperature negated.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

GAP, OBS_SD, ADJ = 0.35, 0.45, 0.6


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def value(metric: str, lo: int | None, hi: int | None, t: datetime, w0: datetime, w1: datetime,
          readings: list[tuple[datetime, float]], hourly: dict[str, dict[datetime, float]],
          weights: dict[str, float], bias: dict[str, float]) -> float | None:
    """P(CLI value in [lo, hi]) at time t. readings: precise (time, °F) of the CLI day;
    hourly: model -> {top-of-hour UTC: °F}; weights/bias: the day-before model fit."""
    sgn = 1 if metric == "high" else -1
    obs = sorted((tt, sgn * f) for tt, f in readings if w0 <= tt <= t)

    def fc_at(h):
        vals = [(weights[m], hourly[m][h] + bias.get(m, 0.0)) for m in weights
                if h in hourly.get(m, {}) and hourly[m][h] is not None]
        tot = sum(a for a, _ in vals)
        return sum(a * b for a, b in vals) / tot if vals and tot > 0 else None

    errs = []
    for tt, f in obs[-3:]:
        h = tt.replace(minute=0, second=0, microsecond=0) + (timedelta(hours=1) if tt.minute >= 30 else timedelta(0))
        c = fc_at(h)
        if c is not None:
            errs.append(f - sgn * c)
    adj = ADJ * sum(errs) / len(errs) if errs else 0.0
    rest = []
    h = t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    while h <= w1:
        c = fc_at(h)
        if c is not None:
            rest.append(sgn * c + adj)
        h += timedelta(hours=1)
    M = max(f for _, f in obs) if obs else None
    if not rest and M is None:
        return None
    mu = max(rest) if rest else None
    sd = min(1.0 + 0.12 * (w1 - t).total_seconds() / 3600, 4.0)

    def F(x):                       # P(final <= x), signed units
        a = _ncdf((x - (M + GAP)) / OBS_SD) if M is not None else 1.0
        b = _ncdf((x - mu) / sd) if mu is not None else 1.0
        return a * b
    L, U = (lo, hi) if sgn == 1 else ((None if hi is None else -hi), (None if lo is None else -lo))
    up = 1.0 if U is None else F(U + 0.5)
    dn = 0.0 if L is None else F(L - 0.5)
    return max(0.0, min(1.0, up - dn))
