"""PAPER shadow: big-event favorites, bid only in the last 24 hours before close.

Why (thread "Check entry timing in main-bot strategies", Nov 13, 2026): the live
favorites rule bids the first time a market sits at 85-96c, up to 7 days before
close, and most fills land 3-7 days out. Replaying the Nov 12 audit data
(Jan 2025 - Nov 2026, research_main/replay.py) with orders allowed only in the
last 24 hours: +4.6c per filled contract vs +2.0c for today's rule on the markets
the bot can reach, with about 20% fewer fills, positive in both halves of the
history (+6.1c / +3.2c +- 2.2). Write-up:
/mnt/project-files/analysis/main-timing/RESULTS.md (project files).

The live rule is frozen until its 500-contract review, so this only logs
1-contract PAPER orders with the live rule's own settings and a 24-hour window,
on every market that rule would pick then, including markets the live rule
already holds (paper rows are vetted separately from real ones). Compare
favorites_late with favorites at the favorites review. It makes no Kalshi calls
of its own: it reuses the event list the live favorites scan read in the same run.
Never set [trading.favorites_late] live = true without Alex's OK.
"""
from __future__ import annotations

import logging

from ..trader import Intent, held_by_event
from . import favorites

NAME = "favorites_late"
log = logging.getLogger("wxbot")


def extra(events: list[dict], now, late: dict, live: dict, held: dict | None) -> list[Intent]:
    """The live favorites rule with the window cut to the last `max_hours` (24) hours."""
    p = dict(live)
    for k in ("min_hours", "max_hours", "contracts"):
        if k in late:
            p[k] = late[k]
    p.setdefault("max_hours", 24)
    out = []
    for i in favorites.candidates(events, now, p, held):
        out.append(Intent(NAME, i.ticker, i.event, i.side, i.price, int(late.get("contracts", 1)),
                          i.close, i.title, f"last-24h shadow; {i.note}", i.volume))
    return out


def propose(ctx) -> list[Intent]:
    scan = favorites.LAST_SCAN
    if not scan or scan.get("at") != ctx.now:
        return []                                  # live scan didn't run this pass (it scans every 30 min)
    live = (ctx.cfg.raw.get("trading", {}) or {}).get("favorites", {}) or {}
    out = extra(scan["events"], ctx.now, ctx.params, live, held_by_event(ctx.orders, NAME, ctx.mode))
    log.info("favorites_late (paper shadow): %d last-24h picks", len(out))
    return out
