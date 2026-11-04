"""Probability model for one city-day's settlement temperature.

Final CLI value = extreme of (what has already been observed today) and (what
is still to come). We simulate it many times:

  observed part  = the day's observed max (or min) so far, nudged for the
                   peak that can fall between hourly readings and for sensor
                   precision;
  remaining part = one ensemble member's max (or min) over the hours still
                   left, corrected for the bias the models have shown against
                   today's actual readings, plus forecast noise that grows
                   with lead time.

Lows are handled by flipping the sign (min T == -max(-T)), so the same code
serves both.
"""
from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .weather import Ensemble, Observations

DEFAULTS = {
    "samples": 20000,
    "sigma_base": 1.0,          # °F noise on the remaining-hours extreme...
    "sigma_per_hour": 0.045,    # ...plus this much per hour of lead time
    "sigma_max": 4.0,
    "spread_inflation": 1.15,   # ensembles are under-dispersed at 2 m
    "bias_tau_hours": 8.0,      # how fast today's observed model bias decays
    "bias_cap": 8.0,
    "anchor_weight_high": 0.5,  # pull toward the NWS forecast before obs exist
    "anchor_weight_low": 0.35,
    "anchor_cap": 6.0,
    "gap_hourly": 0.35,         # typical peak missed between hourly readings
    "gap_6h": 0.15,
    "imprecise_noise": 0.8,     # 5-minute / whole-°C readings: ±1-2 °F
    "imprecise_haircut": 1.0,
    "lock_margin_precise": 0.4,
    "lock_margin_imprecise": 1.5,
    "lock_margin_6h": 0.3,
    "no_obs_sigma_add": 1.0,
}


@dataclass
class DayModel:
    metric: str
    counts: dict[int, int]
    n: int
    obs_extreme: float | None = None       # real °F
    obs_extreme_time: datetime | None = None
    current: float | None = None
    last_obs_time: datetime | None = None
    guaranteed: int | None = None          # high: CLI >= g ; low: CLI <= g
    remaining_hours: float = 0.0
    remaining_median: float | None = None  # real °F, of remaining-hours extreme
    nws: float | None = None
    bias: float = 0.0
    sigma: float = 0.0
    members: int = 0
    notes: list[str] = field(default_factory=list)

    def prob(self, lo: int | None, hi: int | None) -> float:
        tot = 0
        for k, c in self.counts.items():
            if (lo is None or k >= lo) and (hi is None or k <= hi):
                tot += c
        return tot / self.n

    def quantile(self, q: float) -> int:
        target, run = q * self.n, 0
        for k in sorted(self.counts):
            run += self.counts[k]
            if run >= target:
                return k
        return max(self.counts)

    @property
    def median(self) -> int:
        return self.quantile(0.5)

    def lock_side(self, lo: int | None, hi: int | None) -> str | None:
        """'yes'/'no' when observations alone already decide this bracket."""
        g = self.guaranteed
        if g is None:
            return None
        if self.metric == "high":       # CLI >= g
            if hi is not None and hi < g:
                return "no"
            if hi is None and lo is not None and lo <= g:
                return "yes"
        else:                            # CLI <= g
            if lo is not None and lo > g:
                return "no"
            if lo is None and hi is not None and hi >= g:
                return "yes"
        return None


