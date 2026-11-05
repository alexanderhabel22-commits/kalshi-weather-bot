"""Walk-forward backtest of a day-ahead temperature model against Kalshi prices.

Uses the files written by backtest_fetch (settled markets + hourly candles,
Open-Meteo previous-run forecasts). For every settled city-day:

  1. forecast = each model's day-ahead (previous_day1/2) max or min over the
     NWS climate day (local standard time);
  2. the model is fit only on earlier days (walk-forward, no peeking):
     per-city bias and spread from the trailing `window` days, models
     weighted by their trailing accuracy;
  3. bracket probabilities from a normal distribution on whole degrees;
  4. compared with the market's price at a fixed time before close, and a
     simple trading rule is simulated at the ask, after fees.
"""
from __future__ import annotations

import glob
import gzip
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import kalshi
from .stations import default_catalog

UTC = timezone.utc


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _read(path):
    with gzip.open(path, "rb") as fh:
        return json.loads(fh.read())


def std_offset(tz: str) -> timedelta:
    return ZoneInfo(tz).utcoffset(datetime(2026, 1, 15))


def ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bracket_prob(mu: float, sd: float, lo: int | None, hi: int | None) -> float:
    """P(rounded value in [lo, hi]) for a normal(mu, sd) on whole degrees."""
    a = -math.inf if lo is None else (lo - 0.5 - mu) / sd
    b = math.inf if hi is None else (hi + 0.5 - mu) / sd
    return max(0.0, min(1.0, (ncdf(b) if b != math.inf else 1.0) - (ncdf(a) if a != -math.inf else 0.0)))


@dataclass
class Day:
    series: str
    city: str
    metric: str
    day: date
    actual: float
    fc: dict          # model -> {"d1": value, "d2": value}
    brackets: list    # (ticker, lo, hi, result_yes, quotes{h: (bid, ask)})


def load(root: Path, hours=(36, 24, 12)) -> list[Day]:
    cat = {si.ticker: si for si in default_catalog()}
    fc_cache: dict[str, dict] = {}
    days: dict[tuple, Day] = {}
    for p in sorted(glob.glob(str(root / "kalshi*" / "*.json.gz"))):
        d = _read(p)
        si = cat.get(d["series"])
        if si is None:
            continue
        st = si.station
        if st.icao not in fc_cache:
            fp = root / "forecasts" / f"{st.icao}.json.gz"
            fc_cache[st.icao] = _read(fp) if fp.exists() else {}
        for m in d["markets"]:
            res = m.get("result")
            val = _f(m.get("expiration_value"))
            if res not in ("yes", "no") or val is None:
                continue
            br = kalshi.parse_bracket(m)
            if br is None:
                continue
            dd = kalshi.event_date(m.get("event_ticker", ""))
            if dd is None:
                continue
            key = (d["series"], dd)
            if key not in days:
                days[key] = Day(d["series"], si.city, si.metric, dd, val,
                                day_forecasts(fc_cache[st.icao], dd, st.tz, si.metric), [])
            close = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")).timestamp()
            cs = d["candles"].get(m["ticker"]) or []
            quotes = {}
            for h in hours:
                t = close - h * 3600
                prior = [c for c in cs if c["end_period_ts"] <= t]
                if not prior:
                    continue
                c = prior[-1]
                bid = _f((c.get("yes_bid") or {}).get("close_dollars"))
                ask = _f((c.get("yes_ask") or {}).get("close_dollars"))
                if bid is None or ask is None:
                    continue
                quotes[h] = (bid, ask)
            days[key].brackets.append((m["ticker"], br[0], br[1], res == "yes", quotes))
    return sorted(days.values(), key=lambda x: (x.day, x.series))


