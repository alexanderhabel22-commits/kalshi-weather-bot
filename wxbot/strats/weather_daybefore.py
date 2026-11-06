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
from ..util import HttpError, parse_iso, read_json

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
