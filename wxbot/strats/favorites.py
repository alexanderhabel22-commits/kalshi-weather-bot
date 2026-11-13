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
Scanning: once a day, the series in those categories with min_series_volume+
contracts traded; every 6 hours, which of them have open events (a light
listing of all open events); every 30 minutes, the open markets of those
series, grouped into events.
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta

from .. import kalshi
from ..trader import Intent, held_by_event, per_event_pick
from ..util import HttpError, parse_iso
from .mentions import _series_markets

NAME = "favorites"
log = logging.getLogger("wxbot")
DEFAULT_CATS = ("Elections", "Politics", "Entertainment")
LAST_SCAN: dict = {}      # this run's open events, reused by the paper favorites_wide shadow


def _vol(m: dict) -> float:
    for k in ("volume_fp", "volume"):
        try:
            if m.get(k) not in (None, ""):
                return float(m[k])
        except (TypeError, ValueError):
            pass
    return 0.0


def big_series(ctx, cats, min_vol: float) -> dict[str, str]:
    """{series ticker: category} for series with min_vol+ contracts traded (daily)."""
    st = ctx.state
    day = ctx.now.date().isoformat()
    if st.get("series_day") == day and st.get("series"):
        return st["series"]
    found = {}
    for cat in cats:
        js = ctx.http.get_json(f"{kalshi.BASE}/series",
                               params={"category": cat, "include_volume": "true"})
        for x in js.get("series") or []:
            if x.get("ticker") and (x.get("category") or cat) == cat and _vol(x) >= min_vol:
                found[x["ticker"]] = cat
    if found:
        st["series"], st["series_day"] = found, day
    return found


def active_series(ctx, series: dict, max_pages: int = 400) -> list[str]:
    """Big series that have open events, refreshed every 6 hours from a light
    listing of all open events (no nested markets)."""
    st = ctx.state
    last = parse_iso(st.get("active_at"))
    if last is not None and ctx.now - last < timedelta(hours=6) and "active" in st:
        return st["active"]
    seen, cursor = set(), None
    for _ in range(max_pages):
        params = {"status": "open", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        js = ctx.http.get_json(f"{kalshi.BASE}/events", params=params)
        for ev in js.get("events") or []:
            s_ = ev.get("series_ticker") or (ev.get("event_ticker") or "").split("-")[0]
            if s_ in series:
                seen.add(s_)
        cursor = js.get("cursor")
        if not cursor:
            break
    else:
        # Nov 13, 2026: Kalshi lists ~21.5k open events (108 pages); a full cap would
        # silently drop big series from the scan, so say so loudly.
        log.warning("favorites: open-event listing hit the %d-page cap; some series may be missed",
                    max_pages)
    st["active"], st["active_at"] = sorted(seen), ctx.now.isoformat(timespec="seconds")
    return st["active"]


def open_events(ctx, series: dict, active: list[str]) -> list[dict]:
    by_ev: dict[str, dict] = {}
    for s_ in active:
        try:
            ms = _series_markets(ctx, s_)
        except HttpError as exc:
            log.info("favorites %s: %s", s_, exc)
            continue
        for m in ms:
            ev = m.get("event_ticker") or m.get("ticker", "").rsplit("-", 1)[0]
            e = by_ev.setdefault(ev, {"event_ticker": ev, "category": series.get(s_, ""),
                                      "markets": []})
            e["markets"].append(m)
    return list(by_ev.values())


def candidates(events: list[dict], now, p: dict, held: dict | None = None) -> list[Intent]:
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
                f"{ev.get('category')} · {side} bid {bid:.2f} / ask {ask:.2f}", _vol(m)))
        out += per_event_pick(sorted(picks, key=lambda i: -i.price), held, per_event)
    return out


def propose(ctx) -> list[Intent]:
    if ctx.now.minute % 30 >= 5 and not os.environ.get("TRADE_FORCE_SCAN"):
        return []
    p = ctx.params
    cats = list(p.get("categories") or DEFAULT_CATS)
    try:
        series = big_series(ctx, cats, float(p.get("min_series_volume", 1_000_000)))
        active = active_series(ctx, series, int(p.get("max_pages", 400)))
        events = open_events(ctx, series, active[:int(p.get("max_series", 250))])
    except HttpError as exc:
        log.warning("favorites scan: %s", exc)
        return []
    LAST_SCAN.clear()
    LAST_SCAN.update(at=ctx.now, events=events)
    out = candidates(events, ctx.now, p, held_by_event(ctx.orders, NAME, ctx.mode))
    big = sum(1 for e in events if sum(_vol(m) for m in e["markets"]) >=
              float(p.get("min_event_volume", 1_000_000)))
    log.info("favorites: %d big series, %d with open events, %d open events, %d over the "
             "volume floor, %d candidates", len(series), len(active), len(events), big, len(out))
    return out
