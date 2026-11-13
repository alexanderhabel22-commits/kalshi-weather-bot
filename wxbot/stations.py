"""Settlement stations, series catalog, and the NWS climate-day window.

Kalshi daily temperature markets settle on the NWS Daily Climate Report (CLI)
for one specific station. The CLI day runs midnight-to-midnight LOCAL STANDARD
TIME, so during daylight saving time it is 1:00 AM to 12:59 AM by the clock.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from .util import UTC, DATA_DIR, Http, HttpError, read_json, write_json

log = logging.getLogger("wxbot")


@dataclass(frozen=True)
class Station:
    icao: str
    name: str
    lat: float
    lon: float
    tz: str


# Coordinates are the ASOS sensor locations (approximate to ~100 m).
KNOWN_STATIONS: dict[str, Station] = {s.icao: s for s in [
    Station("KNYC", "Central Park", 40.7789, -73.9692, "America/New_York"),
    Station("KMDW", "Chicago Midway", 41.7860, -87.7524, "America/Chicago"),
    Station("KMIA", "Miami Intl", 25.7881, -80.3169, "America/New_York"),
    Station("KLAX", "Los Angeles Intl", 33.9382, -118.3866, "America/Los_Angeles"),
    Station("KDEN", "Denver Intl", 39.8466, -104.6562, "America/Denver"),
    Station("KPHL", "Philadelphia Intl", 39.8733, -75.2268, "America/New_York"),
    Station("KPHX", "Phoenix Sky Harbor", 33.4277, -112.0038, "America/Phoenix"),
    Station("KMSP", "Minneapolis-St Paul", 44.8831, -93.2289, "America/Chicago"),
    Station("KDFW", "Dallas/Fort Worth", 32.8998, -97.0403, "America/Chicago"),
    Station("KMSY", "New Orleans Intl", 29.9934, -90.2580, "America/Chicago"),
    Station("KAUS", "Austin-Bergstrom", 30.1831, -97.6799, "America/Chicago"),
    Station("KSFO", "San Francisco Intl", 37.6197, -122.3656, "America/Los_Angeles"),
    Station("KHOU", "Houston Hobby", 29.6375, -95.2825, "America/Chicago"),
    Station("KSAT", "San Antonio Intl", 29.5443, -98.4839, "America/Chicago"),
    Station("KLAS", "Las Vegas Harry Reid", 36.0719, -115.1634, "America/Los_Angeles"),
    Station("KSEA", "Seattle-Tacoma", 47.4447, -122.3136, "America/Los_Angeles"),
    Station("KOKC", "Oklahoma City", 35.3889, -97.6006, "America/Chicago"),
    Station("KBOS", "Boston Logan", 42.3606, -71.0097, "America/New_York"),
    Station("KDCA", "Washington National", 38.8483, -77.0342, "America/New_York"),
    Station("KATL", "Atlanta Hartsfield", 33.6301, -84.4418, "America/New_York"),
    Station("KEWR", "Newark Liberty", 40.6827, -74.1693, "America/New_York"),
    Station("KTTN", "Trenton Mercer", 40.2767, -74.8156, "America/New_York"),
    Station("KSAN", "San Diego Lindbergh", 32.7336, -117.1831, "America/Los_Angeles"),
]}

# Fallback catalog: series ticker -> (metric, station, city label).
# Auto-discovery (below) refreshes this from Kalshi once a day when it can.
DEFAULT_SERIES: dict[str, tuple[str, str, str]] = {
    "KXHIGHNY": ("high", "KNYC", "NYC"),
    "KXHIGHCHI": ("high", "KMDW", "Chicago"),
    "KXHIGHMIA": ("high", "KMIA", "Miami"),
    "KXHIGHLAX": ("high", "KLAX", "LA"),
    "KXHIGHDEN": ("high", "KDEN", "Denver"),
    "KXHIGHPHIL": ("high", "KPHL", "Philadelphia"),
    "KXHIGHTPHX": ("high", "KPHX", "Phoenix"),
    "KXHIGHTMIN": ("high", "KMSP", "Minneapolis"),
    "KXHIGHTDAL": ("high", "KDFW", "Dallas"),
    "KXHIGHTNOLA": ("high", "KMSY", "New Orleans"),
    "KXHIGHAUS": ("high", "KAUS", "Austin"),
    "KXHIGHTSFO": ("high", "KSFO", "San Francisco"),
    "KXHIGHTHOU": ("high", "KHOU", "Houston"),
    "KXHIGHTSATX": ("high", "KSAT", "San Antonio"),
    "KXHIGHTLV": ("high", "KLAS", "Las Vegas"),
    "KXHIGHTSEA": ("high", "KSEA", "Seattle"),
    "KXHIGHTOKC": ("high", "KOKC", "Oklahoma City"),
    "KXHIGHTBOS": ("high", "KBOS", "Boston"),
    "KXHIGHTDC": ("high", "KDCA", "Washington DC"),
    "KXHIGHTATL": ("high", "KATL", "Atlanta"),
    "KXLOWTNYC": ("low", "KNYC", "NYC"),
    "KXLOWTCHI": ("low", "KMDW", "Chicago"),
    "KXLOWTMIA": ("low", "KMIA", "Miami"),
    "KXLOWTLAX": ("low", "KLAX", "LA"),
    "KXLOWTDEN": ("low", "KDEN", "Denver"),
    "KXLOWTPHIL": ("low", "KPHL", "Philadelphia"),
    "KXLOWTAUS": ("low", "KAUS", "Austin"),
    # Added Nov 13, 2026: Kalshi now runs these too. Every weather series lists
    # weather.com as its settlement source now, so discovery can no longer read the
    # station from the URL; the station comes from the "(CLIxxx)" code in the rules.
    "KXHIGHTEWR": ("high", "KEWR", "Newark"),
    "KXHIGHTTTN": ("high", "KTTN", "Trenton"),
    "KXHIGHTSAN": ("high", "KSAN", "San Diego"),
    "KXLOWTATL": ("low", "KATL", "Atlanta"),
    "KXLOWTBOS": ("low", "KBOS", "Boston"),
    "KXLOWTDAL": ("low", "KDFW", "Dallas"),
    "KXLOWTDC": ("low", "KDCA", "Washington DC"),
    "KXLOWTHOU": ("low", "KHOU", "Houston"),
    "KXLOWTLV": ("low", "KLAS", "Las Vegas"),
    "KXLOWTMIN": ("low", "KMSP", "Minneapolis"),
    "KXLOWTNOLA": ("low", "KMSY", "New Orleans"),
    "KXLOWTOKC": ("low", "KOKC", "Oklahoma City"),
    "KXLOWTPHX": ("low", "KPHX", "Phoenix"),
    "KXLOWTSAN": ("low", "KSAN", "San Diego"),
    "KXLOWTSATX": ("low", "KSAT", "San Antonio"),
    "KXLOWTSEA": ("low", "KSEA", "Seattle"),
    "KXLOWTSFO": ("low", "KSFO", "San Francisco"),
    "KXLOWTEWR": ("low", "KEWR", "Newark"),
    "KXLOWTTTN": ("low", "KTTN", "Trenton"),
}


@dataclass(frozen=True)
class SeriesInfo:
    ticker: str
    metric: str        # "high" or "low"
    station: Station
    city: str


# ---------------------------------------------------------------- CLI window

def std_offset(tz: str, year: int) -> timedelta:
    """UTC offset of local STANDARD time (January offset, northern hemisphere)."""
    return datetime(year, 1, 15, 12, tzinfo=ZoneInfo(tz)).utcoffset()


def cli_window(day: date, tz: str) -> tuple[datetime, datetime]:
    """UTC start/end of the NWS climate day for `day` at a station in `tz`."""
    off = std_offset(tz, day.year)
    start = datetime(day.year, day.month, day.day, tzinfo=UTC) - off
    return start, start + timedelta(days=1)


def local_today(tz: str, now: datetime) -> date:
    return now.astimezone(ZoneInfo(tz)).date()


def climate_day_of(now: datetime, tz: str) -> date:
    """Which CLI day `now` falls in (standard-time calendar day)."""
    return (now + std_offset(tz, now.year)).date()


# ---------------------------------------------------------------- resolution

def station_from_settlement_url(url: str) -> str | None:
    """forecast.weather.gov/product.php?...&issuedby=NYC -> KNYC."""
    try:
        q = parse_qs(urlparse(url).query)
    except ValueError:
        return None
    for key in ("issuedby", "sid", "station"):
        vals = q.get(key) or q.get(key.upper())
        if vals:
            code = vals[0].strip().upper()
            if re.fullmatch(r"[A-Z]{3}", code):
                return "K" + code
            if re.fullmatch(r"K[A-Z]{3}", code):
                return code
    return None


def fetch_station(http: Http, icao: str) -> Station | None:
    """Look up an unknown station's coordinates/timezone from the NWS API."""
    cache_path = DATA_DIR / "stations.json"
    cache = read_json(cache_path, {}) or {}
    if icao in cache:
        c = cache[icao]
        return Station(icao, c["name"], c["lat"], c["lon"], c["tz"])
    try:
        js = http.get_json(f"https://api.weather.gov/stations/{icao}",
                           headers={"Accept": "application/geo+json"})
        lon, lat = js["geometry"]["coordinates"][:2]
        props = js.get("properties", {})
        st = Station(icao, props.get("name", icao), float(lat), float(lon),
                     props.get("timeZone") or "America/New_York")
    except (HttpError, KeyError, TypeError, ValueError) as exc:
        log.warning("could not resolve station %s: %s", icao, exc)
        return None
    cache[icao] = {"name": st.name, "lat": st.lat, "lon": st.lon, "tz": st.tz}
    write_json(cache_path, cache)
    return st


