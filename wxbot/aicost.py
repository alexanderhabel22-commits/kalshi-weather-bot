"""AI spend next to bot profit in the daily/weekly/monthly reports (Alex, Nov 11, 2026).

Reads the public m15-data branch: m15/watch.json (the Foreman's per-day total of every Claude API
call on the server, from its spend ledger) and m15/status.json (the 15-min bot's real-money P&L by
day). Days before the ledger existed come from Alex's Anthropic usage export.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import date

REPO = os.environ.get("GITHUB_REPO", "alexanderhabel22-commits/kalshi-weather-bot")
RAW = f"https://raw.githubusercontent.com/{REPO}/m15-data/"
# from Alex's usage export (kalshi-watcher key): Nov 11 = $24.10 Opus (Copycat boss) + $0.05 Haiku
KNOWN = {"2026-11-11": 24.15}


def _get(path: str):
    try:
        with urllib.request.urlopen(RAW + path, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception:  # noqa: BLE001
        return None


def load() -> dict:
    """{"ai": {day: usd}, "m15": {day: live pnl}} or {} when the data can't be read."""
    w, s = _get("m15/watch.json"), _get("m15/status.json")
    if w is None and s is None:
        return {}
    ai = dict(KNOWN)
    for k, v in ((w or {}).get("ai_history") or {}).items():
        ai[k] = max(float(v or 0), ai.get(k, 0.0))
    m15 = {k: float(v or 0) for k, v in (((s or {}).get("pnl") or {}).get("live_days") or {}).items()}
    return {"ai": ai, "m15": m15}


def period(data: dict, start: date, end: date) -> dict | None:
    if not data:
        return None
    inside = lambda d: start.isoformat() <= d <= end.isoformat()  # noqa: E731
    return {"ai": round(sum(v for k, v in data.get("ai", {}).items() if inside(k)), 2),
            "m15": round(sum(v for k, v in data.get("m15", {}).items() if inside(k)), 2)}
