"""Favorites in big events: resting bids on the 85-96c side.

Research (thread "Find longshot edges in every other category", Feb 2025 -
Oct 2026, 45k sampled markets in Elections, Politics, Entertainment, Tech and
small categories): a resting bid on a favorite at 85-97c made about +3.7c per
filled contract in events that traded 1M+ contracts (317 events, 88% up,
positive in all four half-years, back-of-queue fills only). The same trade in
small events LOST money: thin markets are where the longshot buyer knows
something. So this only trades big, liquid events.

Rules here:
  - open events in `categories` (default Elections, Politics, Entertainment)
    whose markets have traded `min_event_volume` contracts so far
  - markets that close in min_hours - max_hours (default 1 h - 7 days)
  - the favorite side (YES or NO) with our bid = best bid + 1c, never crossing
  - our price within [min_price, max_price] (default 85-96c)
  - at most max_markets_per_event per event, highest price first
  - size `contracts` each; the trader adds the event / strategy / overall caps
Scans every 30 minutes (lists all open events).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from .. import kalshi
from ..trader import Intent
from ..util import HttpError, parse_iso

NAME = "favorites"
log = logging.getLogger("wxbot")
DEFAULT_CATS = ("Elections", "Politics", "Entertainment")


def _vol(m: dict) -> float:
    for k in ("volume_fp", "volume"):
        try:
            if m.get(k) not in (None, ""):
                return float(m[k])
        except (TypeError, ValueError):
            pass
    return 0.0


def open_events(ctx, max_pages: int = 40) -> list[dict]:
    out, cursor = [], None
    for _ in range(max_pages):
        params = {"status": "open", "with_nested_markets": "true", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        js = ctx.http.get_json(f"{kalshi.BASE}/events", params=params)
        out.extend(js.get("events") or [])
        cursor = js.get("cursor")
        if not cursor:
            break
    return out


def candidates(events: list[dict], now, p: dict) -> list[Intent]:
    cats = set(p.get("categories") or DEFAULT_CATS)
    lo, hi = float(p.get("min_price", 0.85)), float(p.get("max_price", 0.96))
    min_ev_vol = float(p.get("min_event_volume", 1_000_000))
    per_event = int(p.get("max_markets_per_event", 2))
    count = int(p.get("contracts", 2))
    t_lo = now + timedelta(hours=float(p.get("min_hours", 1)))
    t_hi = now + timedelta(hours=float(p.get("max_hours", 168)))
    out = []
    for ev in events:
        if (ev.get("category") or "") not in cats:
            continue
        ms = ev.get("markets") or []
        if sum(_vol(m) for m in ms) < min_ev_vol:
            continue
        picks = []
        for m in ms:
            close = parse_iso(m.get("close_time"))
            if close is None or not (t_lo <= close <= t_hi):
                continue
            if (m.get("status") or "active") not in ("active", "open"):
                continue
            yb, ya = kalshi.price(m, "yes_bid"), kalshi.price(m, "yes_ask")
            if yb is None or ya is None or not (0 < yb < ya < 1):
                continue
            # favorite side and its book
            if yb >= 0.5:
                side, bid, ask = "yes", yb, ya
            else:
                side, bid, ask = "no", round(1 - ya, 2), round(1 - yb, 2)
            px = round(bid + 0.01, 2)
            if px >= ask:
                px = bid                    # 1c spread: join the best bid
            if not (lo - 1e-9 <= px <= hi + 1e-9):
                continue
            title = m.get("title") or ev.get("title") or m.get("ticker", "")
            sub = m.get("yes_sub_title") or ""
            picks.append(Intent(
                NAME, m["ticker"], ev.get("event_ticker") or m.get("event_ticker") or "", side,
                px, count, m.get("close_time") or "", f"{title} {sub}".strip()[:120],
                f"{ev.get('category')} · {side} bid {bid:.2f} / ask {ask:.2f}"))
        out += sorted(picks, key=lambda i: -i.price)[:per_event]
    return out


def propose(ctx) -> list[Intent]:
    if ctx.now.minute % 30 >= 5:
        return []
    try:
        events = open_events(ctx, int(ctx.params.get("max_pages", 40)))
    except HttpError as exc:
        log.warning("favorites scan: %s", exc)
        return []
    out = candidates(events, ctx.now, ctx.params)
    log.info("favorites: %d open events, %d candidates", len(events), len(out))
    return out
