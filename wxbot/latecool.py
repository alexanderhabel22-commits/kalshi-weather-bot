"""Paper twin (Nov 21, 2026): late-day highs "need", cool season, later start. PAPER ONLY.

Backtest (/mnt/project-files/analysis/weather-forecast-season/RESULTS.md, 13 months, 20 high
cities): in Oct-Feb, writing the highs "need" orders (sell the brackets above the high so far)
only from 9 hours before close (about 4 PM local standard time) instead of 12 hours earned
+3.7c/ct (dev, 981 fills) vs +1.8c, and +5.1c (holdout Sep-Nov 2026, 235 fills) vs +0.6c.
Total dollars about the same (half the fills at twice the cents). About 10 variants were
tried, so part of the gain may be luck. The live paper highs "need" (12h start) was -2.9c on
307 fills Nov 11-20.

This module reuses wxbot.latepaper's rule and fill check unchanged and only narrows it:
highs, kind "need", event dates in Oct-Feb, 9h-5h before close. It writes its own file
(data/paper_late_cool.csv), so the main paper log and the real-money weather_late mirror,
which reads data/paper_late.csv, are untouched. Nothing is sent to Kalshi.
Judge at about 100 filled orders. Real money only with Alex's explicit OK.
"""
from __future__ import annotations

import csv
from datetime import datetime

from . import kalshi, latepaper
from .util import DATA_DIR

ORDERS = DATA_DIR / "paper_late_cool.csv"
FIELDS = latepaper.FIELDS
DEFAULTS = {"hours_max": 9.0, "hours_min": 5.0, "only": ["high/need"],
            "months": [10, 11, 12, 1, 2]}


def load_orders() -> list[dict]:
    if not ORDERS.exists():
        return []
    with open(ORDERS, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_orders(rows: list[dict]) -> None:
    ORDERS.parent.mkdir(parents=True, exist_ok=True)
    tmp = ORDERS.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(ORDERS)


def place(orders: list[dict], today: dict, now: datetime, params: dict | None = None) -> int:
    """Same arguments as latepaper.place. Writes only the kinds in `only`, for event dates
    whose month is in `months`, inside the (later) hours window."""
    P = {**latepaper.DEFAULTS, **DEFAULTS, **(params or {})}
    only = {str(x) for x in P["only"]}
    metrics = {k.split("/")[0] for k in only}
    months = {int(x) for x in P["months"]}
    sub = {}
    for ev, v in today.items():
        day = kalshi.event_date(ev)
        if v[0].metric in metrics and day is not None and day.month in months:
            sub[ev] = v
    before = len(orders)
    latepaper.place(orders, sub, now, P)
    new = [o for o in orders[before:] if f"{o['metric']}/{o['kind']}" in only]
    orders[before:] = new
    return len(new)


def settle(http, orders: list[dict], now: datetime) -> int:
    return latepaper.settle(http, orders, now)
