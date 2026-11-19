"""PAPER test: favorites in MID-SIZE Entertainment events (300k - 1M contracts traded).

Why (thread "Widen big-event favorites coverage", Nov 19, 2026): the live favorites
rule only trades events with 1M+ contracts traded. The old studies (7-day window)
found 300k-1M events earned only about +0.5c, so they were left out. Since Nov 13
the live rule bids only in the last 24 hours, and replaying THAT rule on the Nov 12
audit data (Jan 2025 - Nov 2026, research_main/replay.py, markets that close on
their listed schedule) changes the answer for Entertainment:

  Entertainment 300k-1M events, last 24 h, 85-96c, 2 markets per event:
  about +4.3c +- 1.5 per filled order, ~8.5 fills a month, 108 events,
  both halves of the history positive (Rotten Tomatoes, album sales, Love Island,
  Netflix ranks, Grammys...). Politics and Elections at that size LOSE, so they
  stay out. Write-up: /mnt/project-files/analysis/favorites-wider/RESULTS.md.

It is the live favorites rule with a different event-size band and category, so it
never overlaps the live rule's events (those are 1M+). It makes no Kalshi calls of
its own: it reuses the event list the live favorites scan read in the same run.
PAPER only. Never set [trading.favorites_mid] live = true without Alex's OK.
"""
from __future__ import annotations

import logging

from ..trader import Intent, held_by_event
from . import favorites

NAME = "favorites_mid"
log = logging.getLogger("wxbot")
KEYS = ("categories", "min_event_volume", "max_markets_per_event", "min_price", "max_price",
        "min_hours", "max_hours", "contracts")


def extra(events: list[dict], now, mid: dict, live: dict, held: dict | None) -> list[Intent]:
    """The live favorites rule on events between min_event_volume and max_event_volume."""
    p = dict(live)
    for k in KEYS:
        if k in mid:
            p[k] = mid[k]
    p.setdefault("categories", ["Entertainment"])
    p.setdefault("max_hours", 24)
    cap = float(mid.get("max_event_volume", 1_000_000))
    small = [e for e in events
             if sum(favorites._vol(m) for m in e.get("markets") or []) < cap]
    out = []
    for i in favorites.candidates(small, now, p, held):
        out.append(Intent(NAME, i.ticker, i.event, i.side, i.price, int(mid.get("contracts", 1)),
                          i.close, i.title, f"mid-size event; {i.note}", i.volume))
    return out


def propose(ctx) -> list[Intent]:
    scan = favorites.LAST_SCAN
    if not scan or scan.get("at") != ctx.now:
        return []                                  # live scan didn't run this pass (it scans every 30 min)
    live = (ctx.cfg.raw.get("trading", {}) or {}).get("favorites", {}) or {}
    out = extra(scan["events"], ctx.now, ctx.params, live, held_by_event(ctx.orders, NAME, ctx.mode))
    log.info("favorites_mid (paper): %d mid-size Entertainment picks", len(out))
    return out
