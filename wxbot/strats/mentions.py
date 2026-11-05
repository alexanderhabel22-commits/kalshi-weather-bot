"""Mentions NO limit orders ("Will X say Y?").

Research (thread "Find edges in non-weather Kalshi markets", 21 months, 3,905
events): a resting order to buy NO at 70-98c made about +4c per filled
contract; the 75-90c band paid +5.7-6c. People overpay for long-shot words.

Rules here:
  - Mentions-category markets that close in 1-72 hours, with some trading
  - our NO price = best NO bid + 1c (still below the NO ask: we never cross)
  - NO price within [min_price, max_price] (default 75-90c)
  - at most max_words_per_event words per event, cheapest NO first
  - size `contracts` each; the trader adds the event / strategy / overall caps
Scans every 30 minutes (each pass lists all open markets closing soon).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from .. import kalshi
from ..trader import Intent
from ..util import HttpError, parse_iso

NAME = "mentions"
log = logging.getLogger("wxbot")


def mentions_series(ctx) -> set[str]:
    st = ctx.state
    day = ctx.now.date().isoformat()
    if st.get("series_day") != day or not st.get("series"):
        js = ctx.http.get_json(f"{kalshi.BASE}/series", params={"category": "Mentions"})
        found = sorted(s["ticker"] for s in js.get("series") or []
                       if (s.get("category") or "Mentions") == "Mentions" and s.get("ticker"))
        if found:
            st["series"], st["series_day"] = found, day
    return set(st.get("series") or [])


def _series_markets(ctx, series: str) -> list[dict]:
    out, cursor = [], None
    for _ in range(5):
        params = {"series_ticker": series, "status": "open", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        js = ctx.http.get_json(f"{kalshi.BASE}/markets", params=params)
        out.extend(js.get("markets") or [])
        cursor = js.get("cursor")
        if not cursor:
            break
    return out


def open_markets(ctx, series: set[str], min_h: float, max_h: float) -> list[dict]:
    """Open markets closing in [min_h, max_h] hours, from the Mentions series
    that have open markets. Which series are active is refreshed every 6 hours
    (one call per series); in between only active series are read."""
    st = ctx.state
    last = parse_iso(st.get("active_at"))
    refresh = last is None or ctx.now - last > timedelta(hours=6) or not st.get("active")
    check = sorted(series) if refresh else st.get("active", [])
    lo, hi = ctx.now + timedelta(hours=min_h), ctx.now + timedelta(hours=max_h)
    out, active = [], []
    for s_ in check:
        try:
            ms = _series_markets(ctx, s_)
        except HttpError as exc:
            log.info("mentions %s: %s", s_, exc)
            continue
        if ms:
            active.append(s_)
        out += [m for m in ms if (c := parse_iso(m.get("close_time"))) and lo <= c <= hi]
    if refresh:
        st["active"], st["active_at"] = active, ctx.now.isoformat(timespec="seconds")
    return out


def _vol(m: dict) -> float:
    for k in ("volume_fp", "volume", "volume_24h_fp", "volume_24h"):
        try:
            if m.get(k) not in (None, ""):
                return float(m[k])
        except (TypeError, ValueError):
            pass
    return 0.0


def candidates(markets: list[dict], series: set[str], p: dict) -> list[Intent]:
    lo, hi = float(p.get("min_price", 0.75)), float(p.get("max_price", 0.90))
    n_words = int(p.get("max_words_per_event", 3))
    count = int(p.get("contracts", 2))
    min_vol = float(p.get("min_volume", 100))
    by_event: dict[str, list] = {}
    for m in markets:
        ev = m.get("event_ticker") or ""
        if ev.split("-")[0] not in series:
            continue
        yb, ya = kalshi.price(m, "yes_bid"), kalshi.price(m, "yes_ask")
        if yb is None or ya is None or not (0 < yb < ya < 1):
            continue
        if _vol(m) < min_vol:
            continue
        no_bid, no_ask = round(1 - ya, 2), round(1 - yb, 2)
        px = round(no_bid + 0.01, 2)
        if px >= no_ask:
            px = no_bid                     # spread is 1c: join the best bid
        if not (lo - 1e-9 <= px <= hi + 1e-9):
            continue
        title = m.get("title") or m.get("yes_sub_title") or m.get("ticker", "")
        by_event.setdefault(ev, []).append(Intent(
            "mentions", m["ticker"], ev, "no", px, count, m.get("close_time") or "",
            title[:120], f"no bid {no_bid:.2f} / ask {no_ask:.2f}"))
    out = []
    for ev in sorted(by_event):
        out += sorted(by_event[ev], key=lambda i: i.price)[:n_words]
    return out


def propose(ctx) -> list[Intent]:
    if ctx.now.minute % 30 >= 5:
        return []
    p = ctx.params
    try:
        series = mentions_series(ctx)
        markets = open_markets(ctx, series, float(p.get("min_hours", 1)), float(p.get("max_hours", 72)))
    except HttpError as exc:
        log.warning("mentions scan: %s", exc)
        return []
    # skip markets whose close we can't read
    markets = [m for m in markets if parse_iso(m.get("close_time"))]
    out = candidates(markets, series, p)
    band = sum(1 for m in markets if (ya := kalshi.price(m, "yes_ask")) and
               float(p.get("min_price", 0.75)) <= 1 - ya + 0.01 <= float(p.get("max_price", 0.90)))
    log.info("mentions: %d series (%d active), %d markets closing soon, %d near the price band, "
             "%d candidates", len(series), len(ctx.state.get("active", [])), len(markets), band, len(out))
    return out
