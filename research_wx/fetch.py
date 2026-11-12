"""Weather research download (Nov 11 2026): more forecast sources for the day-before
weather model. Read-only public data; never trades, never alerts.

usage: python research_wx/fetch.py out [section ...]
sections (default all): om, single, kalshi, readings, mos
"""
import gzip
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, ".")
from wxbot import backtest_fetch as bf  # noqa: E402
from wxbot.stations import default_catalog  # noqa: E402
from wxbot.util import Http  # noqa: E402

UTC = timezone.utc
UA = "kalshi-weather-research (github actions)"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
SECTIONS = sys.argv[2:] or ["om", "single", "kalshi", "readings", "mos"]
T0 = time.time()
BUDGET_S = 285 * 60
LOG = []

PREV = "https://previous-runs-api.open-meteo.com/v1/forecast"
SINGLE = "https://single-runs-api.open-meteo.com/v1/forecast"
OM_MODELS = [
    "ncep_nbm_conus", "ncep_hrrr_conus", "ncep_nam_conus", "ncep_gfs013", "ncep_gfs025",
    "ncep_gfs_graphcast025", "ncep_aigfs025", "ecmwf_ifs", "ecmwf_ifs025", "ecmwf_aifs025_single",
    "ukmo_seamless", "meteofrance_seamless", "jma_seamless", "gem_hrdps_continental",
    "gem_regional", "gem_global", "icon_global", "kma_seamless", "cma_grapes_global",
    "bom_access_global", "best_match", "gfs_seamless", "icon_seamless", "gem_seamless",
]
OM_VARS = ["temperature_2m"] + [f"temperature_2m_previous_day{i}" for i in (1, 2, 3)]
START = datetime(2025, 9, 25, tzinfo=UTC)
MOS_MODELS = {"NBE": START, "MET": START, "ECM": START, "MEX": START, "NAM": START,
              "LAV": datetime(2026, 5, 1, tzinfo=UTC),
              "NBS": datetime(2026, 10, 25, tzinfo=UTC), "GFS": datetime(2026, 10, 25, tzinfo=UTC)}


def say(msg):
    line = f"[{int(time.time() - T0):5d}s] {msg}"
    print(line, flush=True)
    LOG.append(line)


def left():
    return BUDGET_S - (time.time() - T0)


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = obj if isinstance(obj, (bytes, str)) else json.dumps(obj, separators=(",", ":"))
    with gzip.open(path, "wb") as f:
        f.write(data.encode() if isinstance(data, str) else data)


def om_get(sess, url, params):
    """(json or None, note). Waits out rate limits; a 400 is the caller's to read."""
    for attempt in range(7):
        try:
            r = sess.get(url, params=params, timeout=120)
        except requests.RequestException as exc:
            time.sleep(10 * (attempt + 1))
            note = f"network {exc}"
            continue
        if r.status_code == 200:
            return r.json(), "ok"
        note = f"{r.status_code} {r.text[:160]}"
        if r.status_code == 400:
            return None, note
        if "aily" in r.text and r.status_code == 429:
            return None, "DAILY " + note
        time.sleep(65 if r.status_code == 429 else 15 * (attempt + 1))
    return None, note


def section_om(stations, end):
    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    dead = set()
    for st in stations:
        for model in OM_MODELS:
            if model in dead or left() < 600:
                continue
            path = OUT / "om" / f"{st.icao}__{model}.json.gz"
            js, note = om_get(sess, PREV, {
                "latitude": st.lat, "longitude": st.lon, "models": model,
                "hourly": ",".join(OM_VARS), "start_date": START.date().isoformat(),
                "end_date": end.date().isoformat(), "temperature_unit": "fahrenheit",
                "timezone": "GMT"})
            time.sleep(5)
            if js is None:
                say(f"om {st.icao} {model}: {note}")
                if note.startswith("400"):
                    dead.add(model)
                if note.startswith("DAILY"):
                    return
                continue
            h = js.get("hourly") or {}
            counts = {v: sum(x is not None for x in h.get(v) or []) for v in OM_VARS}
            if not any(counts.values()):
                say(f"om {st.icao} {model}: empty")
                dead.add(model)
                continue
            write(path, {"lat": js.get("latitude"), "lon": js.get("longitude"),
                         "elev": js.get("elevation"), "hourly": h})
            say(f"om {st.icao} {model}: {counts}")


