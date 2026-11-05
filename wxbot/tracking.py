"""Measurement logs the bot keeps about itself, so it can be graded:

  lock census  (data/census.json)    every bracket the readings had already
                                     decided, and the cheapest price seen for
                                     it. Shows whether locks are being missed
                                     or are already priced in by other traders.
  forecasts    (data/forecasts.csv)  the model's forecast for each city-day
                                     every 6 hours, later filled in with the
                                     real settlement value. Shows the model's
                                     bias and whether its ranges are honest.
"""
from __future__ import annotations

import csv
import logging
from datetime import datetime, timedelta

from . import kalshi
from .util import DATA_DIR, Http, read_json, write_json

log = logging.getLogger("wxbot")

CENSUS = DATA_DIR / "census.json"
FORECASTS = DATA_DIR / "forecasts.csv"
F_FIELDS = ["event", "series", "city", "metric", "date", "scan_at", "hours_to_end",
            "median", "p10", "p90", "nws", "obs_extreme", "sample_ticker", "actual"]


# ---------------------------------------------------------------- lock census

def load_census() -> dict:
    return read_json(CENSUS, {}) or {}


def census_update(census: dict, day: str, event: str, city: str, metric: str,
                  bracket_rows: list[dict], now: datetime) -> None:
    """Remember each locked bracket once per day with the cheapest ask seen."""
    today = census.setdefault(day, {})
    for b in bracket_rows:
        side = b.get("lock")
        if side not in ("yes", "no"):
            continue
        ask = b.get("yes_ask") if side == "yes" else b.get("no_ask")
        ask = ask if (ask is not None and 0 < ask < 0.995) else 1.0
        key = f"{b['ticker']}|{side}"
        cur = today.get(key)
        if cur is None:
            today[key] = {"city": city, "metric": metric, "event": event,
                          "bracket": b.get("bracket"), "first_seen": now.isoformat(timespec="seconds"),
                          "first_ask": round(ask, 4), "min_ask": round(ask, 4)}
        elif ask < cur["min_ask"]:
            cur["min_ask"] = round(ask, 4)


def save_census(census: dict, keep_days: int = 21) -> None:
    keep = sorted(census)[-keep_days:]
    write_json(CENSUS, {d: census[d] for d in keep})


# ---------------------------------------------------------------- forecasts

def load_forecasts() -> list[dict]:
    if not FORECASTS.exists():
        return []
    with open(FORECASTS, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_forecasts(rows: list[dict], keep_days: int = 45, now: datetime | None = None) -> None:
    if now:
        cutoff = (now - timedelta(days=keep_days)).date().isoformat()
        rows[:] = [r for r in rows if r.get("date", "") >= cutoff]
    FORECASTS.parent.mkdir(parents=True, exist_ok=True)
    tmp = FORECASTS.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=F_FIELDS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(FORECASTS)


def log_forecast(rows: list[dict], seen: set, *, event: str, series: str, city: str,
                 metric: str, date: str, dm, nws, window_end: datetime, now: datetime,
                 sample_ticker: str, every_hours: int = 6) -> bool:
    """Append one forecast snapshot per city-day per `every_hours` block."""
    block = f"{event}|{now:%Y%m%d}{now.hour // every_hours}"
    if block in seen:
        return False
    seen.add(block)
    rows.append({
        "event": event, "series": series, "city": city, "metric": metric, "date": date,
        "scan_at": now.isoformat(timespec="minutes"),
        "hours_to_end": f"{(window_end - now).total_seconds() / 3600:.1f}",
        "median": dm.median, "p10": dm.quantile(0.1), "p90": dm.quantile(0.9),
        "nws": "" if nws is None else f"{nws:.0f}",
        "obs_extreme": "" if dm.obs_extreme is None else f"{dm.obs_extreme:.1f}",
        "sample_ticker": sample_ticker, "actual": "",
    })
    return True


def forecast_blocks(rows: list[dict], every_hours: int = 6) -> set:
    out = set()
    for r in rows:
        try:
            t = datetime.fromisoformat(r["scan_at"])
        except (KeyError, ValueError):
            continue
        out.add(f"{r['event']}|{t:%Y%m%d}{t.hour // every_hours}")
    return out


def settle_forecasts(http: Http, rows: list[dict], now: datetime, max_calls: int = 40) -> int:
    """Fill in the real settlement temperature for finished city-days."""
    today = now.date().isoformat()
    by_event: dict[str, list[dict]] = {}
    for r in rows:
        if not r.get("actual") and r.get("date", "9999") < today:
            by_event.setdefault(r["event"], []).append(r)
    filled = calls = 0
    for ev, group in by_event.items():
        if calls >= max_calls:
            break
        calls += 1
        m = kalshi.get_market(http, group[0]["sample_ticker"])
        val = (m or {}).get("expiration_value")
        try:
            val = float(val)
        except (TypeError, ValueError):
            continue
        for r in group:
            r["actual"] = f"{val:g}"
            filled += 1
    return filled
