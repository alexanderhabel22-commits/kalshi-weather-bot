"""Sell overpriced Nasdaq-100 daily-close ranges before the open (KXNASDAQ100). Started Nov 15, 2026.

Same rule as spx_range (S&P 500), on Kalshi's Nasdaq-100 daily-close markets
(100-point ranges). Before the 9:30 AM ET open the index is frozen at the prior close;
with a normal daily move of ~300 points the middle range is worth at most ~13%, yet
buyers pay 30-50c for YES. Research: thread "Find new Kalshi edges to try", notes in
/mnt/project-files/analysis/range-brackets/RESULTS.md. Back-of-queue replay (a fill only
when a later trade printed above our price), 1c fee:
    Jan 2025 - Sep 2026: sell YES at 35c -> 138 fills, +22.4c per fill (+-3.2), 12% lose
    Sep 8 - Nov 13 2026:  25 fills, +30.0c; every period positive.
    The same rule after the open loses (-3 to -6c): orders are cancelled at 8:45 AM ET.
About 7 fills a month. 1-CONTRACT REAL test (Alex, Nov 14 4:19 PM ET: "Ok sure get it going").

Own loss stop: settled real P&L at or below -$max_loss_usd (or -max_loss_pct of the
bankroll, whichever is smaller) -> no new real orders, one push.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from .. import notify
from ..trader import Intent, archived_realized, held_by_event, realized
from ..util import DATA_DIR, HttpError, read_json, write_json
from . import spx_range as _sx
from .mentions import _series_markets

NAME = "ndx_range"
SERIES = "KXNASDAQ100"
STATE = DATA_DIR / "ndx_range_state.json"
log = logging.getLogger("wxbot")
DEFAULTS = {**_sx.DEFAULTS, "max_loss_usd": 5.0}


def candidates(markets: list[dict], p: dict, now, held: dict | None = None) -> list[Intent]:
    return _sx.candidates(markets, p, now, held, series=SERIES, name=NAME, label="Nasdaq-100 close")


def cancel_due(r: dict, now) -> bool:
    """Resting orders must be gone before the 9:30 AM ET open (8:45 AM ET)."""
    return _sx.cancel_due(r, now)


def stop_limit(p: dict, bankroll: float) -> float:
    return min(float(p["max_loss_usd"]), float(p["max_loss_pct"]) * float(bankroll))


def stopped(ctx, p: dict) -> bool:
    lost = realized(ctx.orders, strategy=NAME, mode="live") + archived_realized("live", NAME)
    limit = stop_limit(p, ctx.cfg.bankroll)
    if lost > -limit:
        return False
    st = read_json(STATE, {}) or {}
    if not st.get("stopped"):
        st["stopped"] = ctx.now.isoformat(timespec="seconds")
        write_json(STATE, st)
        try:
            notify.send(ctx.http, ctx.cfg, "Nasdaq range fade: stopped",
                        f"Real-money result ${lost:+.2f} hit the -${limit:.2f} stop. No new orders.",
                        priority=3, tags=["warning"])
        except Exception as exc:  # alerts must never block trading
            log.warning("ndx_range stop push failed: %s", exc)
    log.warning("ndx_range: loss stop hit ($%+.2f, limit -$%.2f); no new orders", lost, limit)
    return True


def propose(ctx) -> list[Intent]:
    p = {**DEFAULTS, **(ctx.params or {})}
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
        log.info("ndx_range: %s", exc)
        return []
    out = candidates(markets, p, ctx.now, held_by_event(ctx.orders, NAME, ctx.mode))
    log.info("ndx_range: %d open markets, %d candidates", len(markets), len(out))
    return out
