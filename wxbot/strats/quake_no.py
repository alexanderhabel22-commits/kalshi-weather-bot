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
            "max_loss_usd": 4.0, "revision_margin": 0.1,
            "guard": False, "feed_margin": 0.3, "alarm_gap": 0.3, "alarm_floor": 0.5,
            "alarm_margin": 0.3, "alarm_strikes": 2}

# --- Fast-quake guard (Nov 21, 2026; thread "Get faster quake data for quake bet") -------------
# The first real fill lost on a M6.7 Peru quake: the bot bought NO 3.5 min after it struck while
# USGS still showed nothing (USGS first listed it at ~7.4 min; over 123 M6.3+ quakes since Jan
# 2025 USGS's median is ~12 min). GFZ GEOFON had it at 3.5 min (median 4.8 min, first in 95% of
# cases); EMSC is similar. Kalshi traders move the price 1-4 min after a big quake. Worse, that
# move is what makes NO look cheap to this rule, so the rule buys INTO fresh quakes.
# Guard (only when params guard = true; the paper twin quake_no_guard_paper runs it first):
#   1. skip a strike X when GFZ or EMSC already shows M >= X - feed_margin today;
#   2. skip the whole day's markets when 2+ strikes 6.0-7.0 that USGS is 0.3+ short of have a YES
#      bid of 50c+ AND 30c+ above their own climatology: the market has seen a quake USGS hasn't
#      posted. Tuned on Kalshi YES trades Sep 15-Nov 21: fires on 16 of 16 big-quake days, false
#      alarms on 2 of 67 days (single strikes alone: 26 false days, from stray sweeps).
# It can only skip orders, never add them. Feed failures never block (USGS still decides).
GFZ = "https://geofon.gfz.de/fdsnws/event/1/query"
EMSC = "https://www.seismicportal.eu/fdsnws/event/1/query"
FEED_LOG = DATA_DIR / "quake_feed_log.csv"
# Same USGS ComCat 2015-2024 climatology for every strike the guard checks (recomputed Nov 21).
GUARD_DAYS = {6.0: 1041, 6.2: 682, 6.4: 460, 6.6: 309, 6.8: 213, 7.0: 130}


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


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_gfz(text: str) -> list[tuple[str, float]]:
    """GEOFON fdsnws text format -> [(origin time, magnitude)]."""
    out = []
    for line in (text or "").splitlines():
        if line.startswith("#") or "|" not in line:
            continue
        c = line.split("|")
        if len(c) > 10 and _f(c[10]) is not None:
            out.append((c[1], _f(c[10])))
    return out


def parse_emsc(js) -> list[tuple[str, float]]:
    out = []
    for f in (js or {}).get("features") or []:
        pr = f.get("properties") or {}
        if _f(pr.get("mag")) is not None:
            out.append((str(pr.get("time") or ""), _f(pr.get("mag"))))
    return out


def feeds(ctx, start: datetime) -> dict[str, list[tuple[str, float]]]:
    """Quakes M5.5+ since `start` from the two fast free catalogs (GFZ, EMSC). A feed that fails is empty."""
    t = start.strftime("%Y-%m-%dT%H:%M:%S")
    out: dict[str, list] = {}
    try:
        out["gfz"] = parse_gfz(ctx.http.get_text(GFZ, params={"format": "text", "starttime": t, "minmag": 5.5}))
    except Exception as exc:  # a feed problem must never stop the run
        log.info("quake guard: GFZ %s", exc)
        out["gfz"] = []
    try:
        out["emsc"] = parse_emsc(ctx.http.get_json(EMSC, params={"format": "json", "starttime": t,
                                                                 "minmag": 5.5, "limit": 50}))
    except Exception as exc:
        log.info("quake guard: EMSC %s", exc)
        out["emsc"] = []
    return out


def market_alarm(markets: list[dict], now: datetime, usgs_max: float, p: dict) -> str | None:
    """2+ strikes USGS hasn't reached, priced far above climatology = the market knows of a quake."""
    hits = []
    for m in markets:
        x = strike(m)
        n = GUARD_DAYS.get(x) if x is not None else None
        start = market_day(m.get("ticker") or "")
        if not n or start is None or x < usgs_max + float(p["alarm_margin"]) - 1e-9:
            continue
        bid = kalshi.price(m, "yes_bid")
        if bid is None:
            continue
        h = max((start + timedelta(days=1) - now).total_seconds() / 3600, 0.0)
        pr = 1 - (1 - n / DAYS) ** (h / 24)
        if bid >= max(float(p["alarm_floor"]), pr + float(p["alarm_gap"])) - 1e-9:
            hits.append(f"M{x} YES {bid:.2f} vs model {pr:.2f}")
    return "; ".join(hits) if len(hits) >= int(p["alarm_strikes"]) else None


def guard(intents: list[Intent], markets: list[dict], now: datetime, usgs_max: float,
          fd: dict, p: dict) -> list[Intent]:
    if not intents:
        return intents
    alarm = market_alarm(markets, now, usgs_max, p)
    if alarm:
        log.info("quake guard: market alarm (%s), skipping %d", alarm, len(intents))
        return []
    fmax = max([m for rows in fd.values() for _, m in rows] + [0.0])
    keep = []
    for i in intents:
        x = round(float(i.ticker.rsplit("-", 1)[-1]), 1)
        if fmax >= x - float(p["feed_margin"]) - 1e-9:
            log.info("quake guard: GFZ/EMSC M%.1f near %s, skipping", fmax, i.ticker)
            continue
        keep.append(i)
    return keep


def log_feeds(now: datetime, fd: dict, usgs_max: float) -> None:
    """Data: first time each feed showed each M6+ quake (5-min resolution, for the latency review)."""
    import csv
    rows = [(src, t, m) for src, v in fd.items() for t, m in v if m >= 6.0]
    if usgs_max >= 6.0:
        rows.append(("usgs_max", now.strftime("%Y-%m-%d"), usgs_max))
    if not rows:
        return
    seen = set()
    if FEED_LOG.exists():
        with FEED_LOG.open() as fh:
            seen = {(r.get("source"), r.get("origin"), r.get("mag")) for r in csv.DictReader(fh)}
    new = [r for r in rows if (r[0], r[1], f"{r[2]:.2f}") not in seen]
    if not new:
        return
    head = not FEED_LOG.exists()
    with FEED_LOG.open("a", newline="") as fh:
        w = csv.writer(fh)
        if head:
            w.writerow(["seen_at", "source", "origin", "mag"])
        for src, t, m in new:
            w.writerow([now.isoformat(timespec="seconds"), src, t, f"{m:.2f}"])


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


def _feeds(ctx, start: datetime, ms: float) -> dict:
    if _CACHE.get("at") == ctx.now and "fd" in _CACHE:
        return _CACHE["fd"]
    fd = feeds(ctx, start)
    try:
        log_feeds(ctx.now, fd, ms)
    except OSError as exc:
        log.info("quake guard: feed log %s", exc)
    _CACHE["fd"] = fd
    return fd


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
    if p.get("guard"):                                 # read (and log) the fast feeds every run
        fd = _feeds(ctx, start, ms)
        out = guard(out, today, now, ms, fd, p)
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
