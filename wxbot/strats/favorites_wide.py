"""PAPER shadow: big-event favorites keyed on the expected decision date.

Why (main-bot audit, Nov 12, 2026): the favorites rule earned about +3.2c per
filled contract over 22 months, and Elections +6.5c, but most election markets
are decided (and settle) long before their listed close_time, which is often
months or years out. The live rule only bids on markets that CLOSE within 7
days, so it skips most elections and mostly ends up in Rotten Tomatoes scores,
where the replay shows about 0.

This shadow logs 1-contract paper orders on the markets the live rule skips
only because of that: same categories, event-volume floor, 85-96c favorite band
and best-bid+1c price, but the time window is applied to Kalshi's
expected_expiration_time (when the result is expected) instead of close_time.

It never trades real money (keep [trading.favorites_wide] live = false), never
places a market the live rule would place, and makes no Kalshi calls of its
own: it reuses the event list the live favorites scan read in the same run.
"""
from __future__ import annotations

import logging

from ..trader import Intent, held_by_event
from ..util import parse_iso
from . import favorites

NAME = "favorites_wide"
log = logging.getLogger("wxbot")


def extra(events: list[dict], now, wide: dict, live: dict, held: dict | None,
          live_held: dict | None) -> list[Intent]:
    """Favorites picked by the expected-decision-date window that the live rule skips."""
    live_pick = {i.ticker for i in favorites.candidates(events, now, live, live_held)}
    for tks in (live_held or {}).values():
        live_pick |= set(tks)
    shifted, real_close = [], {}
    for ev in events:
        ms = []
        for m in ev.get("markets") or []:
            exp = parse_iso(m.get("expected_expiration_time"))
            close = parse_iso(m.get("close_time"))
            if exp is None or close is None or exp >= close:
                continue                    # no earlier decision date: the live rule already covers it
            real_close[m["ticker"]] = m.get("close_time") or ""
            ms.append(dict(m, close_time=m["expected_expiration_time"]))
        if ms:
            shifted.append(dict(ev, markets=ms))
    out = []
    for i in favorites.candidates(shifted, now, wide, held):
        if i.ticker in live_pick:
            continue
        out.append(Intent(NAME, i.ticker, i.event, i.side, i.price, int(wide.get("contracts", 1)),
                          real_close.get(i.ticker, i.close), i.title,
                          f"decision-date shadow, expected {i.close[:10]}; {i.note}", i.volume))
    return out


def propose(ctx) -> list[Intent]:
    scan = favorites.LAST_SCAN
    if not scan or scan.get("at") != ctx.now:
        return []                                  # live scan didn't run this pass (it scans every 30 min)
    live = (ctx.cfg.raw.get("trading", {}) or {}).get("favorites", {}) or {}
    out = extra(scan["events"], ctx.now, ctx.params, live, held_by_event(ctx.orders, NAME, ctx.mode),
                held_by_event(ctx.orders, "favorites"))
    log.info("favorites_wide (paper shadow): %d decision-date picks", len(out))
    return out