def build_day_model(metric: str, window_start: datetime, window_end: datetime,
                    now: datetime, obs: Observations | None, ens: Ensemble | None,
                    nws_value: float | None, params: dict | None = None,
                    seed: int = 0) -> DayModel | None:
    P = {**DEFAULTS, **(params or {})}
    s = 1.0 if metric == "high" else -1.0
    eff_now = min(now, window_end)
    notes: list[str] = []

    # ---- observations so far in this climate day
    pts = [o for o in (obs.temps if obs else []) if window_start <= o.time <= eff_now]
    groups = (obs.max6 if metric == "high" else obs.min6) if obs else []
    six = [o for o in groups
           if o.time - timedelta(hours=6) >= window_start - timedelta(minutes=5)
           and o.time <= window_end and o.time <= now]
    # Precise readings (METAR 0.1 °C groups, 6-hour extremes) set the observed
    # extreme. Noisy 5-minute readings only count if they clearly exceed it,
    # and then only after a haircut, because the max of many noisy readings
    # is biased upward.
    prec = [(s * o.temp_f, o) for o in pts + six if o.precise]
    impr = [(s * o.temp_f, o) for o in pts if not o.precise]
    obs_x, obs_o = max(prec, key=lambda c: c[0]) if prec else (None, None)
    if impr:
        ix, io = max(impr, key=lambda c: c[0])
        if obs_x is None:
            obs_x, obs_o = ix - P["imprecise_haircut"] / 2, io
        elif ix - P["imprecise_haircut"] * 1.5 > obs_x:
            obs_x, obs_o = ix - P["imprecise_haircut"], io
    last_obs = pts[-1].time if pts else None
    current = pts[-1].temp_f if pts else None
    started = now > window_start
    no_obs = started and not pts
    if no_obs:
        notes.append("no station observations yet")
    elif pts and eff_now - last_obs > timedelta(hours=3):
        notes.append("observations are stale")

    # ---- hours still to come (ensemble)
    lower = last_obs if last_obs else window_start - timedelta(seconds=1)
    idx = [i for i, t in enumerate(ens.times) if lower < t <= window_end] if ens else []

    bias = 0.0
    if ens and pts:
        diffs = []
        for o in pts[-4:]:
            if eff_now - o.time > timedelta(hours=4):
                continue
            j = min(range(len(ens.times)), key=lambda k: abs(ens.times[k] - o.time))
            if abs(ens.times[j] - o.time) <= timedelta(minutes=35):
                m = ens.mean_at(j)
                if m is not None:
                    diffs.append(o.temp_f - m)
        if diffs:
            bias = max(-P["bias_cap"], min(P["bias_cap"], statistics.median(diffs)))

    fut: list[float] = []
    if ens and idx:
        ref = last_obs or max(now, window_start)
        for mem in ens.members:
            vals = []
            for i in idx:
                v = mem[i]
                if v is None:
                    continue
                hrs = max(0.0, (ens.times[i] - ref).total_seconds() / 3600)
                adj = bias * math.exp(-hrs / P["bias_tau_hours"])
                vals.append(s * (v + adj))
            if vals:
                fut.append(max(vals))
    if fut:
        mean = statistics.fmean(fut)
        fut = [mean + P["spread_inflation"] * (x - mean) for x in fut]
        if not pts and nws_value is not None:
            w = P["anchor_weight_high"] if metric == "high" else P["anchor_weight_low"]
            shift = w * (s * nws_value - statistics.median(fut))
            shift = max(-P["anchor_cap"], min(P["anchor_cap"], shift))
            fut = [x + shift for x in fut]

    if obs_x is None and not fut:
        return None

    remaining_h = 0.0
    if idx and ens:
        start_rem = max(now, window_start)
        remaining_h = max(0.0, (window_end - start_rem).total_seconds() / 3600)
        mid = start_rem + (window_end - start_rem) / 2
        lead = max(0.0, (mid - now).total_seconds() / 3600)
    else:
        lead = 0.0
    sigma = min(P["sigma_max"], P["sigma_base"] + P["sigma_per_hour"] * lead
                + (P["no_obs_sigma_add"] if no_obs else 0.0))

    # ---- Monte Carlo
    rng = random.Random(seed)
    n = int(P["samples"])
    counts: dict[int, int] = {}
    precise = bool(obs_o and obs_o.precise)
    gap = P["gap_6h"] if (obs_o and obs_o.source != "obs") else P["gap_hourly"]
    nfut = len(fut)
    for _ in range(n):
        x = -1e9
        if obs_x is not None:
            d = abs(rng.gauss(0.0, gap))
            if not precise:
                d += rng.gauss(0.0, P["imprecise_noise"])
            x = obs_x + d
        if nfut:
            y = fut[rng.randrange(nfut)] + rng.gauss(0.0, sigma)
            if y > x:
                x = y
        k = math.floor(s * x + 0.5)
        counts[k] = counts.get(k, 0) + 1

    # ---- what observations alone guarantee
    guaranteed = None
    if obs_x is not None and obs_o is not None:
        if obs_o.source != "obs":
            margin = P["lock_margin_6h"]
        else:
            margin = P["lock_margin_precise"] if obs_o.precise else P["lock_margin_imprecise"]
        obs_t = s * obs_x
        guaranteed = (math.floor(obs_t - margin + 0.5) if metric == "high"
                      else math.floor(obs_t + margin + 0.5))

    return DayModel(
        metric=metric, counts=counts, n=n,
        obs_extreme=(s * obs_x) if obs_x is not None else None,
        obs_extreme_time=obs_o.time if obs_o else None,
        current=current, last_obs_time=last_obs, guaranteed=guaranteed,
        remaining_hours=remaining_h,
        remaining_median=(s * statistics.median(fut)) if fut else None,
        nws=nws_value, bias=bias, sigma=sigma, members=nfut, notes=notes,
    )
