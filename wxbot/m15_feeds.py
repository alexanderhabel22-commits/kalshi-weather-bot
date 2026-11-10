"""Fast feeds for the 15-minute bot (websockets, each in its own thread).

  KalshiFeed  Kalshi's websocket: best bid/ask ("ticker"), every trade ("trade") on our
              markets, and our own fills ("fill"). ~0.1 s instead of polling every 3 s.
  SpotFeed    Coinbase's public websocket ticker for BTC/ETH/... spot. The jump guard:
              when spot moves far more than usual within a few seconds, our resting
              favourite bids are about to be stale, so the bot pulls them (cooldown).

Both are accelerators only: the REST calls in m15.py stay the source of truth, so if a
feed drops (or Kalshi changes a message format) the bot simply runs at polling speed.
Every message is parsed defensively and anything unexpected is ignored.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import defaultdict, deque

log = logging.getLogger("wxbot")

KALSHI_WS = "wss://api.elections.kalshi.com/trade-api/ws/v2"
COINBASE_WS = "wss://ws-feed.exchange.coinbase.com"


def _dollars(msg: dict, key: str):
    v = msg.get(f"{key}_dollars")
    if v not in (None, ""):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    v = msg.get(key)
    if v in (None, ""):
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v / 100.0 if v > 1 else v


def _count(msg: dict):
    for k in ("count_fp", "count"):
        if msg.get(k) not in (None, ""):
            try:
                return float(msg[k])
            except (TypeError, ValueError):
                return None
    return None


def _ts(msg: dict) -> float:
    v = msg.get("ts")
    try:
        v = float(v)
        return v / 1000.0 if v > 1e11 else v
    except (TypeError, ValueError):
        return time.time()


class _Thread:
    def __init__(self, name: str, wake: threading.Event | None):
        self.wake = wake
        self.last_msg = 0.0
        self.stop = False
        self.ws = None
        self.thread = threading.Thread(target=self._loop, name=name, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def healthy(self, max_age: float = 15.0) -> bool:
        return time.time() - self.last_msg < max_age

    def _loop(self):
        delay = 1.0
        while not self.stop:
            try:
                self._session()
                delay = 1.0
            except Exception as exc:
                log.warning("%s feed dropped: %s", self.thread.name, exc)
            self.ws = None
            time.sleep(delay)
            delay = min(delay * 2, 60)

    def _send(self, obj):
        ws = self.ws
        if ws is not None:
            ws.send(json.dumps(obj))


class KalshiFeed(_Thread):
    def __init__(self, client, wake: threading.Event | None = None):
        super().__init__("kalshi-ws", wake)
        self.client = client
        self.lock = threading.Lock()
        self.tickers: set[str] = set()
        self.subscribed: set[str] = set()
        self.quotes: dict[str, dict] = {}            # ticker -> {"yes_bid","yes_ask","at"}
        self.trades: dict[str, list] = defaultdict(list)   # ticker -> [(ts, yes$, n, taker)]
        self.fills: dict[str, float] = defaultdict(float)  # order_id -> contracts filled
        self.seen_fills: set = set()                 # trade ids already counted (no double count
                                                     # if the socket re-sends a fill after a reconnect)
        self._id = 0

    def watch(self, tickers) -> None:
        with self.lock:
            new = set(tickers) - self.tickers
            self.tickers |= new
        if new and self.ws is not None:
            self._subscribe(sorted(new))

    def forget(self, ticker: str) -> None:
        with self.lock:
            self.tickers.discard(ticker)
            self.quotes.pop(ticker, None)
            self.trades.pop(ticker, None)

    def _subscribe(self, tickers):
        for i in range(0, len(tickers), 50):
            self._id += 1
            self._send({"id": self._id, "cmd": "subscribe",
                        "params": {"channels": ["ticker", "trade"],
                                   "market_tickers": tickers[i:i + 50]}})
        self.subscribed |= set(tickers)

    def _session(self):
        from websockets.sync.client import connect
        hdr = self.client.headers("GET", KALSHI_WS)
        with connect(KALSHI_WS, additional_headers=hdr, open_timeout=15,
                     ping_interval=10, ping_timeout=20, max_size=2 ** 22) as ws:
            self.ws = ws
            self.subscribed = set()
            self._id += 1
            self._send({"id": self._id, "cmd": "subscribe", "params": {"channels": ["fill"]}})
            with self.lock:
                want = sorted(self.tickers)
            if want:
                self._subscribe(want)
            log.warning("kalshi feed connected (%d markets)", len(want))
            for raw in ws:
                self.handle(raw)

    def handle(self, raw) -> None:
        try:
            js = json.loads(raw)
        except (TypeError, ValueError):
            return
        typ, msg = js.get("type"), js.get("msg") or {}
        if not isinstance(msg, dict):
            return
        self.last_msg = time.time()
        tk = msg.get("market_ticker") or msg.get("ticker")
        hit = False
        if typ == "ticker" and tk in self.tickers:
            q = {"at": time.time()}
            for k in ("yes_bid", "yes_ask"):
                v = _dollars(msg, k)
                if v is not None:
                    q[k] = v
            if len(q) > 1:
                with self.lock:
                    self.quotes.setdefault(tk, {}).update(q)
                hit = True
        elif typ == "trade" and tk in self.tickers:
            yp, n = _dollars(msg, "yes_price"), _count(msg)
            side = (msg.get("taker_side") or "").lower()
            if yp is not None and n and side in ("yes", "no"):
                with self.lock:
                    self.trades[tk].append((_ts(msg), yp, n, side))
                hit = True
        elif typ == "fill":
            oid, n = msg.get("order_id"), _count(msg)
            tid = msg.get("trade_id") or msg.get("fill_id")
            if oid and n:
                with self.lock:
                    if tid and (oid, tid) in self.seen_fills:
                        return
                    if tid:
                        self.seen_fills.add((oid, tid))
                    self.fills[oid] += n
                hit = True
        elif typ == "error":
            log.warning("kalshi feed error: %s", msg)
        if hit and self.wake is not None:
            self.wake.set()

    def quote(self, ticker: str, max_age: float = 10.0) -> dict | None:
        with self.lock:
            q = self.quotes.get(ticker)
            return dict(q) if q and time.time() - q["at"] < max_age else None

    def tape(self, ticker: str, since: float) -> list:
        with self.lock:
            return [t for t in self.trades.get(ticker, ()) if t[0] > since]


class SpotFeed(_Thread):
    """Coinbase spot ticker + the jump guard."""

    def __init__(self, products, wake: threading.Event | None = None, window_s: float = 10.0,
                 z: float = 4.0, min_bps: float = 4.0, cooldown_s: float = 20.0):
        super().__init__("spot-ws", wake)
        self.products = sorted(set(products))
        self.window_s, self.z, self.min_bps, self.cooldown_s = window_s, z, min_bps, cooldown_s
        self.lock = threading.Lock()
        self.px: dict[str, deque] = defaultdict(lambda: deque(maxlen=4000))   # (t, price)
        self.moves: dict[str, deque] = defaultdict(lambda: deque(maxlen=1800))  # 1/s |move| bps
        self.last_sample: dict[str, float] = {}
        self.tripped: dict[str, float] = {}          # product -> trip time
        self.trips = 0

    def _session(self):
        from websockets.sync.client import connect
        with connect(COINBASE_WS, open_timeout=15, ping_interval=20, ping_timeout=20) as ws:
            self.ws = ws
            for p in self.products:      # one by one: an unknown product only fails itself
                self._send({"type": "subscribe", "product_ids": [p], "channels": ["ticker"]})
            log.warning("spot feed connected (%s)", ", ".join(self.products))
            for raw in ws:
                self.handle(raw)

    def handle(self, raw, now: float | None = None) -> None:
        try:
            js = json.loads(raw)
        except (TypeError, ValueError):
            return
        if js.get("type") != "ticker":
            return
        p, prod = js.get("price"), js.get("product_id")
        try:
            p = float(p)
        except (TypeError, ValueError):
            return
        if prod not in self.products or p <= 0:
            return
        self.on_price(prod, p, now if now is not None else time.time())

    def on_price(self, prod: str, p: float, now: float) -> None:
        self.last_msg = time.time()
        with self.lock:
            d = self.px[prod]
            d.append((now, p))
            while d and now - d[0][0] > 120:
                d.popleft()
            mv = self._move(d, now)
            if mv is None:
                return
            if now - self.last_sample.get(prod, 0) >= 1.0:
                self.last_sample[prod] = now
                self.moves[prod].append(abs(mv))
            hist = self.moves[prod]
            if len(hist) < 60:
                return
            rms = math.sqrt(sum(x * x for x in hist) / len(hist))
            limit = max(self.min_bps, self.z * rms)
            if abs(mv) >= limit and now - self.tripped.get(prod, 0) > 1.0:
                fresh = now - self.tripped.get(prod, 0) > self.cooldown_s
                self.tripped[prod] = now
                if fresh:
                    self.trips += 1
                    log.info("jump guard %s: %+.1f bps in %.0fs (limit %.1f)",
                             prod, mv, self.window_s, limit)
                    if self.wake is not None:
                        self.wake.set()

    def _move(self, d: deque, now: float):
        """Signed move in bps from the price ~window_s ago to now."""
        if len(d) < 2 or now - d[0][0] < self.window_s * 0.5:
            return None
        ref = d[0][1]
        for t, p in d:
            if now - t <= self.window_s:
                break
            ref = p
        return (d[-1][1] / ref - 1) * 1e4

    def move_bps(self, prod: str, now: float | None = None):
        now = now if now is not None else time.time()
        with self.lock:
            d = self.px.get(prod)
            return self._move(d, now) if d else None

    def blocked(self, prod: str, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        with self.lock:
            return now - self.tripped.get(prod, 0) < self.cooldown_s


OANDA_HOSTS = {"practice": ("https://api-fxpractice.oanda.com", "https://stream-fxpractice.oanda.com"),
               "live": ("https://api-fxtrade.oanda.com", "https://stream-fxtrade.oanda.com")}


class OandaFeed(SpotFeed):
    """Metals/oil prices from OANDA's free practice-account pricing stream (Nov 10, Alex):
    Coinbase only covers crypto, so this gives silver/copper/... the same jump guard.
    Only instruments the account can see are subscribed; the rest are logged and skipped."""

    def __init__(self, token: str, products, *args, env: str = "practice", **kw):
        super().__init__(products, *args, **kw)
        self.thread.name = "oanda"
        self.token = token
        self.api, self.stream = OANDA_HOSTS.get(env, OANDA_HOSTS["practice"])
        self.live_products: list[str] = []

    def _session(self):
        import requests
        h = {"Authorization": f"Bearer {self.token}"}
        accts = requests.get(f"{self.api}/v3/accounts", headers=h, timeout=15)
        accts.raise_for_status()
        aid = accts.json()["accounts"][0]["id"]
        ins = requests.get(f"{self.api}/v3/accounts/{aid}/instruments", headers=h, timeout=15)
        ins.raise_for_status()
        have = {i.get("name") for i in ins.json().get("instruments", [])}
        self.live_products = [p for p in self.products if p in have]
        missing = [p for p in self.products if p not in have]
        if missing:
            log.warning("oanda: not available on this account: %s", ", ".join(missing))
        # Nov 10: US OANDA accounts list no metals/oil at all. A product we can't get must
        # count as unguarded (Spots skips it), never as "feed down", which blocks live bets.
        self.products = list(self.live_products)
        if not self.live_products:
            log.warning("oanda: nothing to watch, feed off")
            self.stop = True
            return
        with requests.get(f"{self.stream}/v3/accounts/{aid}/pricing/stream", headers=h,
                          params={"instruments": ",".join(self.live_products)},
                          stream=True, timeout=(15, 30)) as r:
            r.raise_for_status()
            log.warning("oanda feed connected (%s)", ", ".join(self.live_products))
            for line in r.iter_lines():
                if self.stop:
                    return
                if line:
                    self.handle(line)

    def handle(self, raw, now: float | None = None) -> None:
        try:
            js = json.loads(raw)
        except (TypeError, ValueError):
            return
        if js.get("type") == "HEARTBEAT":
            self.last_msg = time.time()
            return
        if js.get("type") != "PRICE":
            return
        prod = js.get("instrument")
        try:
            p = (float(js["bids"][0]["price"]) + float(js["asks"][0]["price"])) / 2
        except (KeyError, IndexError, TypeError, ValueError):
            return
        if prod not in self.products or p <= 0:
            return
        self.on_price(prod, p, now if now is not None else time.time())


class Spots:
    """One face for several price feeds. A product with no feed is treated as unguarded
    (healthy, never blocked), so a missing OANDA token never stops BTC or the metals.
    `links`: groups of products that trip together (silver jumps -> copper bids pulled too)."""

    def __init__(self, feeds, links=()):
        self.feeds = [f for f in feeds if f is not None]
        self.links = [set(g) for g in links]

    def _feed(self, prod):
        for f in self.feeds:
            if prod in f.products:
                return f
        return None

    def healthy(self, prod: str | None = None) -> bool:
        if prod is None:
            return all(f.healthy() for f in self.feeds if f.products)
        f = self._feed(prod)
        return f is None or f.healthy()

    def move_bps(self, prod: str, now: float | None = None):
        f = self._feed(prod)
        return f.move_bps(prod, now) if f else None

    def blocked(self, prod: str, now: float | None = None) -> bool:
        group = {prod}
        for g in self.links:
            if prod in g:
                group |= g
        for p in group:
            f = self._feed(p)
            if f is not None and f.blocked(p, now):
                return True
        return False

    @property
    def trips(self) -> int:
        return sum(f.trips for f in self.feeds)
