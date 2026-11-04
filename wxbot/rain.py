"""Silent rain census (test started Nov 4, 2026).

Monthly rain markets ("Rain in NYC in Nov 2026? Above 3 inches") are a lock
once the month's total passes the threshold, like a decided temperature
bracket. This module never alerts. It only records, for every threshold the
month's rain has already passed, the ask when we first saw it and the
cheapest ask afterward, so we can tell whether rain locks stay cheap long
enough to buy by hand or are priced in like temperature locks.

Two levels of "passed":
  provisional  latest NWS climate report total + hourly station rain since
               that report (fast, unofficial)
  official     the NWS climate report (CLI) month-to-date total itself

Data is kept in data/rain_census.json and shown in the report.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from . import kalshi
from .stations import KNOWN_STATIONS, cli_window, fetch_station
from .util import DATA_DIR, Http, HttpError, parse_iso, read_json, write_json

log = logging.getLogger("wxbot")

RAIN_CENSUS = DATA_DIR / "rain_census.json"
NWS = "https://api.weather.gov"
NWS_HEADERS = {"Accept": "application/geo+json"}

# Kalshi monthly rain series that settle on an NWS climate site.
# Ticker code -> NWS CLI code (also the station: K + code).
DEFAULT_SERIES = {
    "KXRAINNYCM": "NYC", "KXRAINEWRM": "EWR", "KXRAINTTNM": "TTN", "KXRAININDM": "IND",
    "KXRAINSGFM": "SGF", "KXRAINWPBM": "PBI", "KXRAINNOLAM": "MSY", "KXRAINLEXM": "LEX",
    "KXRAINPVDM": "PVD", "KXRAINCLLM": "CLL", "KXRAINAUSM": "AUS", "KXRAINLAXM": "LAX",
}
_CODE_FIX = {"WPB": "PBI", "NOLA": "MSY"}
_MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST",
           "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]
_MON3 = {m[:3]: i for i, m in enumerate(_MONTHS, 1)}


# ---------------------------------------------------------------- catalog

def cli_code_for(ticker: str) -> str | None:
    if ticker in DEFAULT_SERIES:
        return DEFAULT_SERIES[ticker]
    m = re.fullmatch(r"KXRAIN([A-Z]{3,4})M", ticker or "")
    if not m:
        return None
    code = _CODE_FIX.get(m.group(1), m.group(1))
    return code if len(code) == 3 else None


def rain_catalog(http: Http, state: dict, now: datetime) -> dict[str, str]:
    """{series ticker: CLI code}, rediscovered once a day."""
    today = now.date().isoformat()
    cat = state.get("rain_catalog") or {}
    if cat.get("date") == today and cat.get("series"):
        return dict(cat["series"])
    found = dict(DEFAULT_SERIES)
    try:
        for r in kalshi.list_weather_series(http):
            t = r.get("ticker", "")
            code = cli_code_for(t)
            if code:
                found[t] = code
    except Exception as exc:  # discovery is best-effort
        log.info("rain series discovery failed: %s", exc)
    state["rain_catalog"] = {"date": today, "series": found}
    return found


# ---------------------------------------------------------------- NWS data

def _num(tok: str) -> float | None:
    tok = tok.strip().upper()
    if tok == "T":
        return 0.0          # trace counts as zero in totals
    try:
        return float(tok)
    except ValueError:
        return None


def parse_cli(text: str) -> dict | None:
    """Pull the month-to-date rain total out of an NWS CLI product.

    Returns {"year", "month", "day", "mtd", "as_of": (hour, minute) or None}.
    as_of is set for a same-day preliminary report ("VALID TODAY AS OF 0500 PM");
    None means the report covers the whole day.
    """
    if not text:
        return None
    up = text.upper()
    m = re.search(r"CLIMATE SUMMARY FOR\s+([A-Z]+)\s+(\d{1,2})\s+(\d{4})", up)
    if not m or m.group(1) not in _MONTHS:
        return None
    month, day, year = _MONTHS.index(m.group(1)) + 1, int(m.group(2)), int(m.group(3))
    i = up.find("PRECIPITATION")
    if i < 0:
        return None
    pm = re.search(r"MONTH TO DATE\s+(\S+)", up[i:])
    mtd = _num(pm.group(1)) if pm else None
    if mtd is None:
        return None
    as_of = None
    if vm := re.search(r"VALID TODAY AS OF\s+(\d{3,4})\s*(AM|PM)", up):
        hhmm = vm.group(1).zfill(4)
        hh, mm = int(hhmm[:2]) % 12, int(hhmm[2:])
        if vm.group(2) == "PM":
            hh += 12
        as_of = (hh, mm)
    return {"year": year, "month": month, "day": day, "mtd": mtd, "as_of": as_of}


def cli_covers_until(cli: dict, tz: str) -> datetime:
    """UTC time up to which the CLI total counts rain (climate days run on standard time)."""
    d = date(cli["year"], cli["month"], cli["day"])
    start, end = cli_window(d, tz)
    if cli.get("as_of"):
        hh, mm = cli["as_of"]
        return min(end, start + timedelta(hours=hh, minutes=mm))
    return end


def fetch_cli(http: Http, code: str) -> dict | None:
    js = http.get_json(f"{NWS}/products/types/CLI/locations/{code}", headers=NWS_HEADERS)
    items = js.get("@graph") or js.get("products") or []
    if not items:
        return None
    url = items[0].get("@id") or f"{NWS}/products/{items[0].get('id')}"
    prod = http.get_json(url, headers=NWS_HEADERS)
    return parse_cli(prod.get("productText", ""))


def _inches(q: dict | None) -> float | None:
    if not isinstance(q, dict) or q.get("value") is None:
        return None
    v = float(q["value"])
    unit = (q.get("unitCode") or "").lower()
    if unit.endswith(":mm"):
        return v / 25.4
    if unit.endswith(":m"):
        return v * 39.3701
    return v / 25.4


def hourly_rain_since(js: dict, since: datetime) -> float:
    """Sum of hourly rain (routine reports at :45-:59) after `since`, in inches."""
    by_hour: dict[str, float] = {}
    for feat in js.get("features") or []:
        p = feat.get("properties") or {}
        t = parse_iso(p.get("timestamp"))
        if t is None or t <= since or not 45 <= t.minute <= 59:
            continue
        v = _inches(p.get("precipitationLastHour"))
        if v is None:
            continue
        key = f"{t:%Y%m%d%H}"
        by_hour[key] = max(by_hour.get(key, 0.0), v)
    return round(sum(by_hour.values()), 2)


# ---------------------------------------------------------------- census

def load() -> dict:
    return read_json(RAIN_CENSUS, {}) or {}


def save(census: dict, keep_months: int = 4) -> None:
    keep = sorted({v.get("month", "") for v in census.values()})[-keep_months:]
    write_json(RAIN_CENSUS, {k: v for k, v in census.items() if v.get("month", "") in keep})


def _event_month(event_ticker: str) -> tuple[int, int] | None:
    m = re.search(r"-(\d{2})([A-Z]{3})$", event_ticker or "")
    if not m or m.group(2) not in _MON3:
        return None
    return 2000 + int(m.group(1)), _MON3[m.group(2)]


def record(census: dict, m: dict, *, city: str, level: str, total: float,
           now: datetime, depth: float | None = None) -> bool:
    """Note a passed threshold. Returns True if this is the first sighting at this level."""
    ask = kalshi.price(m, "yes_ask")
    ask = ask if (ask is not None and 0 < ask < 0.995) else 1.0
    key = m.get("ticker", "")
    stamp = now.isoformat(timespec="seconds")
    cur = census.get(key)
    first = False
    if cur is None:
        ym = _event_month(m.get("event_ticker", "")) or (now.year, now.month)
        cur = census[key] = {"city": city, "event": m.get("event_ticker", ""),
                             "month": f"{ym[0]}-{ym[1]:02d}",
                             "threshold": float(m.get("floor_strike")),
                             "min_ask": round(ask, 4)}
    if f"{level}_at" not in cur:
        cur[f"{level}_at"] = stamp
        cur[f"{level}_ask"] = round(ask, 4)
        cur[f"{level}_total"] = round(total, 2)
        if depth is not None:
            cur[f"{level}_depth"] = round(depth, 2)
        first = True
    if ask < cur["min_ask"]:
        cur["min_ask"] = round(ask, 4)
    return first


def census_scan(http: Http, state: dict, census: dict, now: datetime) -> int:
    """One pass over every monthly rain market. Returns thresholds newly seen as passed."""
    new = 0
    for series, code in sorted(rain_catalog(http, state, now).items()):
        try:
            markets = kalshi.list_open_markets(http, series)
        except HttpError as exc:
            log.info("rain markets %s: %s", series, exc)
            continue
        live = []
        for m in markets:
            close = parse_iso(m.get("close_time"))
            ym = _event_month(m.get("event_ticker", ""))
            if (close and close <= now) or ym is None or m.get("floor_strike") is None:
                continue
            if (m.get("strike_type") or "greater") != "greater":
                continue
            live.append((ym, m))
        if not live:
            continue
        icao = "K" + code
        st = KNOWN_STATIONS.get(icao) or fetch_station(http, icao)
        if st is None:
            continue
        try:
            cli = fetch_cli(http, code)
        except HttpError as exc:
            log.info("rain CLI %s: %s", code, exc)
            continue
        if cli is None:
            continue
        since = cli_covers_until(cli, st.tz)
        try:
            obs = http.get_json(f"{NWS}/stations/{icao}/observations",
                                params={"start": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                        "end": now.strftime("%Y-%m-%dT%H:%M:%SZ")},
                                headers=NWS_HEADERS)
            extra = hourly_rain_since(obs, since)
        except HttpError as exc:
            log.info("rain observations %s: %s", icao, exc)
            extra = 0.0
        for (y, mo), m in live:
            official = cli["mtd"] if (cli["year"], cli["month"]) == (y, mo) else None
            official_base = official
            if official is None and since >= cli_window(date(y, mo, 1), st.tz)[0]:
                official_base = 0.0        # last month's final report; this month starts at zero
            if official_base is None:
                continue
            provisional = official_base + extra
            thr = float(m["floor_strike"])
            depth = None
            levels = []
            if provisional > thr:
                levels.append(("provisional", provisional))
            if official is not None and official > thr:
                levels.append(("official", official))
            for level, total in levels:
                if f"{level}_at" not in census.get(m.get("ticker", ""), {}) and depth is None:
                    book = kalshi.get_orderbook(http, m["ticker"])
                    depth = kalshi.depth_to_buy(book, "yes", 0.97) if book else None
                if record(census, m, city=series, level=level, total=total, now=now, depth=depth):
                    new += 1
                    log.info("rain census: %s passed %.2f in (%s total %.2f) ask %s",
                             m["ticker"], thr, level, total, kalshi.price(m, "yes_ask"))
    return new


# ---------------------------------------------------------------- report

def report_section(census: dict, cheap: float = 0.95) -> list[str]:
    out = ["## Rain census (silent test): monthly rain thresholds already passed", "",
           "Provisional = NWS report plus hourly station rain since; official = NWS climate "
           "report total. No alerts are sent for these.", ""]
    items = sorted(census.items(), key=lambda kv: kv[1].get("provisional_at") or
                   kv[1].get("official_at") or "")
    if not items:
        return out + ["No thresholds passed yet.", ""]
    out += ["| Market | Above | First seen (provisional) | Ask then | Official | Ask then | Cheapest ask |",
            "|---|---|---|---|---|---|---|"]

    def c(x):
        return "–" if x is None else f"{round(x * 100)}¢"

    for t, it in items[-25:]:
        out.append(f"| {t} | {it.get('threshold', 0):g} in | {(it.get('provisional_at') or '–')[:16]} | "
                   f"{c(it.get('provisional_ask'))} | {(it.get('official_at') or '–')[:16]} | "
                   f"{c(it.get('official_ask'))} | {c(it.get('min_ask'))} |")
    n = len(items)
    n_cheap = sum(1 for _, it in items if it.get("min_ask", 1.0) <= cheap)
    out += ["", f"{n_cheap} of {n} passed thresholds were ever offered at {round(cheap * 100)}¢ or less "
                "after we saw them pass."]
    return out + [""]
