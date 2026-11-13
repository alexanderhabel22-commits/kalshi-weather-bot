"""Evening "no rain yet" fade on Kalshi's daily rain markets (KXRAIN). Started Nov 13, 2026.

Research (thread "Find new Kalshi angles to test", notes in research_new/RESULTS.md):
"Will it rain in <city> today?" (yes = more than 0 inches, Weather Company data, the
market closes at local midnight and closes early once it rains). On days the airport
has recorded no measurable rain by 8 PM local, buyers still pay 5-25c for YES. Buying
NO at the next real print at 75-95c after 8 PM local, Jul 16 - Nov 12 2026 (35 cities,
exact trades, taker fee included, settlement checked):
    8 PM local  +6.1c +- 3.9 per contract, 109 trades (Jul-Sep +6.3, Oct-Nov +5.9)
    9 PM local  +6.3c +- 4.1, 94 trades
Earlier in the day it is about 0, and a morning forecast (GFS MOS) fade loses. The
airport's hourly reports agree with the settled result on 98% of days (O'Hare for
Chicago). About one trade a day, mostly wins of 5-25c with a rare -75 to -95c loss.

Rule, every scan between `start_hour` and `end_hour` local time:
  - today's KXRAIN market for a known city is open;
  - the station's reports since local midnight show no measurable rain (no hourly
    precipitation of 0.01"+, no nonzero P group in any report) and cover the day
    (at most `max_missing_hours` hourly reports missing);
  - NO ask (1 - yes bid) is in [min_price, max_price]: buy `contracts` NO at that
    ask (taker, like the replay).
Own loss stop: once settled real-money P&L is at or below -max_loss_pct of the
bankroll, no new orders. 1 contract until its 100-contract review.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .. import kalshi
from ..trader import Intent, archived_realized, held_by_event, realized
from ..util import HttpError
from .mentions import _series_markets

NAME = "rain_evening"
SERIES = "KXRAIN"
METARS = "https://aviationweather.gov/api/data/metar"   # every METAR/SPECI with raw text
log = logging.getLogger("wxbot")
UTC = timezone.utc

_ET, _CT, _MT, _PT = "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"
# ticker suffix -> (station, time zone). Checked against settled results Jul-Nov 2026.
# Houston (Hobby 96% agreement) is left out on purpose.
CITIES = {
    "NYC": ("KNYC", _ET), "MIA": ("KMIA", _ET), "DC": ("KDCA", _ET), "BOS": ("KBOS", _ET),
    "ATL": ("KATL", _ET), "PHIL": ("KPHL", _ET), "EWR": ("KEWR", _ET), "TTN": ("KTTN", _ET),
    "LEX": ("KLEX", _ET), "CMH": ("KCMH", _ET), "PIT": ("KPIT", _ET), "PVD": ("KPVD", _ET),
    "IND": ("KIND", _ET), "TPA": ("KTPA", _ET), "DTW": ("KDTW", _ET), "WPB": ("KPBI", _ET),
    "CHI": ("KORD", _CT), "MIN": ("KMSP", _CT), "DAL": ("KDFW", _CT), "NOLA": ("KMSY", _CT),
    "AUS": ("KAUS", _CT), "SATX": ("KSAT", _CT), "OKC": ("KOKC", _CT), "MKE": ("KMKE", _CT),
    "CLL": ("KCLL", _CT), "SGF": ("KSGF", _CT), "STL": ("KSTL", _CT),
    "DEN": ("KDEN", _MT), "ABQ": ("KABQ", _MT), "PHX": ("KPHX", "America/Phoenix"),
    "LAX": ("KLAX", _PT), "SFO": ("KSFO", _PT), "SEA": ("KSEA", _PT), "LV": ("KLAS", _PT),
}
DEFAULTS = {"start_hour": 20, "end_hour": 22, "min_price": 0.75, "max_price": 0.95,
            "contracts": 1, "max_missing_hours": 2, "max_markets_per_day": 8,
            "max_loss_pct": 0.10}
_PGROUP = re.compile(r"\sP(\d{4})(?=\s|\$|$)")


def market_day(ticker: str):
    """KXRAIN-26NOV11-SFO -> date(2026, 11, 11)."""
    try:
        return datetime.strptime(ticker.split("-")[1], "%y%b%d").date()
    except (IndexError, ValueError):
        return None


def rain_so_far(metars: list, since: datetime, now: datetime,
                max_missing: int = 2) -> tuple[bool | None, str]:
    """(rained, why) from the station's METAR/SPECI reports (aviationweather.gov JSON)
    between `since` and `now`. A nonzero P group (rain since the last hourly report,
    hundredths of an inch) counts as measurable rain; P0000 is a trace and does not.
    rained is None when the hourly reports don't cover the day well enough to say."""
    hours = set()
    for ob in metars or []:
        try:
            t = datetime.fromtimestamp(float(ob.get("obsTime")), UTC)
        except (TypeError, ValueError):
            continue
        if t <= since or t > now:
            continue
        raw = ob.get("rawOb") or ""
        g = _PGROUP.search(raw)
        if g and int(g.group(1)) > 0:
            return True, f"P group {g.group(1)} at {t:%H:%M}Z"
        if (ob.get("metarType") or "METAR") == "METAR":
            hours.add(f"{t:%Y%m%d%H}")
    expected = int((now - since).total_seconds() // 3600)
    if len(hours) < expected - max_missing:
        return None, f"only {len(hours)} of {expected} hourly reports"
    return False, f"{len(hours)} reports, dry"


def candidates(ctx, markets: list[dict], p: dict, obs_fetch) -> list[Intent]:
    held = held_by_event(ctx.orders, NAME, ctx.mode)
    out = []
    for m in markets:
        tk = m.get("ticker") or ""
        city = tk.rsplit("-", 1)[-1]
        if city not in CITIES or not tk.startswith(SERIES + "-"):
            continue
        icao, tz = CITIES[city]
        local = ctx.now.astimezone(ZoneInfo(tz))
        day = market_day(tk)
        if day != local.date() or not (int(p["start_hour"]) <= local.hour < int(p["end_hour"])):
            continue
        ev = m.get("event_ticker") or tk.rsplit("-", 1)[0]
        if tk in held.get(ev, set()) or len(held.get(ev, set())) >= int(p["max_markets_per_day"]):
            continue
        bid = kalshi.price(m, "yes_bid")
        if bid is None or not 0 < bid < 1:
            continue
        no_ask = round(1 - bid, 2)
        if not (float(p["min_price"]) <= no_ask <= float(p["max_price"])):
            continue
        since = datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(tz))
        try:
            js = obs_fetch(icao, since, ctx.now)
        except HttpError as exc:
            log.info("rain_evening %s: obs %s", city, exc)
            continue
        rained, why = rain_so_far(js, since, ctx.now, int(p["max_missing_hours"]))
        if rained is not False:
            log.info("rain_evening %s: skip (%s)", city, why)
            continue
        vol = float(m.get("volume_fp") or m.get("volume") or 0)
        out.append(Intent(NAME, tk, ev, "no", no_ask, int(p["contracts"]), m.get("close_time") or "",
                          f"Rain in {city} {day:%b %d}: NO", f"no rain yet at {local:%H:%M} local ({why})",
                          vol, taker=True))
        held.setdefault(ev, set()).add(tk)
    return out


