"""Snapshot of current Kalshi prices for our open orders (for the dashboard).

Written to data/quotes.json after each trade run, so the read-only dashboard
can show how close each resting order is to the market even when the browser
can't call Kalshi directly. Public market data only; read-only.
"""
from __future__ import annotations

import csv
import logging
from datetime import datetime

from . import kalshi
from .util import DATA_DIR, Http, write_json

log = logging.getLogger("wxbot")

QUOTES = DATA_DIR / "quotes.json"
MAX_TICKERS = 60


def open_tickers() -> list[str]:
    """Tickers of open trader orders (resting or filled, unsettled) and open paper orders."""
    out: list[str] = []
    for path, statuses in ((DATA_DIR / "trade_orders.csv", ("resting", "filled")),
                           (DATA_DIR / "paper_orders.csv", ("open",))):
        if not path.exists():
            continue
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("status") in statuses and r.get("ticker") and r["ticker"] not in out:
                    out.append(r["ticker"])
    return out[:MAX_TICKERS]


def quote(m: dict) -> dict:
    yb, ya = kalshi.price(m, "yes_bid"), kalshi.price(m, "yes_ask")
    return {"yes_bid": yb, "yes_ask": ya,
            "no_bid": round(1 - ya, 2) if ya is not None else None,
            "no_ask": round(1 - yb, 2) if yb is not None else None,
            "last": kalshi.price(m, "last_price"), "volume": m.get("volume"),
            "status": m.get("status"), "result": m.get("result") or ""}


def snapshot(http: Http, now: datetime, tickers: list[str] | None = None) -> dict:
    tickers = open_tickers() if tickers is None else tickers
    quotes = {}
    for tk in tickers:
        m = kalshi.get_market(http, tk)
        if m:
            quotes[tk] = quote(m)
    out = {"at": now.isoformat(timespec="seconds"), "quotes": quotes}
    write_json(QUOTES, out)
    log.info("quotes: %d of %d open tickers", len(quotes), len(tickers))
    return out
