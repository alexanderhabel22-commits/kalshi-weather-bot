"""Signed Kalshi trading API client (needs an API key; used only by the trader).

The key comes from the environment (GitHub secrets):
  KALSHI_API_KEY_ID     the key id shown when you create the key
  KALSHI_PRIVATE_KEY    the private key file's full text (PEM)

Every request is signed with RSA-PSS over timestamp + method + path. This
client can only place LIMIT orders (resting post-only, or immediate-or-cancel
at a stated price); there is no market-order method, and every
order is checked again here (price 1-99c, whole contracts, hard size ceiling).
"""
from __future__ import annotations

import base64
import logging
import os
import time
from urllib.parse import urlparse

from .kalshi import BASE
from .util import Http, HttpError

log = logging.getLogger("wxbot")

HARD_MAX_CONTRACTS = 25          # no single order bigger than this, whatever the config says


class OrderRejected(Exception):
    pass


def _load_key(pem: str):
    from cryptography.hazmat.primitives import serialization
    return serialization.load_pem_private_key(pem.encode(), password=None)


class KalshiClient:
    def __init__(self, http: Http, key_id: str, private_key_pem: str):
        self.http = http
        self.key_id = key_id
        self.key = _load_key(private_key_pem.replace("\\n", "\n"))

    @classmethod
    def from_env(cls, http: Http) -> "KalshiClient | None":
        kid = os.environ.get("KALSHI_API_KEY_ID", "").strip()
        pem = os.environ.get("KALSHI_PRIVATE_KEY", "").strip()
        if not kid or not pem:
            return None
        return cls(http, kid, pem)

    # ---------------------------------------------------------------- signing

    def headers(self, method: str, url: str, ts_ms: int | None = None) -> dict:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        ts = str(ts_ms if ts_ms is not None else int(time.time() * 1000))
        path = urlparse(url).path
        sig = self.key.sign((ts + method.upper() + path).encode(),
                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                        salt_length=padding.PSS.DIGEST_LENGTH),
                            hashes.SHA256())
        return {"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode()}

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = BASE + path
        return self.http.get_json(url, params=params, headers=self.headers("GET", url))

    def _post(self, path: str, payload: dict) -> dict:
        url = BASE + path
        return self.http.post_json(url, payload, headers=self.headers("POST", url))

    def _delete(self, path: str) -> dict:
        url = BASE + path
        r = self.http._request("DELETE", url, headers=self.headers("DELETE", url))
        try:
            return r.json()
        except ValueError:
            return {}

    # ---------------------------------------------------------------- calls

    def balance(self) -> float:
        js = self._get("/portfolio/balance")
        return float(js.get("balance", 0)) / 100.0

    def get_order(self, order_id: str) -> dict:
        return self._get(f"/portfolio/orders/{order_id}").get("order") or {}

    def cancel(self, order_id: str, ticker: str = "") -> None:
        path = f"/portfolio/events/orders/{order_id}"
        if ticker:
            path += f"?market_ticker={ticker}"
        try:
            self._delete(path)
        except HttpError as exc:
            if exc.status != 404:
                raise

    # Orders use Kalshi's V2 endpoint (POST /portfolio/events/orders; the old
    # /portfolio/orders create was retired, HTTP 410). V2 has one YES book:
    # side "bid" = buy YES, "ask" = sell YES (= buy NO); price is the YES price
    # as a dollar string, count a string.
    def _order_v2(self, ticker: str, side: str, buy: bool, price: float, count: int,
                  client_order_id: str, tif: str, post_only: bool = False,
                  reduce_only: bool = False) -> dict:
        check_order(side, price, count)
        yes_px = price if side == "yes" else 1.0 - price
        book = "bid" if (side == "yes") == buy else "ask"
        payload = {"ticker": ticker, "client_order_id": client_order_id, "side": book,
                   "count": str(int(count)), "price": f"{round(yes_px, 2):.2f}",
                   "time_in_force": tif, "self_trade_prevention_type": "taker_at_cross"}
        if post_only:
            payload["post_only"] = True
        if reduce_only:
            payload["reduce_only"] = True
        js = self._post("/portfolio/events/orders", payload)
        return js.get("order") or js

    def place_limit(self, ticker: str, side: str, price: float, count: int,
                    client_order_id: str) -> dict:
        """Resting BUY limit order (post-only: never crosses the spread)."""
        return self._order_v2(ticker, side, True, price, count, client_order_id,
                              "good_till_canceled", post_only=True)

    def buy_now(self, ticker: str, side: str, price: float, count: int,
                client_order_id: str) -> dict:
        """Buy at `price` or better right now (takes the ask); whatever doesn't
        fill immediately is cancelled, never left resting."""
        return self._order_v2(ticker, side, True, price, count, client_order_id,
                              "immediate_or_cancel")

    def sell_now(self, ticker: str, side: str, price: float, count: int,
                 client_order_id: str) -> dict:
        """Sell contracts we hold, at `price` or better, immediately; whatever
        doesn't fill right away is cancelled (never left resting). reduce_only:
        it can only shrink a position, never open a new one."""
        return self._order_v2(ticker, side, False, price, count, client_order_id,
                              "immediate_or_cancel", reduce_only=True)


def check_order(side: str, price: float, count: int) -> None:
    if side not in ("yes", "no"):
        raise OrderRejected(f"bad side {side!r}")
    if not isinstance(count, int) or not (1 <= count <= HARD_MAX_CONTRACTS):
        raise OrderRejected(f"bad size {count!r}")
    if not (0.01 <= price <= 0.99) or abs(price * 100 - round(price * 100)) > 1e-6:
        raise OrderRejected(f"bad price {price!r}")


def order_fill_count(order: dict, placed: int) -> int:
    """Contracts filled so far, from a Kalshi order record."""
    for k in ("fill_count", "fill_count_fp"):
        if order.get(k) not in (None, ""):
            return int(float(order[k]))
    for k in ("remaining_count", "remaining_count_fp"):
        if order.get(k) not in (None, ""):
            return max(0, placed - int(float(order[k])))
    return 0
