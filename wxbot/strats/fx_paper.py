"""FX above/below vs a plain lognormal: PAPER ONLY test from Nov 20, 2026 (Alex's thread "Find thin
markets where we out-model casuals"; build OK'd via the project chat Nov 20 ~10:20 PM ET: "paper
only, judge after 8 settled weeks"). Never live: config keeps live = false.

Markets: Kalshi's FX "above K" series, settled on Pyth's FX rate at a fixed time:
  weekly  KX{pair}AW     ("above K at 5 PM ET on Friday")        <- the lead
  daily   KX{pair}AD     (same, daily)                            <- lost in the replay, logged anyway
  events  KX{pair}EVENT  ("above K at <time> after an economic release", new Nov 2026)
for pair in USDMXN, USDJPY, GBPUSD, EURUSD, USDCAD, AUDUSD.
Research: /mnt/project-files/analysis/thin-screen/RESULTS.md (PREREG.md, scripts/fxust_an.py).

HONEST STATUS: replay of the weekly series Aug 7 - Oct 30, 2026: +10.1c +- 5.7 per contract
(1,901 one-per-market trades, 17 settlement weeks, 14 positive; model Brier 0.123 vs market 0.133).
With spot one hour staler it drops to +4.9c +- 8.5, so part of it is speed. Daily AD: -31c on 17
trades. EVENT series: tie with the market. 4 model families were tested, so one lucky pass is
plausible. The weekly series has not listed since ~Oct 30; the radar flags its return.

The rule is the replay's (fxust_an.py, edge 0.05):
  - spot S = Yahoo's live price for the pair (must be <= 30 min old: no trading when FX is shut);
  - sigma = std of the last 528 hourly log returns (22 trading days of Yahoo hourly closes);
  - tau = hours to the market's close, counting weekdays only (at least 1);
  - p = P(rate > K) = Phi(ln(S/K) / (sigma * sqrt(tau)))  ('less' markets: 1 - p);
  - buy YES at the ask a in [0.03, 0.97] when p - a >= 0.05, else NO at 1 - bid in the band
    when (1 - p) - (1 - bid) >= 0.05 (taker; fee charged at settlement as usual);
  - one entry per market, 1 contract, held to settlement.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

from .. import kalshi
from ..trader import Intent, held_by_event
from ..util import HttpError, parse_iso
from .mentions import _series_markets

NAME = "fx_paper"
PAIRS = {"USDMXN": "MXN=X", "USDJPY": "JPY=X", "GBPUSD": "GBPUSD=X", "EURUSD": "EURUSD=X",
         "USDCAD": "CAD=X", "AUDUSD": "AUDUSD=X"}
KINDS = ("AW", "AD", "EVENT")
SERIES = [f"KX{p}{k}" for p in PAIRS for k in KINDS]
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
UA = {"User-Agent": "Mozilla/5.0 (research client)"}
DEFAULTS = {"contracts": 1, "edge": 0.05, "min_price": 0.03, "max_price": 0.97,
            "vol_hours": 528, "max_spot_age_min": 30, "active_refresh_min": 60}
log = logging.getLogger("wxbot")


def phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def pair_of(series: str) -> str | None:
    p = series[2:8]
    return p if p in PAIRS and series[8:] in KINDS else None


def kind_of(ticker: str) -> str:
    s = ticker.split("-")[0]
    return s[8:] if pair_of(s) else ""


def weekday_hours(now: datetime, close: datetime) -> float:
    """Hours from now to close, counting only Mon-Fri (UTC), like the replay; at least 1."""
    n = int((close - now).total_seconds() // 3600)
    if n <= 0:
        return 1.0
    steps = range(0, n, 3)
    wk = sum(1 for h in steps if (now + timedelta(hours=h)).weekday() < 5)
    return max(1.0, n * wk / len(steps))


def sigma(closes: list[float], n: int) -> float | None:
    c = [x for x in closes if x and x > 0]
    r = [math.log(b / a) for a, b in zip(c, c[1:])][-n:]
    if len(r) < n // 2:
        return None
    mu = sum(r) / len(r)
    var = sum((x - mu) ** 2 for x in r) / (len(r) - 1)
    return math.sqrt(var) if var > 0 else None


def p_above(spot: float, strike: float, sig: float, tau_h: float) -> float:
    return phi(math.log(spot / strike) / (sig * math.sqrt(tau_h)))


def candidates(markets: list[dict], now: datetime, fx: dict[str, tuple[float, float]], p: dict,
               held: dict | None = None, name: str = NAME) -> list[Intent]:
    """fx: pair -> (spot, hourly sigma)."""
    held = held or {}
    out = []
    lo, hi, th = float(p["min_price"]), float(p["max_price"]), float(p["edge"]) - 1e-9
    for m in markets:
        tk = m.get("ticker") or ""
        pair = pair_of(tk.split("-")[0])
        st = m.get("strike_type")
        if pair is None or pair not in fx or st not in ("greater", "less"):
            continue
        close = parse_iso(m.get("close_time"))
        if close is None or close <= now:
            continue
        try:
            k = float(m.get("floor_strike") if st == "greater" else m.get("cap_strike"))
        except (TypeError, ValueError):
            continue
        if k <= 0:
            continue
        ev = m.get("event_ticker") or tk.rsplit("-", 1)[0]
        if tk in held.get(ev, set()):
            continue
        spot, sig = fx[pair]
        tau = weekday_hours(now, close)
        pr = p_above(spot, k, sig, tau)
        if st == "less":
            pr = 1 - pr
        b, a = kalshi.price(m, "yes_bid"), kalshi.price(m, "yes_ask")
        if b is None or a is None or b <= 0:
            continue
        side = None
        if lo <= a <= hi and pr - a >= th:
            side, q, edge = "yes", round(a, 2), pr - a
        else:
            q = round(1 - b, 2)
            if lo <= q <= hi and (1 - pr) - q >= th:
                side, edge = "no", (1 - pr) - q
        if side is None:
            continue
        vol = float(m.get("volume_fp") or m.get("volume") or 0)
        word = "above" if st == "greater" else "below"
        out.append(Intent(name, tk, ev, side, q, int(p["contracts"]), m.get("close_time") or "",
                          f"{pair} {word} {k:g} at {close.astimezone(timezone.utc):%b %d %H:%M} UTC: {side.upper()}",
                          f"model YES {pr:.3f} (spot {spot:g}, hourly vol {sig * 100:.3f}%, "
                          f"{tau:.0f} weekday h), edge {edge * 100:+.1f}c [{kind_of(tk)}]", vol, taker=True))
    return out


def fx_inputs(ctx, pairs: set[str], p: dict) -> dict[str, tuple[float, float]]:
    """pair -> (live spot, hourly sigma) from Yahoo's chart API; pairs whose price is stale
    (FX closed) or unreadable are left out."""
    out = {}
    for pair in sorted(pairs):
        try:
            js = ctx.http.get_json(YAHOO.format(sym=PAIRS[pair]),
                                   params={"interval": "60m", "range": "1mo"}, headers=UA)
            res = ((js or {}).get("chart") or {}).get("result") or []
            meta = res[0].get("meta") or {}
            closes = ((res[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
            spot, t = float(meta.get("regularMarketPrice") or 0), int(meta.get("regularMarketTime") or 0)
        except (HttpError, ValueError, TypeError, IndexError, KeyError) as exc:
            log.info("fx_paper: %s spot %s", pair, exc)
            continue
        age = (ctx.now - datetime.fromtimestamp(t, timezone.utc)).total_seconds() / 60 if t else 1e9
        sig = sigma([float(c) for c in closes if c is not None], int(p["vol_hours"]))
        if spot <= 0 or sig is None or age > float(p["max_spot_age_min"]):
            log.info("fx_paper: %s skipped (spot %s, age %.0f min, sigma %s)", pair, spot, age, sig)
            continue
        out[pair] = (spot, sig)
    return out


def active(ctx, p: dict) -> list[dict]:
    """Open markets of the FX series. Which series have open markets is re-checked hourly
    (18 calls); in between only those are read."""
    st = ctx.state
    at = parse_iso(st.get("active_at"))
    refresh = at is None or ctx.now - at > timedelta(minutes=float(p["active_refresh_min"]))
    check = SERIES if refresh else list(st.get("active") or [])
    markets, act = [], []
    for s in check:
        try:
            ms = _series_markets(ctx, s)
        except HttpError as exc:
            log.info("fx_paper: %s %s", s, exc)
            if not refresh:
                act.append(s)
            continue
        if ms:
            act.append(s)
            markets += ms
    if refresh:
        st["active_at"] = ctx.now.isoformat(timespec="seconds")
    st["active"] = act
    return markets


def propose(ctx) -> list[Intent]:
    p = {**DEFAULTS, **(ctx.params or {})}
    markets = active(ctx, p)
    if not markets:
        return []
    pairs = {pair_of((m.get("ticker") or "").split("-")[0]) for m in markets} - {None}
    fx = fx_inputs(ctx, pairs, p)
    if not fx:
        return []
    out = candidates(markets, ctx.now, fx, p, held_by_event(ctx.orders, NAME, ctx.mode))
    log.info("%s: %d open markets, %d pairs priced, %d candidates", NAME, len(markets), len(fx), len(out))
    return out