def stopped(ctx, p: dict) -> bool:
    lost = realized(ctx.orders, strategy=NAME, mode="live") + archived_realized("live", NAME)
    limit = float(p["max_loss_pct"]) * float(ctx.cfg.bankroll)
    if lost <= -limit:
        log.warning("rain_evening: loss stop hit ($%+.2f, limit -$%.2f); no new orders", lost, limit)
        return True
    return False


def propose(ctx) -> list[Intent]:
    p = {**DEFAULTS, **(ctx.params or {})}
    # cheap pre-check: some known city must be inside its evening window
    if not any(int(p["start_hour"]) <= ctx.now.astimezone(ZoneInfo(tz)).hour < int(p["end_hour"])
               for _, tz in CITIES.values()):
        return []
    if ctx.mode == "live" and stopped(ctx, p):
        return []
    try:
        markets = _series_markets(ctx, SERIES)
    except HttpError as exc:
        log.info("rain_evening: %s", exc)
        return []

    def obs_fetch(icao, since, now):
        hours = int((now - since).total_seconds() // 3600) + 2
        return ctx.http.get_json(METARS, params={"ids": icao, "hours": hours, "format": "json"}) or []

    out = candidates(ctx, markets, p, obs_fetch)
    log.info("rain_evening: %d open markets, %d candidates", len(markets), len(out))
    return out
