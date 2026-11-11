"""Scheduled US macro releases (CPI, jobs, PCE, GDP, Fed) for the paper "skip release
windows" rule (Alex OK'd a paper track Nov 10, 2026; research thread "Mine years of price
history for 15-min edges": 15-min favourites with 5-10 min left held 69-75% in release
windows vs 82-84% at the same clock times on normal days, BTC/ETH/silver 2021-2026).

Paper only: nothing here touches orders. The reviews split results into release windows
vs the rest so the rule can be judged at each market's 500-contract review.

Release times come from Kalshi's own econ markets, which close a few minutes before the
print (e.g. 8:25 AM ET for an 8:30 CPI). The Fed press conference window (+30 min) is
added after every decision.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .util import DATA_DIR, read_json, write_json

PATH = Path(DATA_DIR) / "macro_releases.json"
SERIES = {"KXCPI": "cpi", "KXPAYROLLS": "jobs", "KXPCECORE": "pce", "KXGDP": "gdp",
          "KXFEDDECISION": "fomc"}
BASE = "https://api.elections.kalshi.com/trade-api/v2"
WINDOW_S = 900


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def release_ts(close_time: str) -> int | None:
    """Kalshi econ market close -> release time (next :00 or :30), or None if odd."""
    try:
        c = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if c.minute not in (25, 29, 55, 59):
        return None
    return (int(c.timestamp()) // 1800 + 1) * 1800


def load(path: Path = PATH) -> dict[int, str]:
    js = read_json(path, {}) or {}
    out = {}
    for k, v in (js.get("releases") or {}).items():
        try:
            out[int(datetime.fromisoformat(k.replace("Z", "+00:00")).timestamp())] = v
        except ValueError:
            continue
    return out


def refresh(http, now: datetime, path: Path = PATH) -> int:
    """Add upcoming/recent release times from Kalshi. Returns how many are known."""
    known = load(path)
    lo = int((now - timedelta(days=45)).timestamp())
    for series, kind in SERIES.items():
        cursor = None
        for _ in range(10):
            params = {"series_ticker": series, "limit": 1000, "min_close_ts": lo}
            if cursor:
                params["cursor"] = cursor
            try:
                js = http.get_json(f"{BASE}/markets", params=params)
            except Exception as exc:  # calendar refresh must never break anything
                logging.warning("release calendar %s: %s", series, exc)
                break
            for m in js.get("markets") or []:
                ts = release_ts(m.get("close_time") or "")
                if ts:
                    known[ts] = kind
                    if kind == "fomc":
                        known[ts + 1800] = "fomc_presser"
            cursor = js.get("cursor")
            if not cursor:
                break
    js = read_json(path, {}) or {}
    js["releases"] = {_iso(t): k for t, k in sorted(known.items())}
    js["updated"] = now.isoformat(timespec="seconds")
    write_json(path, js)
    return len(known)


def window_kind(close: str, known: dict[int, str]) -> str | None:
    """The release inside the 15-minute window that closes at `close`, if any."""
    try:
        end = int(datetime.fromisoformat(close.replace("Z", "+00:00")).timestamp())
    except (AttributeError, ValueError):
        return None
    start = end - WINDOW_S
    for t in range(start - start % 1800, end, 1800):
        if start <= t < end and t in known:
            return known[t]
    return None