_CITY_RE = re.compile(r"(?:highest|lowest|high|low)\s+temp(?:erature)?\s+in\s+(.+?)(?:\s+today|\s+on\b|\?|$)", re.I)


def series_from_kalshi(raw: dict, http: Http | None) -> SeriesInfo | None:
    """Build SeriesInfo from a Kalshi /series object (used by auto-discovery)."""
    ticker = raw.get("ticker", "")
    m = re.fullmatch(r"KX(HIGH|LOWT)[A-Z]+", ticker)
    if not m:
        return None
    if (raw.get("frequency") or "daily").lower() != "daily":
        return None
    metric = "high" if m.group(1) == "HIGH" else "low"
    icao = None
    for src in raw.get("settlement_sources") or []:
        icao = station_from_settlement_url(src.get("url", ""))
        if icao:
            break
    if not icao and ticker in DEFAULT_SERIES:
        icao = DEFAULT_SERIES[ticker][1]
    if not icao:
        return None
    st = KNOWN_STATIONS.get(icao) or (fetch_station(http, icao) if http else None)
    if not st:
        return None
    if ticker in DEFAULT_SERIES:
        city = DEFAULT_SERIES[ticker][2]
    else:
        cm = _CITY_RE.search(raw.get("title", ""))
        city = cm.group(1).strip() if cm else ticker
    return SeriesInfo(ticker, metric, st, city)


def default_catalog() -> list[SeriesInfo]:
    out = []
    for t, (metric, icao, city) in DEFAULT_SERIES.items():
        out.append(SeriesInfo(t, metric, KNOWN_STATIONS[icao], city))
    return out
