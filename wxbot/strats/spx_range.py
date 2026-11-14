"""Sell overpriced S&P 500 daily-close ranges before the open (KXINX). Started Nov 13, 2026.

Research (thread "Check Kalshi stock trading for an edge", notes in
research_spx/RESULTS.md): Kalshi's daily S&P 500 close markets are 25-point ranges.
Before the 9:30 AM ET open the index is frozen at the prior close, and with a normal
daily move of ~60-75 points even the middle range is worth at most ~15-20%. Buyers
still pay 30-50c for YES. Replay of every settled KXINX event Feb 2025 - Nov 12 2026,
trades between the prior close and the open, a fill counted only when a buyer later
paid MORE than our price (back of queue), 1c fee assumed:
    sell YES at 35c (= buy NO at 65c): 293 fills, +12.8c per fill, 21% lose,
    +11.5 / +14.7 / +12.1 / +14.8c in the four periods (2025, Jan-Apr, May-Aug, Sep-Nov 2026)
    at 25-30c it is +5 to +8c; at 15-20c about 0 (matches the Nov 5 Financials NO-GO).
About 14 fills a month. Taking YES bids of 35c+ directly (buy NO at the ask) made about
+10c too, so a bid already at/above our level is taken instead of waited for.

Rule, every scan while the next KXINX close is 7.5-23.9 hours away (about 4:05 PM ET
the day before to 8:30 AM ET on the day):
  - take the `n_markets` range ("between") markets with the highest YES price;
  - if the YES bid is at or above `sell_yes`: buy NO at its ask (1 - YES bid), taker;
  - else rest a post-only NO bid at 1 - `sell_yes` (65c).
Every resting order is cancelled at `cancel_hours` before the close (8:45 AM ET), well
before the open, because once the index moves the middle range can be worth far more.
Own loss stop at -max_loss_pct of the bankroll. 1 contract until the 100-contract review.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from .. import kalshi
from ..trader import Intent, archived_realized, held_by_event, realized
from ..util import HttpError, parse_iso
from .mentions import _series_markets

NAME = "spx_range"
SERIES = "KXINX"
log = logging.getLogger("wxbot")
DEFAULTS = {"sell_yes": 0.35, "n_markets": 6, "contracts": 1, "min_hours": 7.5,
            "max_hours": 23.9, "cancel_hours": 7.25, "max_loss_pct": 0.10}


def _mid(m: dict) -> float:
    yb, ya = kalshi.price(m, "yes_bid") or 0.0, kalshi.price(m, "yes_ask") or 0.0
    if ya <= 0 or ya >= 1:
        return yb
    return (yb + ya) / 2


def candidates(markets: list[dict], p: dict, now, held: dict | None = None,
               series: str = SERIES, name: str = NAME, label: str = "S&P 500 close") -> list[Intent]:
    """Also used by ndx_range (same rule on the Nasdaq-100) with its own series/name."""
    held = held or {}
    sell = float(p["sell_yes"])
    lo, hi = now + timedelta(hours=float(p["min_hours"])), now + timedelta(hours=float(p["max_hours"]))
    by_event: dict[str, list] = {}
    for m in markets:
        tk = m.get("ticker") or ""
        if not tk.startswith(series + "-") or (m.get("strike_type") or "") != "between":
            continue
        close = parse_iso(m.get("close_time"))
        if close is None or not lo <= close <= hi:
            continue
        ev = m.get("event_ticker") or tk.rsplit("-", 1)[0]
        by_event.setdefault(ev, []).append(m)
    out = []
    for ev, ms in sorted(by_event.items()):
        mine = held.get(ev, set())
        for m in sorted(ms, key=_mid, reverse=True)[:int(p["n_markets"])]:
            tk = m["ticker"]
            if tk in mine:
                continue
            yb = kalshi.price(m, "yes_bid")
            yb = yb if yb is not None and 0 < yb < 1 else 0.0
            title = (m.get("title") or label) + f" {m.get('yes_sub_title') or tk.rsplit('-', 1)[-1]}"
            vol = float(m.get("volume_fp") or m.get("volume") or 0)
            if yb >= sell - 1e-9:
                out.append(Intent(name, tk, ev, "no", round(1 - yb, 2), int(p["contracts"]),
                                  m.get("close_time") or "", title[:120],
                                  f"YES bid {yb:.2f} >= {sell:.2f}: take", vol, taker=True))
            else:
                out.append(Intent(name, tk, ev, "no", round(1 - sell, 2), int(p["contracts"]),
                                  m.get("close_time") or "", title[:120],
                                  f"rest: sell YES at {sell:.2f} (bid {yb:.2f})", vol))
    return out


def cancel_due(r: dict, now) -> bool:
    """Resting orders of this strategy must be gone before the 9:30 AM ET open."""
    close = parse_iso(r.get("close"))
    return close is not None and now >= close - timedelta(hours=DEFAULTS["cancel_hours"])


def stopped(ctx, p: dict) -> bool:
    lost = realized(ctx.orders, strategy=NAME, mode="live") + archived_realized("live", NAME)
    limit = float(p["max_loss_pct"]) * float(ctx.cfg.bankroll)
    if lost <= -limit:
        log.warning("spx_range: loss stop hit ($%+.2f, limit -$%.2f); no new orders", lost, limit)
        return True
    return False


def propose(ctx) -> list[Intent]:
    p = {**DEFAULTS, **(ctx.params or {})}
    # cheap pre-check: skip the API during the trading day (4 PM ET close = 20:00/21:00 UTC)
    nxt = ctx.now.replace(hour=21, minute=0, second=0, microsecond=0)
    if nxt <= ctx.now:
        nxt += timedelta(days=1)
    if (nxt - ctx.now).total_seconds() / 3600 < float(p["min_hours"]) - 1.5:
        return []
    if ctx.mode == "live" and stopped(ctx, p):
        return []
    try:
        markets = _series_markets(ctx, SERIES)
    except HttpError as exc:
        log.info("spx_range: %s", exc)
        return []
    out = candidates(markets, p, ctx.now, held_by_event(ctx.orders, NAME, ctx.mode))
    log.info("spx_range: %d open markets, %d candidates", len(markets), len(out))
    return out
