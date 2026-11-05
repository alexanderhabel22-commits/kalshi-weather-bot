"""Weather day-before limit orders: mirror the paper orders that wxbot.daybefore
placed in this scan (data/paper_orders.csv) as real orders. Off until the
paper results earn it ([trading.weather] enabled = false)."""
from __future__ import annotations

from datetime import timedelta

from ..daybefore import load_orders
from ..trader import Intent
from ..util import parse_iso

NAME = "weather"


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
