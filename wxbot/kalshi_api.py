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
# Liquidity-reward test (wxbot/lip.py) only: big but cheap resting bids. Each order <= 500
# contracts, <= 10c, and <= $6 in total, so one order can never risk more than $6.
LIP_MAX_CONTRACTS = 500
LIP_MAX_PRICE = 0.10
LIP_MAX_ORDER_COST = 6.0


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

    def shard_balances(self) -> dict:
        """{exchange_index: available $} from /portfolio/balance balance_breakdown (exchange sharding)."""
        js = self._get("/portfolio/balance")
        out = {}
        for b in js.get("balance_breakdown") or []:
            try:
                out[int(b["exchange_index"])] = float(b["balance"])
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def cash_on(self, shard: int = 0) -> float:
        """Spendable cash on one exchange shard. Kalshi checks collateral per shard, so
        a bot can only use the cash on the shard its markets live on. Falls back to the
        account balance when Kalshi sends no per-shard breakdown."""
        js = self._get("/portfolio/balance")
        for b in js.get("balance_breakdown") or []:
            try:
                if int(b["exchange_index"]) == int(shard):
                    return float(b["balance"])
            except (KeyError, TypeError, ValueError):
                continue
        return float(js.get("balance", 0)) / 100.0

    def shard_transfer(self, dollars: float, src: int, dst: int) -> dict:
        """Move our own cash between exchange shards of this account (not a withdrawal).
        Kalshi takes the amount in centicents ($1 = 10,000)."""
        amt = int(round(dollars * 100)) * 100
        if amt <= 0 or dollars > 1000:
            raise OrderRejected(f"bad transfer {dollars!r}")
        return self._post("/portfolio/intra_exchange_instance_transfer", {
            "source": "event_contract", "destination": "event_contract", "amount": amt,
            "source_exchange_shard": int(src), "destination_exchange_shard": int(dst)})

    def fills(self, ticker: str | None = None, min_ts: int | None = None,
              max_pages: int = 50) -> list:
        """Every fill on the account (optionally one market / since min_ts), all pages."""
        out, cursor = [], None
        for _ in range(max_pages):
            params = {"limit": 200}
            if ticker:
                params["ticker"] = ticker
            if min_ts:
                params["min_ts"] = int(min_ts)
            if cursor:
                params["cursor"] = cursor
            js = self._get("/portfolio/fills", params)
            out.extend(js.get("fills") or [])
            cursor = js.get("cursor")
            if not cursor:
                return out
        raise HttpError(0, BASE + "/portfolio/fills", "fills listing did not finish")

    def position(self, ticker: str) -> float:
        """Contracts held in one market (either side), from Kalshi's position record."""
        js = self._get("/portfolio/positions", {"ticker": ticker, "limit": 10})
        for p in js.get("market_positions") or []:
            if (p.get("ticker") or p.get("market_ticker")) == ticker:
                for k in ("position_fp", "position"):
                    if p.get(k) not in (None, ""):
                        return abs(round(float(p[k]), 2))
        return 0.0

    def get_order(self, order_id: str) -> dict:
        return self._get(f"/portfolio/orders/{order_id}").get("order") or {}

    def resting_orders(self, max_pages: int = 50) -> dict:
        """{order_id: order} for every resting order, from one paginated listing
        (instead of one request per order). Raises if the listing doesn't finish,
        so the caller falls back to checking orders one by one."""
        out, cursor = {}, None
        for _ in range(max_pages):
            params = {"status": "resting", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            js = self._get("/portfolio/orders", params)
            for o in js.get("orders") or []:
                if o.get("order_id"):
                    out[o["order_id"]] = o
            cursor = js.get("cursor")
            if not cursor:
                return out
        raise HttpError(0, BASE + "/portfolio/orders", "resting order listing did not finish")

    def queue_positions(self, tickers: list) -> dict:
        """Contracts ahead of each of our resting orders on these markets (read-only).
        Kalshi: GET /portfolio/orders/queue_positions?market_tickers=A,B."""
        return self._get("/portfolio/orders/queue_positions",
                         {"market_tickers": ",".join(tickers)})

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
                  reduce_only: bool = False, lip: bool = False) -> dict:
        if lip:
            check_lip_order(side, price, count)
        else:
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
        try:
            js = self._post("/portfolio/events/orders", payload)
        except HttpError as exc:
            # The HTTP layer retries a POST after a timeout or 5xx. If the first try
            # actually reached Kalshi, the retry gets 409 order_already_exists and the
            # order would rest untracked (Nov 8: an orphan copper bid filled a 3rd
            # contract). Adopt the existing order instead of reporting a reject.
            if exc.status == 409 and "already_exists" in (exc.body or ""):
                found = self.find_order(ticker, client_order_id)
                if found:
                    return found
            raise
        return js.get("order") or js

    def find_order(self, ticker: str, client_order_id: str) -> dict:
        """Our order on `ticker` with this client_order_id, in any status ({} if none)."""
        cursor = None
        for _ in range(5):
            params = {"ticker": ticker, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            js = self._get("/portfolio/orders", params)
            for o in js.get("orders") or []:
                if o.get("client_order_id") == client_order_id:
                    return o
            cursor = js.get("cursor")
            if not cursor:
                break
        return {}

    def place_limit(self, ticker: str, side: str, price: float, count: int,
                    client_order_id: str) -> dict:
        """Resting BUY limit order (post-only: never crosses the spread)."""
        return self._order_v2(ticker, side, True, price, count, client_order_id,
                              "good_till_canceled", post_only=True)

    def place_lip_bid(self, ticker: str, side: str, price: float, count: int,
                      client_order_id: str) -> dict:
        """Liquidity-reward test only: a resting post-only BUY with the LIP ceilings
        (cheap and capped in dollars instead of the 25-contract ceiling)."""
        return self._order_v2(ticker, side, True, price, count, client_order_id,
                              "good_till_canceled", post_only=True, lip=True)

    def place_lip_exit(self, ticker: str, side: str, price: float, count: int,
                       client_order_id: str, now: bool = False) -> dict:
        """Liquidity-reward test only: offer back contracts a reward bid bought by accident.
        reduce_only (it can only shrink what we hold), LIP ceilings; resting post-only, or
        immediate-or-cancel when the bid is already at our price (`now`)."""
        return self._order_v2(ticker, side, False, price, count, client_order_id,
                              "immediate_or_cancel" if now else "good_till_canceled",
                              post_only=not now, reduce_only=True, lip=True)

    def credit_history(self, max_pages: int = 5) -> list:
        """Credits Kalshi applied to the account, newest first (GET /portfolio/credit_history;
        where incentive payouts are expected to show up)."""
        out, cursor = [], None
        for _ in range(max_pages):
            params = {"limit": 200}
            if cursor:
                params["cursor"] = cursor
            js = self._get("/portfolio/credit_history", params)
            rows = js.get("credits") or js.get("credit_history")
            if rows is None:            # field name not documented: take the first list
                rows = next((v for v in js.values() if isinstance(v, list)), [])
            out.extend(rows)
            cursor = js.get("cursor") or js.get("next_cursor")
            if not cursor:
                break
        return out

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


def check_lip_order(side: str, price: float, count: int) -> None:
    check_order(side, price, 1)
    if not isinstance(count, int) or not (1 <= count <= LIP_MAX_CONTRACTS):
        raise OrderRejected(f"bad lip size {count!r}")
    if price > LIP_MAX_PRICE + 1e-9 or price * count > LIP_MAX_ORDER_COST + 1e-9:
        raise OrderRejected(f"lip order too big: {count} @ {price}")


def fill_qty(order: dict, placed: float) -> float:
    """Contracts filled so far, keeping fractions: Kalshi fills can be partial contracts
    (Nov 9: a silver window ended with 2.5). int() truncation read 0.5 as 0, and the 15-min
    bot then re-bid for a contract it already partly held."""
    for k in ("fill_count_fp", "fill_count"):
        if order.get(k) not in (None, ""):
            return round(float(order[k]), 2)
    for k in ("remaining_count_fp", "remaining_count"):
        if order.get(k) not in (None, ""):
            return round(max(0.0, placed - float(order[k])), 2)
    return 0.0


def order_fill_count(order: dict, placed: int) -> int:
    """Contracts filled so far, from a Kalshi order record."""
    for k in ("fill_count", "fill_count_fp"):
        if order.get(k) not in (None, ""):
            return int(float(order[k]))
    for k in ("remaining_count", "remaining_count_fp"):
        if order.get(k) not in (None, ""):
            return max(0, placed - int(float(order[k])))
    return 0


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fill_totals(fills: list) -> dict:
    """{ticker: [contracts, cost $, fee $]} from Kalshi fill records. Each fill names the side
    it acquired and that side's price (same reading as wxbot/taxledger.py, which matched all
    730 settlements on Nov 10). Only for bots that never sell (the 15-min bot)."""
    out: dict = {}
    for f in fills:
        tk = f.get("ticker") or f.get("market_ticker")
        side = str(f.get("side") or "").lower()
        n = _num(f.get("count_fp"))
        if n is None:
            n = _num(f.get("count"))
        if not tk or side not in ("yes", "no") or not n:
            continue
        price = _num(f.get(f"{side}_price_dollars"))
        if price is None:
            c = _num(f.get(f"{side}_price"))
            other = _num(f.get(f"{'no' if side == 'yes' else 'yes'}_price_dollars"))
            price = c / 100.0 if c is not None else (1.0 - other if other is not None else None)
        if price is None:
            continue
        fee = _num(f.get("fee_cost"))
        if fee is None:
            fc = _num(f.get("fee"))
            fee = fc / 100.0 if fc is not None else 0.0
        t = out.setdefault(tk, [0.0, 0.0, 0.0])
        t[0] += n
        t[1] += n * price
        t[2] += fee
    return {k: [round(v[0], 2), round(v[1], 4), round(v[2], 4)] for k, v in out.items()}
