"""Paper test: late-day limit orders on daily temperature markets (started Nov 11, 2026).

Research (research_wx/RESULTS.md, 13 months of Kalshi prices): from about noon to the
evening of the day itself, the market pays too much for brackets that need the
temperature to pass the low (or high) the station has already recorded. Selling those
brackets (buying NO 1c inside the offer, only where the spread is 4c or wider) earned
about +5.7c per filled contract on lows from Oct 2025 to Aug 2026 and +3.7c in
Sep-Nov 2026. Highs showed it only in the cool season.

This module only keeps score. Nothing is sent to Kalshi. For each of today's markets,
once between `hours_max` and `hours_min` hours before close, it writes down the orders
the rule would rest:

  kind "need":  buy NO on each bracket beyond the extreme so far (lows: below the low
                so far; highs: above the high so far)
  kind "holds": buy YES on the bracket that holds the extreme so far

After close the order counts as filled if a real trade printed at our price or better
(same check as the day-before paper orders), and it is graded against the settlement.
Orders live in data/paper_late.csv.
"""
from __future__ import annotations

import csv
import logging
import math
from datetime import datetime, timedelta

from . import kalshi
from .util import DATA_DIR, parse_iso

log = logging.getLogger("wxbot")

ORDERS = DATA_DIR / "paper_late.csv"
FIELDS = ["id", "placed_at", "ticker", "event", "series", "city", "metric", "date", "bracket",
          "kind", "side", "price", "bid", "ask", "extreme", "current", "hours_left",
          "close", "status", "touched", "through", "result", "pnl", "settled_at", "trend"]
DEFAULTS = {"hours_max": 12.0, "hours_min": 5.0, "min_spread": 0.04, "min_readings": 3,
            "max_reading_age_hours": 2.0, "min_price": 0.03, "max_price": 0.97,
            # Nov 15 filters (research_wx/RESULTS_filters.md; Alex Nov 15 1:32 AM ET: "if a change
            # clears the bar then implement it"). They only skip or delay orders, never add any.
            # A skipped bracket can still be written at a later scan once it passes.
            #  - highs "holds": wait while the temperature is still rising (up 1F+ over the
            #    last hour, or unknown). Replay (20 cities) +0.5 -> +5.0c/ct Oct25-Aug26,
            #    holdout Sep-Nov26 +2.2 -> +4.7c (difference +2.5c, 95% +0.5..+4.3).
            #  - min_spread_by: per "metric/kind" minimum spread on top of min_spread.
            "min_spread_by": {},
            "wait_while_rising": ["high/holds"], "rise_f": 1.0, "trend_hours": 1.0}