def section_single(stations):
    """Can we ask Open-Meteo for one exact model run (clean timing)? Keep raw answers."""
    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    st = stations[0]
    for model in ("ncep_nbm_conus", "ncep_gfs013", "ecmwf_ifs025", "ncep_hrrr_conus", "gfs_seamless"):
        for run in ("2026-11-01T12:00", "2026-08-01T12:00", "2026-03-01T12:00", "2025-10-15T12:00"):
            for extra in ({"run": run}, {"run": run, "forecast_days": 4}):
                js, note = om_get(sess, SINGLE, {
                    "latitude": st.lat, "longitude": st.lon, "models": model,
                    "hourly": "temperature_2m", "temperature_unit": "fahrenheit",
                    "timezone": "GMT", **extra})
                time.sleep(2)
                tag = f"{model}_{run.replace(':', '')}_{len(extra)}"
                if js is None:
                    say(f"single {tag}: {note}")
                    continue
                h = js.get("hourly") or {}
                n = sum(x is not None for x in h.get("temperature_2m") or [])
                say(f"single {tag}: {n} values, first {((h.get('time') or ['?'])[0])}")
                write(OUT / "single" / f"{tag}.json.gz", js)
                break


def section_kalshi(catalog):
    since = datetime(2026, 10, 25, tzinfo=UTC)
    for si in catalog:
        if left() < 600:
            return
        say(bf.fetch_series(si.ticker, OUT, since, UA))


def section_readings(stations, end):
    http = Http(UA, timeout=60)
    for st in stations:
        if left() < 600:
            return
        say(bf.fetch_readings(http, st, OUT, datetime(2026, 10, 28, tzinfo=UTC), end))


def section_mos(stations, end):
    http = Http(UA, timeout=120)
    dead = set()
    for model, start in MOS_MODELS.items():
        step = 31 if model == "LAV" else 92
        for st in stations:
            if model in dead or left() < 300:
                continue
            parts, a = [], start
            while a < end:
                b = min(a + timedelta(days=step), end)
                text = bf._iem_get(http, bf.IEM_MOS, [
                    ("station", st.icao), ("model", model), ("format", "csv"),
                    ("sts", a.strftime("%Y-%m-%dT%H:%MZ")), ("ets", b.strftime("%Y-%m-%dT%H:%MZ"))],
                    f"mos {st.icao} {model}")
                if text is None:
                    say(f"mos {st.icao} {model} {a.date()}: failed")
                else:
                    lines = text.splitlines()
                    parts.append("\n".join(lines if not parts else lines[1:]))
                a = b
            body = "\n".join(p for p in parts if p)
            n = body.count("\n")
            say(f"mos {st.icao} {model}: {n} lines")
            if n < 5 and st is stations[0]:
                dead.add(model)       # this archive doesn't have the model
                continue
            write(OUT / "mos" / f"{st.icao}_{model}.csv.gz", body)


def main():
    catalog = default_catalog()
    stations = list({si.station.icao: si.station for si in catalog}.values())
    end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    jobs = {"om": lambda: section_om(stations, end), "single": lambda: section_single(stations),
            "kalshi": lambda: section_kalshi(catalog), "readings": lambda: section_readings(stations, end),
            "mos": lambda: section_mos(stations, end)}
    for name in SECTIONS:
        say(f"== {name}")
        try:
            jobs[name]()
        except Exception as exc:  # noqa: BLE001 - keep what we have
            say(f"{name} crashed: {exc!r}")
        (OUT / "log.txt").parent.mkdir(parents=True, exist_ok=True)
        (OUT / "log.txt").write_text("\n".join(LOG))


if __name__ == "__main__":
    main()
