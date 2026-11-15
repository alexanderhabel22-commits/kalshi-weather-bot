"""Daily live-vs-replay drift check (Nov 15, 2026; plain code, no AI).

Once a day, for every main-bot strategy trading real money, compare what it has really made per
contract (settled events only) with what its replay said it should make. It pushes Alex only when
the gap is real, not noise:

  * "below replay": 20+ settled events and even the optimistic end (90% bootstrap high) of the
    live profit per contract is under the replay's number;
  * "losing": 20+ settled events and the optimistic end is under zero.

Each strategy pushes once per level (a later, worse level pushes again; clearing is logged, not
pushed). Read-only: it never changes sizes, config or orders; the freeze and the milestone reviews
decide what to do about it. State lives in trader_state.json ("drift").
"""
from __future__ import annotations

import logging
from datetime import datetime

from .util import Config, read_json, write_json

log = logging.getLogger("wxbot")

# Replay profit per contract after fees, in cents (sources: project analysis files, Nov 12-14).
REPLAY_C = {
    "favorites": 2.0,        # main-bot audit: ~+2.0c on reachable markets (last-24h entry replay +4.6c)
    "macro_fade": 8.7,       # main-bot audit: Fed/CPI fade +8.7c after fees
    "macro_wide": 6.6,       # gas + jobless-claims fade replay
    "ai_fade": 5.0,          # KXLLM1 fade lead
    "weather_late": 3.9,     # late-day lows replay "need" (+4-6c)
    "rain_evening": 6.1,     # evening rain fade replay +6.1c
    "spx_range": 12.8,       # S&P range fade, per fill
    "ndx_range": 22.0,       # Nasdaq-100 range fade, per fill
    "favorites_wide": None,  # no clean replay number: only the "losing" check applies
}
MIN_EVENTS = 20
EVERY_S = 20 * 3600          # about once a day (the trade job runs every 5 minutes)
LEVELS = {"ok": 0, "below_replay": 1, "losing": 2}


def assess(units: list[dict], replay_c: float | None) -> dict:
    from .review import per_contract_range
    n = sum(u["n"] for u in units)
    pnl = sum(u["pnl"] for u in units)
    out = {"events": len(units), "contracts": n, "live_c": round(100 * pnl / n, 2) if n else None,
           "replay_c": replay_c, "level": "ok"}
    if len(units) < MIN_EVENTS:
        out["level"] = "too_few"
        return out
    ci = per_contract_range(units)
    if ci is None:
        out["level"] = "too_few"
        return out
    lo, hi, _ = ci
    out["low_c"], out["high_c"] = round(100 * lo, 2), round(100 * hi, 2)
    if hi < 0:
        out["level"] = "losing"
    elif replay_c is not None and 100 * hi < replay_c:
        out["level"] = "below_replay"
    return out


def check(rows: list[dict], live: list[str], prev: dict) -> tuple[dict, list[str]]:
    """Returns (new state per strategy, push lines for levels that got worse)."""
    from .review import main_units, NAMES
    units = main_units(rows, live)
    state, pushes = {}, []
    for s in live:
        a = assess(units.get(s, []), REPLAY_C.get(s))
        old = (prev.get(s) or {}).get("pushed_level", "ok")
        a["pushed_level"] = old
        if LEVELS.get(a["level"], 0) > LEVELS.get(old, 0):
            name = NAMES.get(s, s)
            if a["level"] == "losing":
                pushes.append(f"{name}: losing for real. {a['live_c']:+.1f}c/contract over {a['events']} "
                              f"events (best case {a['high_c']:+.1f}c).")
            else:
                pushes.append(f"{name}: running below its replay. {a['live_c']:+.1f}c/contract over "
                              f"{a['events']} events, best case {a['high_c']:+.1f}c vs replay "
                              f"{a['replay_c']:+.1f}c.")
            a["pushed_level"] = a["level"]
        elif a["level"] in ("ok", "too_few"):
            a["pushed_level"] = "ok"       # cleared: a later relapse pushes again
        state[s] = a
    return state, pushes


def run(cfg: Config, now: datetime, http=None, dry_run: bool = False) -> list[str]:
    from . import notify, trader
    st = read_json(trader.STATE, {}) or {}
    d = st.get("drift") or {}
    last = d.get("at")
    try:
        if last and (now - datetime.fromisoformat(last)).total_seconds() < EVERY_S:
            return []
    except ValueError:
        pass
    tcfg = cfg.raw.get("trading", {}) or {}
    live = [k for k, v in tcfg.items() if isinstance(v, dict) and v.get("live")]
    state, pushes = check(trader.load_all(), live, d.get("by") or {})
    st = read_json(trader.STATE, {}) or {}          # re-read: never clobber the trader's own writes
    st["drift"] = {"at": now.isoformat(timespec="seconds"), "by": state}
    if not dry_run:
        write_json(trader.STATE, st)
        if pushes and http is not None:
            try:
                notify.send(http, cfg, "Live results vs replay",
                            "\n".join(pushes) + "\nNothing changes by itself; it's for the next review.",
                            priority=3, tags=["chart_with_downwards_trend"])
            except Exception as exc:  # noqa: BLE001
                log.warning("drift push failed: %s", exc)
    return pushes