def load_orders() -> list[dict]:
    if not ORDERS.exists():
        return []
    with open(ORDERS, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_orders(rows: list[dict]) -> None:
    ORDERS.parent.mkdir(parents=True, exist_ok=True)
    tmp = ORDERS.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(ORDERS)


def extreme_so_far(metric: str, obs, start: datetime, now: datetime, params: dict):
    """(extreme, latest reading) from the precise station readings of this climate day,
    or None when there are too few or they are stale."""
    pts = [o for o in (obs.temps if obs else []) if o.precise and start <= o.time <= now]
    if len(pts) < int(params["min_readings"]):
        return None
    if now - pts[-1].time > timedelta(hours=float(params["max_reading_age_hours"])):
        return None
    vals = [o.temp_f for o in pts]
    return (max(vals) if metric == "high" else min(vals)), vals[-1]


def trend_toward(metric: str, obs, now: datetime, params: dict) -> float | None:
    """How far the temperature moved toward a new extreme over the last `trend_hours`
    (highs: rise, lows: drop), in F. None when no precise reading sits near that time."""
    pts = [o for o in (obs.temps if obs else []) if o.precise and o.time <= now]
    if len(pts) < 2:
        return None
    back = now - timedelta(hours=float(params.get("trend_hours", 1.0)))
    old = [o for o in pts if o.time <= back]
    if not old or back - old[-1].time > timedelta(minutes=90):
        return None
    d = pts[-1].temp_f - old[-1].temp_f
    return d if metric == "high" else -d


def plan_orders(metric: str, markets: list[dict], extreme: float, params: dict,
                trend: float | None = None) -> list[dict]:
    """Paper orders for one event given the extreme recorded so far. `trend`: the last
    hour's move toward a new extreme (trend_toward), used by wait_while_rising."""
    k = math.floor(extreme + 0.5)                      # whole degree the station is at
    by_spread = {str(a): float(b) for a, b in (params.get("min_spread_by") or {}).items()}
    waiting = {str(x) for x in (params.get("wait_while_rising") or [])}
    rising = trend is None or trend >= float(params.get("rise_f", 1.0)) - 1e-9
    out = []
    for m in markets:
        br = kalshi.parse_bracket(m)
        bid, ask = kalshi.price(m, "yes_bid"), kalshi.price(m, "yes_ask")
        if br is None or bid is None or ask is None or not (0 < bid < ask < 1):
            continue
        if ask - bid < float(params["min_spread"]) - 1e-9:
            continue
        lo, hi = br
        holds = (lo is None or k >= lo) and (hi is None or k <= hi)
        need = (hi is not None and hi < k) if metric == "low" else (lo is not None and lo > k)
        key = f"{metric}/{'need' if need else 'holds'}"
        if (need or holds) and ask - bid < by_spread.get(key, 0.0) - 1e-9:
            continue                                                # Nov 15 spread filter
        if (need or holds) and key in waiting and rising:
            continue                                                # wait: still heading for a new extreme
        if need:
            side, price = "no", round(1 - (ask - 0.01), 2)          # sell YES at ask - 1c
        elif holds:
            side, price = "yes", round(bid + 0.01, 2)               # buy YES at bid + 1c
        else:
            continue                                                # already ruled out
        if not (float(params["min_price"]) <= price <= float(params["max_price"])):
            continue
        out.append({"ticker": m.get("ticker", ""), "bracket": kalshi.bracket_label(lo, hi),
                    "kind": "need" if need else "holds", "side": side, "price": price,
                    "bid": bid, "ask": ask})
    return out


def place(orders: list[dict], today: dict, now: datetime, params: dict | None = None) -> int:
    """today: event -> (SeriesInfo, markets, Observations, climate-day start).
    Writes each bracket's order once, the first scan inside the time window."""
    P = {**DEFAULTS, **(params or {})}
    have = {o["id"] for o in orders}
    placed = 0
    for ev, (si, ms, obs, start) in today.items():
        closes = [c for c in (parse_iso(m.get("close_time")) for m in ms) if c]
        if not closes:
            continue
        hours = (min(closes) - now).total_seconds() / 3600
        if not (float(P["hours_min"]) <= hours <= float(P["hours_max"])):
            continue
        got = extreme_so_far(si.metric, obs, start, now, P)
        if got is None:
            continue
        ext, cur = got
        day = kalshi.event_date(ev)
        tr = trend_toward(si.metric, obs, now, P)
        for o in plan_orders(si.metric, ms, ext, P, tr):
            oid = f"{o['ticker']}|{o['side']}"
            if oid in have:
                continue
            have.add(oid)
            orders.append({
                "id": oid, "placed_at": now.isoformat(timespec="seconds"), "ticker": o["ticker"],
                "event": ev, "series": si.ticker, "city": si.city, "metric": si.metric,
                "date": day.isoformat() if day else "", "bracket": o["bracket"], "kind": o["kind"],
                "side": o["side"], "price": f"{o['price']:.2f}", "bid": f"{o['bid']:.2f}",
                "ask": f"{o['ask']:.2f}", "extreme": f"{ext:.1f}", "current": f"{cur:.1f}",
                "hours_left": f"{hours:.1f}", "close": min(closes).isoformat(timespec="seconds"),
                "trend": "" if tr is None else f"{tr:.1f}",
                "status": "open"})
            placed += 1
    return placed


def settle(http, orders: list[dict], now: datetime) -> int:
    from .daybefore import settle as _settle          # same fill check and grading
    return _settle(http, orders, now)


# ---------------------------------------------------------------- report

def _line(label: str, rows: list[dict]) -> str:
    if not rows:
        return f"| {label} | 0 | | | |"
    pnl = [float(o["pnl"]) for o in rows]
    cost = sum(float(o["price"]) for o in rows)
    by_day: dict[str, float] = {}
    for o, v in zip(rows, pnl):
        by_day[o["date"]] = by_day.get(o["date"], 0.0) + v
    n = len(rows)
    mean = sum(pnl) / n
    se = math.sqrt(sum((v - mean * sum(1 for o in rows if o["date"] == d)) ** 2
                       for d, v in by_day.items())) / n
    return (f"| {label} | {n} | {sum(v > 0 for v in pnl)} | ${sum(pnl):+.2f} on ${cost:.2f} "
            f"| {mean * 100:+.1f}c ±{se * 100:.1f} |")


def report_section(orders: list[dict]) -> list[str]:
    lines = ["## Paper test: late-day weather orders", "",
             "No money. From midday on, the rule sells the brackets that still need the "
             "temperature to pass today's low/high so far (kind need, buys NO) and buys the bracket "
             "that holds it (kind holds). Filled = a real trade printed at our price or better. "
             "Backtest for lows, need: +3.7c per contract in Sep-Nov 2026. "
             "The lows, need orders also trade real money (strategy weather_late).", ""]
    if not orders:
        return lines + ["No orders yet.", ""]
    done = [o for o in orders if o.get("status") in ("filled", "unfilled")]
    filled = [o for o in done if o["status"] == "filled"]
    lines += [f"Written {len(orders)} · open {sum(o.get('status') == 'open' for o in orders)} "
              f"· graded {len(done)} · filled {len(filled)}", "",
              "| Group | Fills | Won | P&L | Per contract (± 1 se) |", "|---|---|---|---|---|"]
    for metric in ("low", "high"):
        for kind in ("need", "holds"):
            lines.append(_line(f"{metric}s, {kind}",
                               [o for o in filled if o["metric"] == metric and o["kind"] == kind]))
    lines.append("")
    return lines
