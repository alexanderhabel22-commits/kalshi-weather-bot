"""Weather data: live station observations (NWS), NWS forecast highs/lows,
and multi-model ensemble forecasts (Open-Meteo). All temperatures in °F."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .stations import Station
from .util import (CACHE_DIR, UTC, Http, HttpError, c_to_f, parse_iso,
                   parse_iso_duration, read_json, write_json)

log = logging.getLogger("wxbot")

NWS = "https://api.weather.gov"
NWS_HEADERS = {"Accept": "application/geo+json"}
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"


@dataclass
class Obs:
    time: datetime
    temp_f: float
    precise: bool          # 0.1 °C METAR T-group (or 6-hr extreme group)
    source: str = "obs"    # "obs", "max6", "min6"


@dataclass
class Observations:
    temps: list[Obs]       # point readings
    max6: list[Obs]        # 6-hour maxima, stamped at END of their 6-h period
    min6: list[Obs]        # 6-hour minima, stamped at END of their 6-h period


# ---------------------------------------------------------------- METAR

def _tenths(sign: str, digits: str) -> float:
    v = int(digits) / 10.0
    return -v if sign == "1" else v


def parse_metar(raw: str) -> dict:
    """Extract precise temperature groups from a METAR's remarks.

    T snTTT snTTT  -> temperature/dewpoint, 0.1 °C
    1 snTTT        -> max temp of the past 6 h, 0.1 °C (synoptic hours)
    2 snTTT        -> min temp of the past 6 h, 0.1 °C
    """
    out: dict = {}
    if not raw:
        return out
    if m := re.search(r"\bT([01])(\d{3})([01])(\d{3})\b", raw):
        out["t_c"] = _tenths(m.group(1), m.group(2))
    elif m := re.search(r"\bT([01])(\d{3})\b", raw):
        out["t_c"] = _tenths(m.group(1), m.group(2))
    if "RMK" in raw:
        for tok in raw.split("RMK", 1)[1].split():
            if re.fullmatch(r"1[01]\d{3}", tok):
                out["max6_c"] = _tenths(tok[1], tok[2:])
            elif re.fullmatch(r"2[01]\d{3}", tok):
                out["min6_c"] = _tenths(tok[1], tok[2:])
    return out


def parse_observations(js: dict) -> Observations:
    temps, max6, min6 = [], [], []
    for feat in js.get("features") or []:
        p = feat.get("properties") or {}
        t = parse_iso(p.get("timestamp"))
        if t is None:
            continue
        temp = p.get("temperature") or {}
        qc = (temp.get("qualityControl") or "").upper()
        raw = p.get("rawMessage") or ""
        groups = parse_metar(raw)
        if qc not in ("X", "Q"):
            if "t_c" in groups:
                temps.append(Obs(t, c_to_f(groups["t_c"]), True))
            elif temp.get("value") is not None:
                # No 0.1 °C group: 5-minute/whole-°C data, can be off by 1-2 °F
                temps.append(Obs(t, c_to_f(float(temp["value"])), False))
        if "max6_c" in groups:
            max6.append(Obs(t, c_to_f(groups["max6_c"]), True, "max6"))
        if "min6_c" in groups:
            min6.append(Obs(t, c_to_f(groups["min6_c"]), True, "min6"))
    # Drop physically absurd values and isolated spikes
    temps = [o for o in temps if -60 <= o.temp_f <= 135]
    temps.sort(key=lambda o: o.time)
    temps = _despike(temps)
    return Observations(temps, sorted(max6, key=lambda o: o.time), sorted(min6, key=lambda o: o.time))


def _despike(obs: list[Obs], limit_f: float = 9.0) -> list[Obs]:
    if len(obs) < 3:
        return obs
    keep = [obs[0]]
    for i in range(1, len(obs) - 1):
        prev, cur, nxt = obs[i - 1].temp_f, obs[i].temp_f, obs[i + 1].temp_f
        if abs(cur - prev) > limit_f and abs(cur - nxt) > limit_f:
            log.info("dropping spike %.1fF at %s", cur, obs[i].time)
            continue
        keep.append(obs[i])
    keep.append(obs[-1])
    return keep


def fetch_observations(http: Http, icao: str, start: datetime, end: datetime) -> Observations:
    js = http.get_json(f"{NWS}/stations/{icao}/observations",
                       params={"start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "end": end.strftime("%Y-%m-%dT%H:%M:%SZ")},
                       headers=NWS_HEADERS)
    return parse_observations(js)


# ---------------------------------------------------------------- NWS forecast

def fetch_nws_extremes(http: Http, st: Station, max_age_min: int = 120) -> dict[str, dict]:
    """NWS gridpoint forecast highs/lows by local date: {"2026-11-03": {"high": f, "low": f}}."""
    cache_path = CACHE_DIR / f"nws_{st.icao}.json"
    cached = read_json(cache_path)
    if cached and time.time() - cached.get("fetched", 0) < max_age_min * 60:
        return cached["extremes"]
    try:
        grid_url = cached.get("grid_url") if cached else None
        if not grid_url:
            pts = http.get_json(f"{NWS}/points/{st.lat:.4f},{st.lon:.4f}", headers=NWS_HEADERS)
            grid_url = pts["properties"]["forecastGridData"]
        grid = http.get_json(grid_url, headers=NWS_HEADERS)
        extremes = parse_nws_grid(grid, st.tz)
    except (HttpError, KeyError, TypeError, ValueError) as exc:
        log.info("NWS forecast unavailable for %s: %s", st.icao, exc)
        return (cached or {}).get("extremes", {})
    write_json(cache_path, {"fetched": time.time(), "grid_url": grid_url, "extremes": extremes})
    return extremes


def parse_nws_grid(grid: dict, tz: str) -> dict[str, dict]:
    props = grid.get("properties") or {}
    out: dict[str, dict] = {}
    zone = ZoneInfo(tz)
    for key, label in (("maxTemperature", "high"), ("minTemperature", "low")):
        block = props.get(key) or {}
        uom = (block.get("uom") or "wmoUnit:degC").lower()
        for v in block.get("values") or []:
            if v.get("value") is None or "/" not in (v.get("validTime") or ""):
                continue
            start_s, dur_s = v["validTime"].split("/", 1)
            start = parse_iso(start_s)
            mid = start + parse_iso_duration(dur_s) / 2
            d = mid.astimezone(zone).date().isoformat()
            val = float(v["value"])
            out.setdefault(d, {})[label] = c_to_f(val) if "degc" in uom else val
    return out


# ---------------------------------------------------------------- ensemble

@dataclass
class Ensemble:
    times: list[datetime]                 # hourly, UTC
    members: list[list[float | None]]     # each member's hourly °F
    models: str

    def mean_at(self, idx: int) -> float | None:
        vals = [m[idx] for m in self.members if m[idx] is not None]
        return sum(vals) / len(vals) if vals else None


MODEL_CHAINS = [
    "gfs025,ecmwf_ifs025,icon_seamless,gem_global",
    "ncep_gefs025,ecmwf_ifs025_ensemble,icon_seamless_eps,gem_global_ensemble",
    "ecmwf_ifs025",
    "",
]


def parse_ensemble(js: dict, models: str) -> Ensemble:
    hourly = js.get("hourly") or {}
    raw_times = hourly.get("time") or []
    times = []
    for t in raw_times:
        if isinstance(t, (int, float)):
            times.append(datetime.fromtimestamp(t, UTC))
        else:
            times.append(parse_iso(t))
    members = []
    for key, series in hourly.items():
        if not key.startswith("temperature_2m") or not isinstance(series, list):
            continue
        if len(series) != len(times):
            continue
        if sum(v is not None for v in series) < len(series) * 0.5:
            continue  # model doesn't cover this range
        members.append([None if v is None else float(v) for v in series])
    return Ensemble(times, members, models)


def fetch_ensemble(http: Http, st: Station, preferred: str, max_age_min: int = 90) -> Ensemble | None:
    cache_path = CACHE_DIR / f"ens_{st.icao}.json"
    cached = read_json(cache_path)
    if cached and time.time() - cached.get("fetched", 0) < max_age_min * 60:
        return parse_ensemble(cached["js"], cached.get("models", ""))
    chains = [preferred] + [c for c in MODEL_CHAINS if c != preferred]
    for models in chains:
        params = {"latitude": f"{st.lat:.4f}", "longitude": f"{st.lon:.4f}",
                  "hourly": "temperature_2m", "temperature_unit": "fahrenheit",
                  "timezone": "GMT", "timeformat": "unixtime",
                  "past_days": 1, "forecast_days": 3}
        if models:
            params["models"] = models
        try:
            js = http.get_json(ENSEMBLE_URL, params=params)
        except HttpError as exc:
            log.info("ensemble models=%r failed for %s: %s", models, st.icao, exc)
            continue
        ens = parse_ensemble(js, models)
        if len(ens.members) >= 10:
            write_json(cache_path, {"fetched": time.time(), "models": models, "js": js})
            return ens
    if cached:  # stale beats nothing
        log.warning("using stale ensemble for %s", st.icao)
        return parse_ensemble(cached["js"], cached.get("models", ""))
    return None
