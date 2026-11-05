"""Weather LOCKs, bought with real money (Alex's OK, Nov 5 2026).

A LOCK is a bracket the day's observations have already decided (e.g. the
high already hit 77F, so "74-75F" can't be the high: buy NO). The scan writes
every LOCK it sees to data/lock_signals.json; this strategy re-reads the
market right before ordering and buys at the current ASK, because a resting
bid behind the book doesn't fill (Nov 5: our 64c sat behind a 65c bid while
the ask was 67c). The order is immediate-or-cancel: whatever isn't there at
that price is cancelled, never left resting.

Buy only while the edge still holds at the live ask:
  fair (99.5%) - ask - taker fee >= min_edge_lock, and ask <= max_price_lock
Size = the alert's sizing (fractional Kelly, max_bet_frac_lock of the bankroll);
the trader then applies the per-event (20%) and live (75%) caps, the loss
stops and the cash actually in the account.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from .. import kalshi
from ..strategy import DEFAULTS, kelly_contracts
from ..trader import Intent
from ..util import DATA_DIR, HttpError, parse_iso, read_json

NAME = "locks"
SIGNALS = DATA_DIR / "lock_signals.json"
log = logging.getLogger("wxbot")


def check(sig: dict, m: dict | None, P: dict, p: dict, bankroll: float) -> tuple[Intent | None, str]:
    """(intent, why) for one LOCK signal against the live market."""
    if not m or (m.get("result") or "") or (m.get("status") or "active") not in ("active", "open"):
        return None, "market not open"
    side = sig["side"]
    ask = kalshi.price(m, f"{side}_ask")
    if ask is None and side == "no":
        yb = kalshi.price(m, "yes_bid")
        ask = round(1 - yb, 2) if yb else None
    if not kalshi.tradable(ask):
        return None, "no ask"
    ask = round(ask, 2)
    fair = float(sig.get("fair_p") or P["lock_prob"])
    if ask > float(P["max_price_lock"]):
        return None, f"ask {ask:.2f} above {P['max_price_lock']}"
    if ask < float(p.get("min_price", 0.01)):
        return None, f"ask {ask:.2f} below {p.get('min_price')}"
    edge = fair - ask - kalshi.fee_per_contract(ask)
    if edge < float(P["min_edge_lock"]):
        return None, f"edge gone ({edge:.3f})"
    n = kelly_contracts(fair, ask, bankroll, float(P["kelly_fraction"]), float(P["max_bet_frac_lock"]))
    n = min(n, int(p.get("contracts", n)))
    if n < 1:
        return None, "size 0"
    vol = 0.0
    for k in ("volume_fp", "volume"):
        try:
            vol = float(m.get(k) or 0) or vol
        except (TypeError, ValueError):
            pass
    title = f"LOCK {sig.get('city')} {sig.get('metric')} {sig.get('date')} {sig.get('bracket')}"
    note = (f"obs {sig.get('obs_extreme')} guaranteed {sig.get('guaranteed')}; "
            f"ask {ask:.2f} edge {edge:.3f}")
    return Intent(NAME, sig["ticker"], sig.get("event") or m.get("event_ticker", ""), side, ask, n,
                  sig.get("close") or m.get("close_time") or "", title, note, vol, taker=True), "ok"


def propose(ctx) -> list[Intent]:
    js = read_json(SIGNALS, {}) or {}
    at = parse_iso(js.get("at"))
    max_age = timedelta(minutes=float(ctx.params.get("max_signal_minutes", 15)))
    if at is None or ctx.now - at > max_age:
        return []
    P = {**DEFAULTS, **ctx.cfg.strategy_params}
    bankroll = float(ctx.cfg.bankroll)
    out = []
    for sig in js.get("signals") or []:
        try:
            m = kalshi.get_market(ctx.http, sig["ticker"])
        except HttpError as exc:
            log.warning("locks %s: %s", sig.get("ticker"), exc)
            continue
        it, why = check(sig, m, P, ctx.params, bankroll)
        log.info("locks %s %s: %s", sig.get("ticker"), sig.get("side"), why)
        if it:
            out.append(it)
    return out
