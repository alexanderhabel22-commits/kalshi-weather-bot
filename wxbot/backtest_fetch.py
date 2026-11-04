"""Download history for a backtest: settled Kalshi weather markets with hourly
prices, archived forecasts (Open-Meteo previous runs), and past station
readings (Iowa Environmental Mesonet METAR archive).

Runs on GitHub Actions (the backtest workflow), since that's where the bot has
network access. Writes gzip'd JSON/CSV under the output folder and skips files
that already exist, so a re-run only fills gaps.
"""
from __future__ import annotations

import gzip
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import kalshi
from .stations import default_catalog
from .util import Http, HttpError

log = logging.getLogger("wxbot")
UTC = timezone.utc

PREV_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"
OM_MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless", "gem_seamless"]
IEM = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = obj if isinstance(obj, (bytes, str)) else json.dumps(obj, separators=(",", ":"))
    if isinstance(data, str):
        data = data.encode()
    with gzip.open(path, "wb") as f:
        f.write(data)


def read(path: Path):
    with gzip.open(path, "rb") as f:
        raw = f.read()
    return json.loads(raw) if path.name.endswith(".json.gz") else raw.decode()


def _ts(s: str | None) -> int | None:
    if not s:
        return None
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


# ---------------------------------------------------------------- Kalshi

def settled_markets(http: Http, series: str, since: datetime) -> list[dict]:
    out, cursor = [], None
    for _ in range(50):
        params = {"series_ticker": series, "status": "settled", "limit": 1000,
                  "min_close_ts": int(since.timestamp())}
        if cursor:
            params["cursor"] = cursor
        js = http.get_json(f"{kalshi.BASE}/markets", params=params)
        out.extend(js.get("markets") or [])
        cursor = js.get("cursor")
        if not cursor:
            break
    return out


def market_candles(http: Http, series: str, m: dict) -> list[dict]:
    start = _ts(m.get("open_time")) or (_ts(m.get("close_time")) - 3 * 86400)
    end = _ts(m.get("close_time"))
    js = http.get_json(f"{kalshi.BASE}/series/{series}/markets/{m['ticker']}/candlesticks",
                       params={"start_ts": start, "end_ts": end, "period_interval": 60})
    return js.get("candlesticks") or []


def fetch_series(series: str, out: Path, since: datetime, user_agent: str) -> str:
    path = out / "kalshi" / f"{series}.json.gz"
    if path.exists():
        return f"{series}: cached"
    http = Http(user_agent, min_interval=0.25)
    try:
        markets = settled_markets(http, series, since)
    except HttpError as exc:
        return f"{series}: markets failed ({exc})"
    candles, failed = {}, 0
    for m in markets:
        try:
            candles[m["ticker"]] = market_candles(http, series, m)
        except HttpError as exc:
            failed += 1
            if failed <= 3:
                log.warning("candles %s: %s", m.get("ticker"), exc)
    _write(path, {"series": series, "markets": markets, "candles": candles})
    return f"{series}: {len(markets)} markets, {len(candles)} with prices, {failed} failed"


# ---------------------------------------------------------------- forecasts

def fetch_forecasts(http: Http, st, out: Path, start: datetime, end: datetime) -> str:
    path = out / "forecasts" / f"{st.icao}.json.gz"
    if path.exists():
        return f"{st.icao} forecasts: cached"
    got = {}
    for model in OM_MODELS:
        try:
            js = http.get_json(PREV_RUNS, params={
                "latitude": st.lat, "longitude": st.lon, "models": model,
                "hourly": "temperature_2m,temperature_2m_previous_day1,temperature_2m_previous_day2",
                "start_date": start.date().isoformat(), "end_date": end.date().isoformat(),
                "temperature_unit": "fahrenheit", "timezone": "GMT"})
            got[model] = js.get("hourly") or {}
        except HttpError as exc:
            log.warning("previous runs %s %s: %s", st.icao, model, exc)
    _write(path, got)
    return f"{st.icao} forecasts: {', '.join(got) or 'none'}"


# ---------------------------------------------------------------- readings

def fetch_readings(http: Http, st, out: Path, start: datetime, end: datetime) -> str:
    path = out / "readings" / f"{st.icao}.csv.gz"
    if path.exists():
        return f"{st.icao} readings: cached"
    sid = st.icao[1:] if st.icao.startswith("K") else st.icao
    params = [("station", sid), ("data", "tmpf"), ("data", "metar"), ("tz", "Etc/UTC"),
              ("format", "onlycomma"), ("latlon", "no"), ("missing", "M"), ("direct", "no"),
              ("report_type", "3"), ("report_type", "4"),
              ("year1", start.year), ("month1", start.month), ("day1", start.day),
              ("year2", end.year), ("month2", end.month), ("day2", end.day)]
    try:
        r = http.session.get(IEM, params=params, timeout=120)
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - log and move on
        return f"{st.icao} readings: failed ({exc})"
    _write(path, r.text)
    return f"{st.icao} readings: {r.text.count(chr(10))} lines"


# ---------------------------------------------------------------- main

def run(out: Path, days: int, user_agent: str, workers: int = 4) -> list[str]:
    end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    catalog = default_catalog()
    lines = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for msg in pool.map(lambda si: fetch_series(si.ticker, out, start, user_agent), catalog):
            log.info(msg)
            lines.append(msg)
    http = Http(user_agent, timeout=60)
    stations = {si.station.icao: si.station for si in catalog}
    for st in stations.values():
        for fn in (fetch_forecasts, fetch_readings):
            msg = fn(http, st, out, start - timedelta(days=3), end)
            log.info(msg)
            lines.append(msg)
    meta = {"fetched": datetime.now(UTC).isoformat(timespec="seconds"),
            "start": start.date().isoformat(), "end": end.date().isoformat(), "log": lines}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    return lines
