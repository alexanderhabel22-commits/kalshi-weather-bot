"""Shipping chokepoint counts: PAPER ONLY test from Nov 20, 2026 (Alex, Nov 20 11:41 AM ET:
"Whatever you think" to running the shipping lead on paper now instead of waiting for the
mid-January re-run). Never live: config keeps live = false.

Kalshi's weekly transit-count markets (KXHORMUZWEEKLY, KXSUEZWEEKLY, KXPANAMAWEEKLY,
KXBABELMANDEBWEEKLY): "ships through X during the Mon-Sun week > K", settled on IMF PortWatch
daily counts. Research: thread "Test next batch of untested edges", Nov 20, 2026,
/mnt/project-files/analysis/untested-edges-nov20/RESULTS-BATCH2.md (script scripts2/an_ship.py).

HONEST STATUS: replay Sep-Nov 2026 +4.7c +- 8.3 on 216 trades over 39 series-weeks: NOT
significant (Suez +17.5, Bab el-Mandeb +9.3, Hormuz +1.0, Panama -9.2). This paper test exists
to collect more weeks faster than waiting for the January re-run.

The rule is the replay's, unchanged (an_ship.py, TH = 0.05):
  - 'greater' markets of the four series, only until the week's Sunday is over (ET);
  - forecast for the week = 7 x the mean of the last 14 PortWatch days published (days of the
    week already published count as known; PortWatch posts through Sunday each Tuesday);
  - negative binomial with the replay's per-series dispersion r (fit on Feb 2024-Aug 2026);
  - p = P(week total > K); buy YES at the ask a in [0.03, 0.97] when p - a - fee(a) >= 0.05,
    else NO at 1 - bid in the band when (1 - p) - (1 - bid) - fee >= 0.05 (taker, 1-ct fee);
  - one entry per market, held to settlement.
Live-only safety guard: no orders when PortWatch can't be read or its newest day is over 16
days old (the replay always had the data).
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .. import kalshi
from ..trader import Intent, held_by_event
from ..util import HttpError, parse_iso
from .mentions import _series_markets

NAME = "ship_paper"
PORTWATCH = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
             "Daily_Chokepoints_Data/FeatureServer/0/query")
SERIES = {"KXHORMUZWEEKLY": "Strait of Hormuz", "KXSUEZWEEKLY": "Suez Canal",
          "KXPANAMAWEEKLY": "Panama Canal", "KXBABELMANDEBWEEKLY": "Bab el-Mandeb Strait"}
SHORT = {"KXHORMUZWEEKLY": "Hormuz", "KXSUEZWEEKLY": "Suez", "KXPANAMAWEEKLY": "Panama",
         "KXBABELMANDEBWEEKLY": "Bab el-Mandeb"}
# Negative-binomial dispersion per series, frozen from the replay's fit (weeks Feb 5 2024 - Aug
# 2026, method of moments on 7 x 14-day-mean forecasts). Panama hit the fit's floor (~Poisson).
R = {"KXHORMUZWEEKLY": 38.913, "KXSUEZWEEKLY": 1849.688, "KXPANAMAWEEKLY": 10000.0,
     "KXBABELMANDEBWEEKLY": 266.556}
DEFAULTS = {"contracts": 1, "edge": 0.05, "min_price": 0.03, "max_price": 0.97,
            "refresh_hours": 3, "max_data_age_days": 16}
ET = ZoneInfo("America/New_York")
log = logging.getLogger("wxbot")


def fee1(q: float) -> float:
    """Taker fee on one contract, rounded up to the cent (as in the replay)."""
    return math.ceil(0.07 * q * (1 - q) * 100 - 1e-9) / 100


def week_end(ticker: str) -> date | None:
    """KXSUEZWEEKLY-26NOV22-T320 -> date(2026, 11, 22), the Sunday that ends the Mon-Sun week."""
    try:
        return datetime.strptime(ticker.split("-")[1], "%y%b%d").date()
    except (IndexError, ValueError):
        return None


def nb_sf(need: int, r: float, mu: float) -> float:
    """P(X > need) for a negative binomial with mean mu and size r (scipy's nbinom(r, r/(r+mu)))."""
    if need < 0:
        return 1.0
    if mu <= 0:
        return 0.0
    pp = r / (r + mu)
    lp, lq = math.log(pp), math.log1p(-pp)
    base = r * lp - math.lgamma(r)
    cdf = 0.0
    for k in range(need + 1):
        cdf += math.exp(base + math.lgamma(k + r) - math.lgamma(k + 1) + k * lq)
    return min(max(1.0 - cdf, 0.0), 1.0)


def p_over(series: str, strike: float, daily: dict[date, int], end: date) -> float | None:
    """Model chance the Mon-Sun week ending `end` totals more than `strike` transits."""
    if not daily:
        return None
    last = max(daily)
    mon = end - timedelta(days=6)
    known = [mon + timedelta(days=i) for i in range(7) if mon + timedelta(days=i) <= last]
    kn = sum(daily.get(d, 0) for d in known)
    rem = 7 - len(known)
    pred = 7 * sum(daily.get(last - timedelta(days=i), 0) for i in range(14)) / 14
    if rem == 0:
        return 1.0 if kn > strike else 0.0
    mu = pred * rem / 7
    return nb_sf(math.floor(strike) - kn, R[series], mu)


def candidates(markets: list[dict], now: datetime, counts: dict[str, dict[date, int]], p: dict,
               held: dict | None = None, name: str = NAME) -> list[Intent]:
    held = held or {}
    today = now.astimezone(ET).date()
    out = []
    for m in markets:
        tk = m.get("ticker") or ""
        series = tk.split("-")[0]
        if series not in SERIES or m.get("strike_type") != "greater":
            continue
        end = week_end(tk)
        if end is None or today > end:
            continue                                   # only before the week is over (as the replay)
        try:
            k = float(m.get("floor_strike"))
        except (TypeError, ValueError):
            continue
        ev = m.get("event_ticker") or tk.rsplit("-", 1)[0]
        if tk in held.get(ev, set()):
            continue                                   # one entry per market
        daily = counts.get(series) or {}
        pr = p_over(series, k, daily, end)
        if pr is None:
            continue
        b, a = kalshi.price(m, "yes_bid"), kalshi.price(m, "yes_ask")
        if b is None or a is None:
            continue
        lo, hi, th = float(p["min_price"]), float(p["max_price"]), float(p["edge"]) - 1e-9
        side = None
        if lo <= a <= hi and pr - a - fee1(a) >= th:
            side, q, edge = "yes", round(a, 2), pr - a - fee1(a)
        else:
            q = round(1 - b, 2)
            if lo <= q <= hi and (1 - pr) - q - fee1(q) >= th:
                side, edge = "no", (1 - pr) - q - fee1(q)
        if side is None:
            continue
        last = max(daily)
        vol = float(m.get("volume_fp") or m.get("volume") or 0)
        out.append(Intent(name, tk, ev, side, q, int(p["contracts"]), m.get("close_time") or "",
                          f"{SHORT[series]} transits week to {end:%b %d} > {k:g}: {side.upper()}",
                          f"model YES {pr:.3f} (PortWatch to {last:%b %d}, 14-day avg "
                          f"{sum(daily.get(last - timedelta(days=i), 0) for i in range(14)) / 14:.1f}/day), "
                          f"edge {edge * 100:+.1f}c", vol, taker=True))
    return out


def counts(ctx, p: dict) -> dict[str, dict[date, int]] | None:
    """Last ~30 PortWatch days per chokepoint, cached in the strategy state (refresh every few
    hours: PortWatch only posts on Tuesdays). None if it can't be read and the cache is stale."""
    st = ctx.state
    at = parse_iso(st.get("pw_at"))
    if at is None or ctx.now - at > timedelta(hours=float(p["refresh_hours"])) or not st.get("pw"):
        since = (ctx.now.astimezone(ET).date() - timedelta(days=30)).isoformat()
        fresh = {}
        try:
            for series, port in SERIES.items():
                js = ctx.http.get_json(PORTWATCH, params={
                    "where": f"portname='{port}' AND date>=DATE '{since}'", "outFields": "date,n_total",
                    "orderByFields": "date", "resultRecordCount": 100, "f": "json"})
                rows = {}
                for f in (js or {}).get("features") or []:
                    a = f.get("attributes") or {}
                    if a.get("date") and a.get("n_total") is not None:
                        rows[str(a["date"])[:10]] = int(a["n_total"])
                if not rows:
                    raise HttpError(f"PortWatch: no rows for {port}")
                fresh[series] = rows
            st["pw"], st["pw_at"] = fresh, ctx.now.isoformat(timespec="seconds")
        except (HttpError, ValueError, TypeError) as exc:
            log.info("ship_paper: PortWatch %s", exc)
            if not st.get("pw"):
                return None
    out = {}
    for series, rows in (st.get("pw") or {}).items():
        out[series] = {date.fromisoformat(d): int(n) for d, n in rows.items()}
    newest = min((max(d) for d in out.values() if d), default=None)
    if newest is None or (ctx.now.astimezone(ET).date() - newest).days > int(p["max_data_age_days"]):
        log.info("ship_paper: PortWatch data too old (%s); no orders", newest)
        return None
    return out


def propose(ctx) -> list[Intent]:
    p = {**DEFAULTS, **(ctx.params or {})}
    markets = []
    for s in SERIES:
        try:
            markets += _series_markets(ctx, s)
        except HttpError as exc:
            log.info("ship_paper: %s %s", s, exc)
    if not markets:
        return []
    c = counts(ctx, p)
    if c is None:
        return []
    out = candidates(markets, ctx.now, c, p, held_by_event(ctx.orders, NAME, ctx.mode))
    log.info("%s: %d open markets, %d candidates", NAME, len(markets), len(out))
    return out
