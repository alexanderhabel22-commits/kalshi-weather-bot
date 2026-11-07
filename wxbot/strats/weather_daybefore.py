"""Weather day-before limit orders: mirror the paper orders that wxbot.daybefore
placed in this scan (data/paper_orders.csv) as real orders.

Model exit (Alex, Nov 6): about 12 hours before close (midday on the day itself)
the bot re-runs the forecast with the newest model runs and re-scores each held
bracket. `value()` gives that chance for our side; the trader sells at the bid
only when the bid after the taker fee beats it by the margin (8c), e.g. it holds
a 50% bracket at 51c but sells it at about 60c. Backtest (research_exit/, Jan-Nov
2026, 1 contract per order): +$455 vs +$390 for holding over 11 months, better in
all 11 months. With the day-before forecast alone it never fires, so the gain is
the fresher same-day forecast (the archive may be slightly fresher than live).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from .. import kalshi
from ..backtest import bracket_prob
from ..daybefore import PARAMS, _logit, _sig, fetch_models, forecast, load_orders
from ..stations import default_catalog
from ..trader import Intent
from ..util import DATA_DIR, HttpError, parse_iso, read_json

NAME = "weather"
log = logging.getLogger("wxbot")
_FC: dict = {}          # (station, hour) -> hourly forecast, shared within a run


def propose(ctx) -> list[Intent]:
    count = int(ctx.params.get("contracts", 1))
    recent = ctx.now - timedelta(minutes=float(ctx.params.get("max_age_minutes", 30)))
    out = []
    for o in load_orders():
        placed = parse_iso(o.get("placed_at"))
        if o.get("status") != "open" or placed is None or placed < recent:
            continue
        out.append(Intent("weather", o["ticker"], o["event"], o["side"], float(o["price"]), count,
                          o.get("close") or "", f"{o['city']} {o['metric']} {o['date']} {o['bracket']}",
                          f"blend {o['blend_p']} edge {o['edge']}"))
    return out


def value(http, r: dict, m: dict, now, params: dict | None = None) -> float | None:
    """Fresh chance that our side of a held weather bracket wins, or None outside
    the exit window (or when anything is missing: then the bot just holds)."""
    ex = (params if params is not None else read_json(PARAMS) or {}).get("exit") or {}
    tk = r.get("ticker", "")
    o = next((o for o in load_orders() if o.get("ticker") == tk), None)
    close = parse_iso((m or {}).get("close_time") or r.get("close"))
    if not ex or o is None or close is None:
        return None
    sp = ex.get("series", {}).get(o.get("series"))
    si = next((s for s in default_catalog() if s.ticker == o.get("series")), None)
    if not sp or si is None:
        return None
    hours = (close - now).total_seconds() / 3600
    if abs(hours - float(ex["hours_before_close"])) > float(ex.get("window_hours", 0.75)):
        return None
    br = kalshi.parse_bracket(m)
    bid, ask = kalshi.price(m, "yes_bid"), kalshi.price(m, "yes_ask")
    day = kalshi.event_date(m.get("event_ticker") or o.get("event", ""))
    if br is None or day is None or bid is None or ask is None or not (0 < bid < ask < 1):
        return None
    key = (si.station.icao, now.strftime("%Y%m%d%H"))
    if key not in _FC:
        try:
            _FC[key] = fetch_models(http, si.station, sorted(sp["weights"]))
        except HttpError as exc:
            log.warning("weather exit forecast %s: %s", si.station.icao, exc)
            _FC[key] = None
    if not _FC[key]:
        return None
    f = forecast(sp, _FC[key], day, si.station.tz, si.metric)
    if f is None:
        return None
    b = ex["blend"]
    p = _sig(b[0] + b[1] * _logit((bid + ask) / 2) + b[2] * _logit(bracket_prob(f[0], f[1], *br)))
    log.info("weather exit %s: forecast %.1f±%.1f, yes %.2f (bid %.2f ask %.2f)",
             tk, f[0], f[1], p, bid, ask)
    return p if r.get("side") == "yes" else 1 - p


# ---------------------------------------------------------------- live-readings exit (shadow)

SHADOW_CSV = DATA_DIR / "exit_shadow.csv"
SHADOW_COLS = ["at", "row", "ticker", "side", "hours_left", "readings", "obs_max", "p_yes",
               "value", "bid", "would_sell", "note"]
_OBS: dict = {}          # (icao, day) -> precise readings, shared within a run


def _hourly_map(fc: dict) -> dict:
    out = {}
    times = [parse_iso(t) for t in fc.get("time") or []]
    for k, vals in fc.items():
        if k != "time" and isinstance(vals, list):
            out[k] = {t: v for t, v in zip(times, vals) if t is not None and v is not None}
    return out


def obs_value(http, r: dict, m: dict, now, params: dict | None = None):
    """Our side's chance from today's actual readings plus the rest-of-day forecast
    (wxbot.obs_exit), at the check hours in exit_obs. Returns (hours mark, value,
    p_yes, readings used, max reading) or None outside the windows."""
    from .. import obs_exit
    from ..stations import cli_window
    from ..weather import fetch_observations
    P = params if params is not None else read_json(PARAMS) or {}
    ex, series = P.get("exit_obs") or {}, P.get("series") or {}
    o = next((o for o in load_orders() if o.get("ticker") == r.get("ticker")), None)
    close = parse_iso((m or {}).get("close_time") or r.get("close"))
    if not ex or o is None or close is None:
        return None
    hours = (close - now).total_seconds() / 3600
    H = next((h for h in ex["hours_before_close"] if abs(hours - h) <= float(ex.get("window_hours", 0.25))), None)
    sp = series.get(o.get("series"))
    si = next((s for s in default_catalog() if s.ticker == o.get("series")), None)
    br = kalshi.parse_bracket(m)
    bid, ask = kalshi.price(m, "yes_bid"), kalshi.price(m, "yes_ask")
    day = kalshi.event_date(m.get("event_ticker") or o.get("event", ""))
    if H is None or not sp or si is None or br is None or day is None or bid is None or ask is None \
            or not (0 < bid < ask < 1):
        return None
    w0, w1 = cli_window(day, si.station.tz)
    fkey = (si.station.icao, now.strftime("%Y%m%d%H"))
    if fkey not in _FC:
        try:
            _FC[fkey] = fetch_models(http, si.station, sorted(sp["weights"]))
        except HttpError as exc:
            log.warning("weather obs exit forecast %s: %s", si.station.icao, exc)
            _FC[fkey] = None
    okey = (si.station.icao, day.isoformat(), now.strftime("%Y%m%d%H%M"))
    if okey not in _OBS:
        try:
            got = fetch_observations(http, si.station.icao, w0, min(now, w1))
            _OBS[okey] = [(x.time, x.temp_f) for x in got.temps if x.precise]
        except HttpError as exc:
            log.warning("weather obs exit readings %s: %s", si.station.icao, exc)
            _OBS[okey] = None
    if not _FC[fkey] or _OBS[okey] is None:
        return None
    rd = _OBS[okey]
    p_obs = obs_exit.value(si.metric, br[0], br[1], now, w0, w1, rd, _hourly_map(_FC[fkey]),
                           sp["weights"], sp["bias"])
    if p_obs is None:
        return None
    b = ex["blend"][str(H)]
    p = _sig(b[0] + b[1] * _logit((bid + ask) / 2) + b[2] * _logit(p_obs))
    sgn = 1 if si.metric == "high" else -1
    top = sgn * max(sgn * f for _, f in rd) if rd else None
    return H, (p if r.get("side") == "yes" else 1 - p), p, len(rd), top


def shadow(http, r: dict, m: dict, now, params: dict | None = None) -> bool:
    """Log (never trade) what the live-readings exit would do, once per position and
    check hour, to data/exit_shadow.csv. Paper until Alex OKs it."""
    import csv
    import os
    from ..trader import _fee
    P = params if params is not None else read_json(PARAMS) or {}
    got = obs_value(http, r, m, now, P)
    if got is None:
        return False
    H, v, p_yes, n, top = got
    rows = []
    if os.path.exists(SHADOW_CSV):
        with open(SHADOW_CSV, newline="") as fh:
            rows = list(csv.DictReader(fh))
    if any(x["row"] == r.get("id") and x["hours_left"] == str(H) for x in rows):
        return False
    yb, ya = kalshi.price(m, "yes_bid"), kalshi.price(m, "yes_ask")
    bid = yb if r.get("side") == "yes" else round(1 - ya, 2)
    margin = float((P.get("exit_obs") or {}).get("margin", 0.08))
    sell = bid - _fee(bid) >= v + margin
    new = not os.path.exists(SHADOW_CSV)
    with open(SHADOW_CSV, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SHADOW_COLS)
        if new:
            w.writeheader()
        w.writerow({"at": now.isoformat(timespec="seconds"), "row": r.get("id"), "ticker": r.get("ticker"),
                    "side": r.get("side"), "hours_left": H, "readings": n,
                    "obs_max": "" if top is None else f"{top:.1f}", "p_yes": f"{p_yes:.3f}",
                    "value": f"{v:.3f}", "bid": f"{bid:.2f}", "would_sell": "yes" if sell else "no",
                    "note": f"bought {r.get('mode')} @ {r.get('price')}"})
    log.info("weather obs exit (shadow) %s %s H%s: value %.2f bid %.2f -> %s",
             r.get("ticker"), r.get("side"), H, v, bid, "WOULD SELL" if sell else "hold")
    return True
