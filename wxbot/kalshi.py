"""Kalshi public market data (no API key needed) plus bracket and fee math."""
from __future__ import annotations

import logging
import math
import re
from datetime import date
from urllib.parse import quote

from .util import Http, HttpError

log = logging.getLogger("wxbot")

BASE = "https://api.elections.kalshi.com/trade-api/v2"
_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


# ---------------------------------------------------------------- API calls

def list_open_markets(http: Http, series: str) -> list[dict]:
    out, cursor = [], None
    for _ in range(10):
        params = {"series_ticker": series, "status": "open", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        js = http.get_json(f"{BASE}/markets", params=params)
        out.extend(js.get("markets") or [])
        cursor = js.get("cursor")
        if not cursor:
            break
    return out


def get_market(http: Http, ticker: str) -> dict | None:
    try:
        return http.get_json(f"{BASE}/markets/{ticker}").get("market")
    except HttpError as exc:
        log.warning("market %s: %s", ticker, exc)
        return None


def list_weather_series(http: Http) -> list[dict]:
    """All series in Kalshi's climate category (for auto-discovery)."""
    for category in ("Climate and Weather", "Climate"):
        try:
            js = http.get_json(f"{BASE}/series", params={"category": category})
        except HttpError:
            continue
        series = js.get("series") or []
        if series:
            return series
    return []


def get_orderbook(http: Http, ticker: str) -> dict | None:
    """Returns {"yes": [(price$, qty)], "no": [(price$, qty)]} (bids), or None."""
    try:
        js = http.get_json(f"{BASE}/markets/{ticker}/orderbook")
    except HttpError as exc:
        log.info("orderbook %s unavailable: %s", ticker, exc)
        return None
    return parse_orderbook(js)


def parse_orderbook(js: dict) -> dict | None:
    for key in ("orderbook_fp", "orderbook"):
        ob = js.get(key)
        if not isinstance(ob, dict):
            continue
        if "yes_dollars" in ob or "no_dollars" in ob:
            return {side: [(float(p), float(q)) for p, q in (ob.get(f"{side}_dollars") or [])]
                    for side in ("yes", "no")}
        if "yes" in ob or "no" in ob:
            return {side: [(float(p) / 100.0, float(q)) for p, q in (ob.get(side) or [])]
                    for side in ("yes", "no")}
    return None


def depth_to_buy(book: dict | None, side: str, limit: float) -> float | None:
    """Contracts available to BUY `side` at or below `limit` dollars.

    Buying YES at price a matches a resting NO bid at (1 - a), and vice versa.
    """
    if not book:
        return None
    opposite = "no" if side == "yes" else "yes"
    total = 0.0
    for price, qty in book.get(opposite, []):
        if 1.0 - price <= limit + 1e-9:
            total += qty
    return total


# ---------------------------------------------------------------- parsing

def price(m: dict, key: str) -> float | None:
    """Price field in dollars, handling both *_dollars strings and cent ints."""
    v = m.get(f"{key}_dollars")
    if v not in (None, ""):
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
    v = m.get(key)
    if v in (None, ""):
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    units = (m.get("response_price_units") or "usd_cent").lower()
    return v / 100.0 if "cent" in units else v


def tradable(p: float | None) -> bool:
    return p is not None and 0.0 < p < 0.995


def event_date(event_ticker: str) -> date | None:
    """KXHIGHNY-26NOV03 -> date(2026, 11, 3)."""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})$", event_ticker or "")
    if not m or m.group(2) not in _MONTHS:
        return None
    try:
        return date(2000 + int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3)))
    except ValueError:
        return None


_ABOVE = re.compile(r"(-?\d+)\s*°?\s*(?:F\s*)?or (?:above|higher|more)", re.I)
_BELOW = re.compile(r"(-?\d+)\s*°?\s*(?:F\s*)?or (?:below|lower|less)", re.I)
_RANGE = re.compile(r"(-?\d+)\s*°?\s*(?:F\s*)?(?:to|-|–)\s*(-?\d+)\s*°?", re.I)


def bracket_from_text(text: str) -> tuple[int | None, int | None] | None:
    if not text:
        return None
    if m := _ABOVE.search(text):
        return int(m.group(1)), None
    if m := _BELOW.search(text):
        return None, int(m.group(1))
    if m := _RANGE.search(text):
        a, b = int(m.group(1)), int(m.group(2))
        return min(a, b), max(a, b)
    return None


def bracket_from_strikes(m: dict) -> tuple[int | None, int | None] | None:
    """Kalshi semantics (verified on KXHIGHNY): greater = value > floor,
    less = value < cap, between = floor <= value <= cap (integers, inclusive)."""
    st = (m.get("strike_type") or "").lower()
    fl, cap = m.get("floor_strike"), m.get("cap_strike")

    def i(x):
        return None if x is None else float(x)

    fl, cap = i(fl), i(cap)
    if st == "greater" and fl is not None:
        return math.floor(fl) + 1, None
    if st == "greater_or_equal" and fl is not None:
        return math.ceil(fl), None
    if st == "less" and cap is not None:
        return None, math.ceil(cap) - 1
    if st == "less_or_equal" and cap is not None:
        return None, math.floor(cap)
    if st == "between" and fl is not None and cap is not None:
        return math.ceil(fl), math.floor(cap)
    return None


def parse_bracket(m: dict) -> tuple[int | None, int | None] | None:
    """Inclusive integer °F range [lo, hi] that pays YES. None = can't parse.

    Uses the strike fields and the human label, and refuses the market if they
    disagree (protects against misreading a contract)."""
    a = bracket_from_strikes(m)
    b = bracket_from_text(m.get("yes_sub_title") or m.get("subtitle") or "")
    if a and b and a != b:
        log.warning("bracket mismatch on %s: strikes=%s label=%s", m.get("ticker"), a, b)
        return None
    return a or b


def bracket_label(lo: int | None, hi: int | None) -> str:
    if lo is None:
        return f"{hi}° or below"
    if hi is None:
        return f"{lo}° or above"
    return f"{lo}° to {hi}°" if lo != hi else f"{lo}°"


# ---------------------------------------------------------------- fees

FEE_RATE = 0.07


def fee_per_contract(p: float, rate: float = FEE_RATE) -> float:
    return rate * p * (1.0 - p)


def taker_fee(contracts: int, p: float, rate: float = FEE_RATE) -> float:
    """Kalshi general taker fee: round_up(0.07 * C * P * (1-P)) to the cent."""
    raw = rate * contracts * p * (1.0 - p)
    return math.ceil(round(raw * 100, 6)) / 100.0


def market_url(ticker: str) -> str:
    """Kalshi web link for a market (or event) ticker.

    kalshi.com/markets/<series> without its slug shows "something went wrong";
    kalshi.com/events/<ticker> resolves to the right page (same format as the dashboard).
    """
    return f"https://kalshi.com/events/{quote(ticker.lower(), safe='')}"