def day_forecasts(fc: dict, d: date, tz: str, metric: str) -> dict:
    off = std_offset(tz)
    start = datetime(d.year, d.month, d.day, tzinfo=UTC) - off
    want = {(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(24)}
    out = {}
    for model, h in fc.items():
        times = h.get("time") or []
        idx = [i for i, t in enumerate(times) if t in want]
        if len(idx) < 20:
            continue
        rec = {}
        for tag, var in (("d0", "temperature_2m"), ("d1", "temperature_2m_previous_day1"),
                         ("d2", "temperature_2m_previous_day2")):
            vals = [h[var][i] for i in idx if h.get(var) and h[var][i] is not None]
            if len(vals) >= 20:
                rec[tag] = max(vals) if metric == "high" else min(vals)
        out[model] = rec
    return out


# ---------------------------------------------------------------- model

def predict(history: list[Day], today: Day, lead: str, window: int = 45,
            min_hist: int = 15, sd_infl: float = 1.0, sd_floor: float = 1.2,
            spread_sd: bool = False):
    """Walk-forward per-city forecast from earlier days only. Returns (mu, sd)."""
    past = [h for h in history if h.series == today.series and h.day < today.day
            and (today.day - h.day).days <= window]
    models = [m for m in today.fc if lead in today.fc[m]]
    if not models:
        return None
    if len(past) < min_hist:
        return None
    # inverse-MSE model weights, bias-corrected per model
    w, bias = {}, {}
    for m in models:
        errs = [h.actual - h.fc[m][lead] for h in past if m in h.fc and lead in h.fc[m]]
        if len(errs) < min_hist:
            continue
        b = sum(errs) / len(errs)
        mse = sum((e - b) ** 2 for e in errs) / len(errs)
        bias[m], w[m] = b, 1.0 / max(mse, 0.25)
    if not w:
        return None
    tot = sum(w.values())

    def combo(day: Day):
        if not all(m in day.fc and lead in day.fc[m] for m in w):
            return None
        return sum(w[m] * (day.fc[m][lead] + bias[m]) for m in w) / tot

    def spread(day: Day) -> float:
        v = [day.fc[m][lead] + bias[m] for m in w]
        if len(v) < 2:
            return 0.0
        mean = sum(v) / len(v)
        return math.sqrt(sum((x - mean) ** 2 for x in v) / (len(v) - 1))

    pairs = [(h.actual - c, spread(h)) for h in past if (c := combo(h)) is not None]
    mu = combo(today)
    if mu is None or len(pairs) < min_hist:
        return None
    mse = sum(r * r for r, _ in pairs) / len(pairs)
    if spread_sd:
        # error variance = a + b * spread^2, fit by least squares on past days
        xs = [s_ * s_ for _, s_ in pairs]
        ys = [r * r for r, _ in pairs]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        vx = sum((x - mx) ** 2 for x in xs)
        b = max(0.0, sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx) if vx > 0 else 0.0
        a = max(my - b * mx, 0.5)
        var = a + b * spread(today) ** 2
    else:
        var = mse
    sd = math.sqrt(var) * sd_infl
    return mu, max(sd, sd_floor)


# ---------------------------------------------------------------- evaluation

def fee(p: float) -> float:
    return math.ceil(round(0.07 * p * (1 - p) * 100, 6)) / 100


def evaluate(days: list[Day], lead: str = "d1", hour: int = 24, min_edge: float = 0.05,
             start: date | None = None, end: date | None = None, **kw) -> dict:
    st = defaultdict(float)
    by_city = defaultdict(lambda: defaultdict(float))
    trades = []
    for d in days:
        if (start and d.day < start) or (end and d.day > end):
            continue
        pr = predict(days, d, lead, **kw)
        if pr is None:
            continue
        mu, sd = pr
        st["days"] += 1
        st["abs_err"] += abs(d.actual - mu)
        st["sd"] += sd
        for tk, lo, hi, won, q in d.brackets:
            if hour not in q:
                continue
            bid, ask = q[hour]
            if not (0 < bid < ask < 1):
                continue
            p = bracket_prob(mu, sd, lo, hi)
            mid = (bid + ask) / 2
            y = 1.0 if won else 0.0
            st["n"] += 1
            st["brier_model"] += (p - y) ** 2
            st["brier_market"] += (mid - y) ** 2
            # trading rule at the ask, after fees
            for side, prob, price, win in (("yes", p, ask, won), ("no", 1 - p, 1 - bid, not won)):
                if not (0.03 <= price <= 0.97):
                    continue
                edge = prob - price - fee(price)
                if edge >= min_edge:
                    pnl = (1.0 if win else 0.0) - price - fee(price)
                    trades.append((d.day, d.series, tk, side, round(prob, 3), price, pnl))
                    st["trades"] += 1
                    st["pnl"] += pnl
                    st["staked"] += price + fee(price)
                    st["wins"] += 1 if win else 0
                    c = by_city[d.city]
                    c["trades"] += 1
                    c["pnl"] += pnl
    n, k = st["n"] or 1, st["days"] or 1
    return {
        "days": int(st["days"]), "brackets": int(st["n"]),
        "mae": st["abs_err"] / k, "avg_sd": st["sd"] / k,
        "brier_model": st["brier_model"] / n, "brier_market": st["brier_market"] / n,
        "trades": int(st["trades"]), "wins": int(st["wins"]), "pnl": round(st["pnl"], 2),
        "roi": st["pnl"] / st["staked"] if st["staked"] else 0.0,
        "by_city": {c: dict(v) for c, v in by_city.items()}, "trade_list": trades,
    }
