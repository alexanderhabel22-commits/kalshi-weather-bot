"""Earthquake NO test: 2-CONTRACT REAL-MONEY TEST from Nov 20, 2026 (Alex, Nov 20 12:23 AM ET:
"we should do the earthquake one no?", 12:23 AM "maybe 2 contracts?", 12:24 AM "bump the cap up to 4 bucks").

Kalshi's daily "biggest earthquake" market (KXBIGGESTQUAKE, "USGS max magnitude worldwide
during the UTC day >= X"). On days with no big quake yet, buyers overpay for a late big-quake
YES on the high strikes. Research: thread "Hunt for untested Kalshi edges", Nov 20, 2026,
/mnt/project-files/analysis/untested-edges-nov20/RESULTS.md (script scripts/an_eq.py).

HONEST STATUS: the pre-registered broad test LOST (-2.9c +- 6.7, 634 trades, 72 days). This
rule is a slice found AFTER looking at the data: trades placed during the UTC day on the 6.6
and 7.0 strikes, almost all NO at 86-93c: +6.6c +- 5.9 on 38 trades / 25 days
(X=7.0 +7.9c on 14 days, X=6.6 +9.2c on 6 days). Small, weak sample. ~$2-3/month at 1 contract.
One M6.6+ day costs ~$1.80 per 2-contract order.

The rule is the replay's, unchanged (an_eq.py, EDGE = 0.05):
  - market strike X in STRIKES (6.6, 7.0), during its UTC day (not before it starts);
  - USGS max magnitude so far today < X;
  - p(YES) = 1 - (1 - pday(X)) ** (hours_left / 24), pday = share of 2015-2024 UTC days whose
    max quake was >= X (USGS ComCat, 309 and 130 of 3,653 days);
  - NO price q = 1 - yes_bid in [0.03, 0.97]; buy NO at q (taker) when (1 - p) - q - fee(q) >= 0.05;
  - one entry per market, never in the last 10 minutes (the trader's own rule).
One live-only safety guard the replay could not need (it saw FINAL magnitudes): skip when a
quake within 0.1 of X has already happened today, because USGS often revises the first
magnitude by 0.1-0.2 (9 of 72 replay days settled on a revised value).

HARD LOSS CAP (Alex): $4 total on real money. Settled real P&L + the full cost of every open
real position + the new order must stay within $4, so even if every open position loses the
test cannot lose more than $4. Once settled losses leave no room for another order the
strategy stops for good and sends one push. The same rule keeps logging as the paper twin
quake_no_paper (wxbot/strats/quake_no_paper.py) so reviews at 20 and 50 fills have data.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

from .. import kalshi, notify
from ..trader import Intent, archived_realized, exposure, held_by_event, realized
from ..util import DATA_DIR, HttpError, read_json, write_json
from .mentions import _series_markets

NAME = "quake_no"
SERIES = "KXBIGGESTQUAKE"
USGS = "https://earthquake.usgs.gov/fdsnws/event/1/query"
STATE = DATA_DIR / "quake_no_state.json"
UTC = timezone.utc
log = logging.getLogger("wxbot")

# USGS ComCat, 2015-01-01 to 2024-12-31 (3,653 UTC days), days whose biggest quake was >= X.
# Same climatology as the Nov 20 replay (an_eq.py pday()).
DAYS = 3653
DAYS_AT_LEAST = {6.6: 309, 7.0: 130}
STRIKES = (6.6, 7.0)
DEFAULTS = {"contracts": 2, "edge": 0.05, "min_price": 0.03, "max_price": 0.97,
            "max_loss_usd": 4.0, "revision_margin": 0.1}


def fee1(q: float) -> float:
    """Taker fee on one contract, rounded up to the cent (as in the replay)."""
    return math.ceil(0.07 * q * (1 - q) * 100 - 1e-9) / 100


def pday(x: float) -> float:
    for k, n in DAYS_AT_LEAST.items():
        if abs(k - x) < 1e-6:
            return max(n / DAYS, 1e-4)
    return 0.0


def strike(m: dict) -> float | None:
    v = m.get("floor_strike")
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        try:
            return round(float((m.get("ticker") or "").rsplit("-", 1)[-1]), 1)
        except ValueError:
            return None


def market_day(ticker: str):
    """KXBIGGESTQUAKE-20NOV26-7.0 -> datetime(2026, 11, 20, tzinfo=UTC) (start of the UTC day)."""
    try:
        d = datetime.strptime(ticker.split("-")[1], "%d%b%y")
    except (IndexError, ValueError):
        return None
    return d.replace(tzinfo=UTC)


def p_yes(x: float, hours_left: float) -> float:
    return 1 - (1 - pday(x)) ** (max(hours_left, 0.0) / 24.0)


def candidates(markets: list[dict], now: datetime, max_so_far: float, p: dict,
               held: dict | None = None, name: str = NAME) -> list[Intent]:
    held = held or {}
    out = []
    for m in markets:
        tk = m.get("ticker") or ""
        x = strike(m)
        if not tk.startswith(SERIES + "-") or x is None or all(abs(x - s) > 1e-6 for s in STRIKES):
            continue
        start = market_day(tk)
        if start is None or not (start <= now < start + timedelta(days=1)):
            continue                                   # only during its own UTC day
        if max_so_far >= x - float(p["revision_margin"]) - 1e-9:
            continue                                   # already happened (or close enough to be revised up)
        ev = m.get("event_ticker") or tk.rsplit("-", 1)[0]
        if tk in held.get(ev, set()):
            continue
        bid = kalshi.price(m, "yes_bid")
        if bid is None or not 0 < bid < 1:
            continue
        q = round(1 - bid, 2)
        if not (float(p["min_price"]) <= q <= float(p["max_price"])):
            continue
        hours_left = (start + timedelta(days=1) - now).total_seconds() / 3600
        pr = p_yes(x, hours_left)
        edge = (1 - pr) - q - fee1(q)
        if edge < float(p["edge"]) - 1e-9:
            continue
        vol = float(m.get("volume_fp") or m.get("volume") or 0)
        out.append(Intent(name, tk, ev, "no", q, int(p["contracts"]), m.get("close_time") or "",
                          f"Biggest quake {start:%b %d} >= {x}: NO",
                          f"max so far M{max_so_far:.1f}, {hours_left:.1f} h left, model YES {pr:.3f}, "
                          f"edge {edge * 100:+.1f}c", vol, taker=True))
    return out


def max_so_far(ctx, start: datetime) -> float | None:
    """Biggest USGS magnitude since `start` (None if USGS can't be read: then no orders)."""
    try:
        js = ctx.http.get_json(USGS, params={"format": "geojson", "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
                                             "minmagnitude": 5.5, "eventtype": "earthquake",
                                             "orderby": "magnitude", "limit": 20})
    except HttpError as exc:
        log.info("quake_no: USGS %s", exc)
        return None
    mags = []
    for f in (js or {}).get("features") or []:
        try:
            mags.append(float((f.get("properties") or {}).get("mag")))
        except (TypeError, ValueError):
            continue
    return max(mags) if mags else 0.0


def budget(ctx, p: dict) -> tuple[float, float]:
    """(room, settled): dollars still allowed at risk under the hard cap, and settled real P&L."""
    settled = realized(ctx.orders, strategy=NAME, mode="live") + archived_realized("live", NAME)
    live_rows = [r for r in ctx.orders if r.get("mode") == "live"]
    open_cost = exposure(live_rows, NAME)
    return float(p["max_loss_usd"]) + settled - open_cost, settled


def fit_budget(intents: list[Intent], room: float) -> list[Intent]:
    """Shrink / drop orders so the worst case (every open position loses) stays within the cap."""
    out = []
    for i in intents:
        unit = i.price + fee1(i.price)
        n = min(i.count, int((room + 1e-9) // unit))
        if n < 1:
            continue
        i.count = n
        room -= n * unit
        out.append(i)
    return out


def stopped(ctx, p: dict, settled: float) -> bool:
    """Settled losses alone leave no room for one more contract at the top of the band: stop for good."""
    cap = float(p["max_loss_usd"])
    if cap + settled >= float(p["max_price"]) + fee1(float(p["max_price"])):
        return False
    st = read_json(STATE, {}) or {}
    if not st.get("stopped"):
        st["stopped"] = ctx.now.isoformat(timespec="seconds")
        st["pnl"] = round(settled, 2)
        write_json(STATE, st)
        try:
            notify.send(ctx.http, ctx.cfg, "Earthquake NO test: stopped",
                        f"Real-money result ${settled:+.2f} used up the ${cap:.0f} cap. No new real orders; "
                        "the paper copy keeps logging.", priority=3, tags=["warning"])
        except Exception as exc:  # alerts must never block trading
            log.warning("quake_no stop push failed: %s", exc)
    log.warning("quake_no: $%.0f cap used ($%+.2f settled); no new orders", cap, settled)
    return True


_CACHE: dict = {}      # one Kalshi + one USGS read per run, shared with the paper twin


def _read(ctx, start: datetime):
    if _CACHE.get("at") == ctx.now:
        return _CACHE["today"], _CACHE["ms"]
    try:
        markets = _series_markets(ctx, SERIES)
    except HttpError as exc:
        log.info("quake_no: %s", exc)
        markets = []
    today = [m for m in markets if market_day(m.get("ticker") or "") == start]
    ms = max_so_far(ctx, start) if today else None
    _CACHE.clear()
    _CACHE.update(at=ctx.now, today=today, ms=ms)
    return today, ms


def scan(ctx, p: dict, name: str) -> list[Intent]:
    now = ctx.now
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today, ms = _read(ctx, start)
    if not today or ms is None:
        return []
    out = candidates(today, now, ms, p, held_by_event(ctx.orders, name, ctx.mode), name=name)
    log.info("%s: %d open markets today, max so far M%.1f, %d candidates", name, len(today), ms, len(out))
    return out


def propose(ctx) -> list[Intent]:
    p = {**DEFAULTS, **(ctx.params or {})}
    if ctx.mode == "live":
        room, settled = budget(ctx, p)
        if stopped(ctx, p, settled):
            return []
        if room < float(p["min_price"]):
            return []
        return fit_budget(scan(ctx, p, NAME), room)
    return scan(ctx, p, NAME)
