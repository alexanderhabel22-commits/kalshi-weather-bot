"""Read Alex's Kalshi balance so the bankroll follows the account (read-only).

Every trade run (~5 min, right after each scan) asks Kalshi for the cash
balance and the value of open positions and saves the sum to
data/bankroll.json. Config.bankroll uses it for every bet size, cap and loss
stop, so a deposit or withdrawal changes the *limits* within minutes but is
never counted as profit or loss: P&L is always added up from the bot's own
trades (trade_orders.csv / equity.csv), never from the balance.

If the key is missing or the call fails, the old file ages out after
BANKROLL_MAX_AGE_H and the BANKROLL variable / config value is used.
"""
from __future__ import annotations

import logging
from datetime import datetime

from .kalshi_api import KalshiClient
from .util import BANKROLL_FILE, Http, write_json

log = logging.getLogger("wxbot")


def _cents(d: dict, key: str) -> float | None:
    """Kalshi gives money as integer cents (`key`) or a dollar string (`key_dollars`)."""
    if d.get(key) is not None:
        return float(d[key]) / 100.0
    if d.get(key + "_dollars") is not None:
        return float(d[key + "_dollars"])
    return None


def positions_value(client: KalshiClient) -> float:
    """Fallback when /portfolio/balance has no portfolio_value: open positions at cost."""
    total, cursor = 0.0, None
    for _ in range(20):
        params = {"limit": 1000, "count_filter": "position"}
        if cursor:
            params["cursor"] = cursor
        js = client._get("/portfolio/positions", params)
        for p in js.get("market_positions") or []:
            total += abs(_cents(p, "market_exposure") or 0.0)
        cursor = js.get("cursor")
        if not cursor:
            break
    return total


def fetch(client: KalshiClient) -> dict:
    js = client._get("/portfolio/balance")
    cash = _cents(js, "balance")
    if cash is None:
        raise ValueError(f"no balance in reply: {sorted(js)}")
    pos = _cents(js, "portfolio_value")
    src = "portfolio_value"
    if pos is None:
        pos, src = positions_value(client), "positions_at_cost"
    out = {"cash": round(cash, 2), "positions": round(pos, 2),
           "total": round(cash + pos, 2), "positions_source": src}
    shards = {}
    for b in js.get("balance_breakdown") or []:          # cash per exchange shard
        try:
            shards[str(int(b["exchange_index"]))] = round(float(b["balance"]), 2)
        except (KeyError, TypeError, ValueError):
            continue
    if shards:
        out["shards"] = shards
    return out


def refresh(http: Http, now: datetime, client: KalshiClient | None = None) -> dict | None:
    """Read the balance and save it. Never raises: sizing falls back instead."""
    try:
        client = client or KalshiClient.from_env(http)
        if client is None:
            return None
        info = fetch(client)
    except Exception as exc:  # a failed read must never stop trading
        log.warning("balance read failed (using fallback bankroll): %s", exc)
        return None
    if info["total"] <= 0:
        log.warning("balance read returned $%.2f; ignoring it", info["total"])
        return None
    info["at"] = now.isoformat()
    write_json(BANKROLL_FILE, info)
    log.info("bankroll from Kalshi: $%.2f (cash $%.2f + positions $%.2f)",
             info["total"], info["cash"], info["positions"])
    return info
