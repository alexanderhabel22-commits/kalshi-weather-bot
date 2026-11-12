"""Late-day lows: mirror the paper orders that wxbot.latepaper wrote in this scan
(data/paper_late.csv) as real orders. REAL money since Nov 11, 2026 (Alex: "implement
the strategy you mentioned with real money; it can replace the strategy you got rid of").

The rule (research_wx/RESULTS.md): from about noon on the day itself, buy NO 1c inside
the offer on each LOW bracket that sits below the low the station has already recorded,
only where the spread is 4c or wider, and hold to settlement. Backtest: +5.7c per filled
contract Oct 2025 - Aug 2026, +3.7c Sep - Nov 2026, about 7 fills a day over 7 cities.

Only the tested part trades: lows, kind "need". Highs and the "holds" orders stay on
paper in data/paper_late.csv. Rails on top of the trader's own caps and stops:
  - the order must still rest (never cross the spread and pay a taker fee);
  - own loss stop: once this strategy's settled real-money P&L is at or below
    -max_loss_pct of the bankroll it stops placing orders (held ones settle normally).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from .. import kalshi
from ..latepaper import load_orders
from ..trader import Intent, archived_realized, realized
from ..util import HttpError, parse_iso

NAME = "weather_late"
log = logging.getLogger("wxbot")


def stopped(ctx) -> bool:
    """True once the strategy's own real-money loss stop is hit."""
    lost = realized(ctx.orders, strategy=NAME, mode="live") + archived_realized("live", NAME)
    limit = float(ctx.params.get("max_loss_pct", 0.10)) * float(ctx.cfg.bankroll)
    if lost <= -limit:
        log.warning("weather_late: loss stop hit ($%+.2f, limit -$%.2f); no new orders", lost, limit)
        return True
    return False


def propose(ctx) -> list[Intent]:
    count = int(ctx.params.get("contracts", 1))
    recent = ctx.now - timedelta(minutes=float(ctx.params.get("max_age_minutes", 30)))
    metrics = set(ctx.params.get("metrics", ["low"]))
    kinds = set(ctx.params.get("kinds", ["need"]))
    picks = []
    for o in load_orders():
        placed = parse_iso(o.get("placed_at"))
        if o.get("status") != "open" or placed is None or placed < recent:
            continue
        if o.get("metric") not in metrics or o.get("kind") not in kinds:
            continue
        picks.append(o)
    if not picks or (ctx.mode == "live" and stopped(ctx)):
        return []
    out = []
    for o in picks:
        price = float(o["price"])
        try:
            m = kalshi.get_market(ctx.http, o["ticker"])
        except HttpError as exc:
            log.warning("weather_late %s: %s", o["ticker"], exc)
            continue
        bid, ask = (kalshi.price(m, "yes_bid"), kalshi.price(m, "yes_ask")) if m else (None, None)
        if bid is None or ask is None or not (0 < bid < ask < 1):
            continue
        # our price on the other side of the book: buying NO at p = offering YES at 1 - p
        level = round(1 - price, 2) if o["side"] == "no" else price
        rests = level > bid + 1e-9 if o["side"] == "no" else level < ask - 1e-9
        if not rests:
            log.info("weather_late %s: market moved, order would cross (bid %.2f ask %.2f); skipped",
                     o["ticker"], bid, ask)
            continue
        out.append(Intent(NAME, o["ticker"], o["event"], o["side"], price, count, o.get("close") or "",
                          f"{o['city']} {o['metric']} {o['date']} {o['bracket']}",
                          f"late-day {o['kind']}: {o.get('metric', 'low')} so far {o.get('extreme')}, {o.get('hours_left')}h left"))
    return out
