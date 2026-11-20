"""Jobs/GDP/Starship fade: 2-CONTRACT REAL-MONEY TEST from Nov 20, 2026 (Alex, Nov 20 2:13 PM ET:
"Run em live again, this is test money").

Series: KXU3 (US unemployment rate above X), KXGDP (real GDP growth above X),
KXSPACEXSTARSHIP (Starship flight launches before date X). Same rule and code as macro_fade
(resting NO at best bid + 1c, 71-95c, closing 2 h to 21 days out, up to 3 markets per event).

Research: thread "Dig into unemployment, GDP, SpaceX leads", Nov 20, 2026,
/mnt/project-files/analysis/fade-leads/RESULTS.md. Replay Dec 2024-Nov 2026: +13.0c on 126 fills,
39/39 events up, ZERO losing fills; walk-forward pick (data before Sep 2025) +12.6c on 68 fills after.
HONEST STATUS: zero losses means the real risk is one surprise release, which loses ~86c per
contract on every market of that event at once (up to 3 markets x 2 contracts ~ -$5). ~5 fills/month.

HARD LOSS CAP $6: settled real P&L + the full cost of every open real order/position + the new
order must stay within $6, so even if everything open loses the test cannot lose more than $6.
Once settled losses leave no room for another order it stops for good and sends one push.
The paper twin fade_leads_paper keeps logging the same rule for the 10/30-fill reviews.
"""
from __future__ import annotations

import dataclasses
import logging
import os

from .. import notify
from ..trader import Intent, archived_realized, exposure, held_by_event, realized
from ..util import DATA_DIR, HttpError, read_json, write_json
from . import macro_fade
from .mentions import _series_markets

NAME = "fade_leads"
SERIES = ["KXU3", "KXGDP", "KXSPACEXSTARSHIP"]
STATE = DATA_DIR / "fade_leads_state.json"
FEE = 0.01           # per-contract allowance on top of the price (maker fee is at most ~1c)
DEFAULTS = {"contracts": 2, "max_loss_usd": 6.0, "max_price": 0.95, "min_price": 0.71}
log = logging.getLogger("wxbot")

_CACHE: dict = {}    # one Kalshi read per run, shared with the paper twin


def markets(ctx, p: dict) -> list[dict]:
    key = (ctx.now, tuple(p.get("series") or SERIES))
    if _CACHE.get("key") == key:
        return _CACHE["markets"]
    out = []
    for s in p.get("series") or SERIES:
        try:
            out += _series_markets(ctx, s)
        except HttpError as exc:
            log.info("fade_leads %s: %s", s, exc)
    _CACHE.clear()
    _CACHE.update(key=key, markets=out)
    return out


def candidates(ms: list[dict], p: dict, now, held: dict | None = None, name: str = NAME,
               label: str = "fade leads") -> list[Intent]:
    return [dataclasses.replace(i, strategy=name, note=f"{label}; {i.note}")
            for i in macro_fade.candidates(ms, p, now, held)]


def scan(ctx, p: dict, name: str, label: str) -> list[Intent]:
    if ctx.now.minute % 30 >= 5 and not os.environ.get("TRADE_FORCE_SCAN"):
        return []
    ms = markets(ctx, p)
    out = candidates(ms, p, ctx.now, held_by_event(ctx.orders, name, ctx.mode), name, label)
    log.info("%s: %d open markets, %d candidates", name, len(ms), len(out))
    return out


def budget(ctx, p: dict) -> tuple[float, float]:
    """(room, settled): dollars still allowed at risk under the hard cap, and settled real P&L."""
    settled = realized(ctx.orders, strategy=NAME, mode="live") + archived_realized("live", NAME)
    open_cost = exposure([r for r in ctx.orders if r.get("mode") == "live"], NAME)
    return float(p["max_loss_usd"]) + settled - open_cost, settled


def fit_budget(intents: list[Intent], room: float) -> list[Intent]:
    """Shrink / drop orders so the worst case (every open order fills and loses) stays within the cap."""
    out = []
    for i in intents:
        unit = i.price + FEE
        n = min(i.count, int((room + 1e-9) // unit))
        if n < 1:
            continue
        i.count = n
        room -= n * unit
        out.append(i)
    return out


def stopped(ctx, p: dict, settled: float) -> bool:
    """Settled losses alone leave no room for one contract at the top of the band: stop for good."""
    cap = float(p["max_loss_usd"])
    if cap + settled >= float(p["max_price"]) + FEE:
        return False
    st = read_json(STATE, {}) or {}
    if not st.get("stopped"):
        st["stopped"] = ctx.now.isoformat(timespec="seconds")
        st["pnl"] = round(settled, 2)
        write_json(STATE, st)
        try:
            notify.send(ctx.http, ctx.cfg, "Jobs/GDP/Starship fade: stopped",
                        f"Real-money result ${settled:+.2f} used up the ${cap:.0f} cap. No new real orders; "
                        "the paper copy keeps logging.", priority=3, tags=["warning"])
        except Exception as exc:  # alerts must never block trading
            log.warning("fade_leads stop push failed: %s", exc)
    log.warning("fade_leads: $%.0f cap used ($%+.2f settled); no new orders", cap, settled)
    return True


def propose(ctx) -> list[Intent]:
    p = {**DEFAULTS, **(ctx.params or {})}
    if ctx.mode == "live":
        room, settled = budget(ctx, p)
        if stopped(ctx, p, settled):
            return []
        if room < float(p["min_price"]) + FEE:
            return []
        return fit_budget(scan(ctx, p, NAME, "fade leads"), room)
    return scan(ctx, p, NAME, "fade leads")
