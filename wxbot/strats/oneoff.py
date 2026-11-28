"""One-off REAL-MONEY orders that Alex OKs by name in the project thread.

Each entry in [trading.oneoff] orders is one buy: ticker, side, a price LIMIT and a contract
count. Every run, while the entry is open (before its `until` time), it buys right away
(immediate-or-cancel) only if the ask is at or below the limit: no chasing. Contracts already
filled for that ticker/side by this strategy count toward the target, so it never buys more
than `count`. All the usual rails still apply (overall/event caps, cash, daily/total stops).
Held to settlement (exit = "hold").

Nov 27, 2026 10:11 PM ET, Alex: "Do what you think is best make me money!" then "do something
you can do bruh": 8 NO on KXU3-26NOV-T4.2 (November unemployment above 4.2%) at 87c or better.
The same rule as fade_leads (+12.6c/ct replay, 22/22 releases); fade_leads already holds 2 NO
there, and 10 total stays inside the 20% per-event cap.
"""
from __future__ import annotations

import logging

from .. import kalshi
from ..trader import Intent
from ..util import parse_iso

NAME = "oneoff"
log = logging.getLogger("wxbot")


def _filled(rows: list[dict], ticker: str, side: str) -> int:
    n = 0
    for r in rows:
        if r.get("strategy") == NAME and r.get("ticker") == ticker and r.get("side") == side:
            try:
                n += int(float(r.get("filled") or 0))
            except (TypeError, ValueError):
                pass
    return n


def propose(ctx) -> list[Intent]:
    orders = [o for o in (ctx.params or {}).get("orders") or [] if o.get("ticker")]
    rows = [r for r in ctx.orders if r.get("mode") == ctx.mode]
    want = []
    for o in orders:
        until = parse_iso(o.get("until") or "")
        if until is not None and ctx.now >= until:
            continue
        side = str(o.get("side", "")).lower()
        need = int(o.get("count", 0)) - _filled(rows, o["ticker"], side)
        if side in ("yes", "no") and need > 0:
            want.append((o, side, need))
    if not want:
        return []
    mk = kalshi.get_markets(ctx.http, [o["ticker"] for o, _, _ in want])
    out = []
    for o, side, need in want:
        m = mk.get(o["ticker"]) or {}
        if m.get("result") or (m.get("status") not in (None, "open", "active")):
            continue
        if side == "yes":
            ask = kalshi.price(m, "yes_ask")
        else:
            yb = kalshi.price(m, "yes_bid")
            ask = None if yb is None else round(1 - yb, 2)
        limit = round(float(o["price"]), 2)
        if ask is None or ask <= 0 or ask > limit + 1e-9:
            log.info("oneoff %s %s: ask %s above limit %.2f, waiting", o["ticker"], side, ask, limit)
            continue
        out.append(Intent(strategy=NAME, ticker=o["ticker"], event=m.get("event_ticker", ""),
                          side=side, price=ask, count=need, close=m.get("close_time", ""),
                          title=m.get("title", ""), note=f"one-off (Alex OK); {o.get('note', '')}".strip(),
                          volume=float(m.get("volume_fp") or m.get("volume") or 0), taker=True))
    return out
