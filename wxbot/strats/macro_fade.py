"""Fade longshots in Fed and inflation markets (resting NO limit orders).

Research (thread "Confirm longshot fading in financial markets", Feb 2025 -
Oct 2026, research_fin/): a resting NO order filled when someone buys YES at
5-29c made about +5c to +12c per filled contract after maker fees in the big
Fed and CPI/PCE series, also with back-of-queue fills only, and 13-15 of
17-18 months were up in a walk-forward test. The 1-4c band paid only ~+1.5c,
so it is skipped (it risks 96-99c to make 1-4c).

Big caveat: these markets resolve on a handful of releases a year. One real
surprise (a Fed move nobody priced) hits every NO in that event at once, so
the per-event cap matters more than the average.

Rules here:
  - open markets in [series] (default: KXFED, KXFEDDECISION, KXCPI,
    KXCPIYOY, KXCPICORE, KXPCECORE) closing in min_hours..max_hours
  - our NO price = best NO bid + 1c (never crossing the NO ask)
  - NO price within [min_price, max_price] (default 71-95c = YES 5-29c)
  - at most max_markets_per_event per event, cheapest NO first
  - size `contracts` each; the trader adds the event / strategy / overall caps
Scans every 30 minutes.
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta

from .. import kalshi
from ..trader import Intent
from ..util import HttpError, parse_iso
from .mentions import _series_markets, _vol

NAME = "macro_fade"
SERIES = ["KXFED", "KXFEDDECISION", "KXCPI", "KXCPIYOY", "KXCPICORE", "KXPCECORE"]
log = logging.getLogger("wxbot")


def candidates(markets: list[dict], p: dict, now) -> list[Intent]:
    lo, hi = float(p.get("min_price", 0.71)), float(p.get("max_price", 0.95))
    per_event = int(p.get("max_markets_per_event", 3))
    count = int(p.get("contracts", 1))
    min_vol = float(p.get("min_volume", 500))
    t_lo = now + timedelta(hours=float(p.get("min_hours", 1)))
    t_hi = now + timedelta(hours=float(p.get("max_hours", 504)))
    by_event: dict[str, list] = {}
    for m in markets:
        close = parse_iso(m.get("close_time"))
        if close is None or not t_lo <= close <= t_hi:
            continue
        yb, ya = kalshi.price(m, "yes_bid"), kalshi.price(m, "yes_ask")
        if yb is None or ya is None or not (0 < yb < ya < 1):
            continue
        if _vol(m) < min_vol:
            continue
        no_bid, no_ask = round(1 - ya, 2), round(1 - yb, 2)
        px = round(no_bid + 0.01, 2)
        if px >= no_ask:
            px = no_bid                     # 1c spread: join the best bid
        if not (lo - 1e-9 <= px <= hi + 1e-9):
            continue
        ev = m.get("event_ticker") or m["ticker"].rsplit("-", 1)[0]
        title = m.get("title") or m.get("yes_sub_title") or m["ticker"]
        by_event.setdefault(ev, []).append(Intent(
            NAME, m["ticker"], ev, "no", px, count, m.get("close_time") or "", title[:120],
            f"no bid {no_bid:.2f} / ask {no_ask:.2f}", _vol(m)))
    out = []
    for ev in sorted(by_event):
        out += sorted(by_event[ev], key=lambda i: i.price)[:per_event]
    return out


def propose(ctx) -> list[Intent]:
    if ctx.now.minute % 30 >= 5 and not os.environ.get("TRADE_FORCE_SCAN"):
        return []
    p = ctx.params
    markets = []
    for s in p.get("series") or SERIES:
        try:
            markets += _series_markets(ctx, s)
        except HttpError as exc:
            log.info("macro_fade %s: %s", s, exc)
    out = candidates(markets, p, ctx.now)
    log.info("macro_fade: %d open markets in %d series, %d candidates",
             len(markets), len(p.get("series") or SERIES), len(out))
    return out
