"""Shadow fair value for the 15-minute crypto markets (Nov 10, 2026; Alex: make the bots "smart").

SHADOW ONLY: nothing here places, changes or cancels an order. It logs what a price-aware bot
would have thought, so we can test (on real data, second by second) whether it would have:
skipped our bad fills, dropped losing second contracts, or found cheap buys.

Why: a 1-minute Coinbase model could not beat Kalshi's own price (Brier 0.061 vs 0.036 on BTC),
and Coinbase alone matches the settlement only ~95% of the time. Kalshi settles crypto 15-min
markets on the CF Benchmarks real-time index: the 60-second average before close vs the
60-second average before open (= the market's floor_strike). The official index is licensed,
so we rebuild a close copy from free public feeds of exchanges in the index (Coinbase, Kraken,
Bitstamp): the median of their live mid/last prices, sampled once a second.

  Composite     websocket feeds -> per-second index per coin (ring buffer, ~30 min)
  fair_yes()    P(settlement average >= strike) from the index, its 1-second volatility,
                time left, and (inside the last 60 s) the part of the average already fixed
  FairShadow    every few seconds logs one row per open crypto market in its last 15 min,
                plus a row the moment one of our fills shows up; hourly CSV files
                fv-YYYY-MM-DD-HH.csv, each uploaded to m15-data once after its hour ends
"""
from __future__ import annotations

import csv
import json
import logging
import math
import statistics
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from .m15_feeds import _Thread

log = logging.getLogger("wxbot")

AVG_S = 60                  # settlement = average of the index over the last 60 s
FV_COLS = ["ts", "event", "ticker", "series", "left_s", "strike", "index", "venues", "sigma_bps_s",
           "fv_yes", "yes_bid", "yes_ask", "our_side", "our_q", "filled", "nth_fill"]

VENUES = {
    # venue: (url, symbol format per coin)
    "coinbase": ("wss://ws-feed.exchange.coinbase.com", "{c}-USD"),
    "kraken": ("wss://ws.kraken.com/v2", "{c}/USD"),
    "bitstamp": ("wss://ws.bitstamp.net", "{lc}usd"),
}


def _f(x):
    try:
        x = float(x)
        return x if x > 0 and math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def parse_venue(venue: str, raw) -> list[tuple[str, float]]:
    """(symbol, price) pairs from one websocket message; [] for anything unexpected.
    Uses the bid/ask mid where the venue sends one (the index is built from order books)."""
    try:
        js = json.loads(raw)
    except (TypeError, ValueError):
        return []
    out = []
    try:
        if venue == "coinbase" and js.get("type") == "ticker":
            b, a = _f(js.get("best_bid")), _f(js.get("best_ask"))
            p = (b + a) / 2 if b and a else _f(js.get("price"))
            if p:
                out.append((js.get("product_id"), p))
        elif venue == "kraken" and js.get("channel") == "ticker":
            for d in js.get("data") or []:
                b, a = _f(d.get("bid")), _f(d.get("ask"))
                p = (b + a) / 2 if b and a else _f(d.get("last"))
                if p:
                    out.append((d.get("symbol"), p))
        elif venue == "bitstamp" and js.get("event") == "trade":
            ch = js.get("channel") or ""
            p = _f((js.get("data") or {}).get("price"))
            if p and ch.startswith("live_trades_"):
                out.append((ch[len("live_trades_"):], p))
    except (AttributeError, TypeError):
        return []
    return out


class VenueFeed(_Thread):
    def __init__(self, venue: str, coins, sink):
        super().__init__(f"fv-{venue}", None)
        self.venue = venue
        self.url, fmt = VENUES[venue]
        self.sym = {fmt.format(c=c, lc=c.lower()): c for c in coins}
        self.sink = sink

    def _session(self):
        from websockets.sync.client import connect
        with connect(self.url, open_timeout=15, ping_interval=20, ping_timeout=20) as ws:
            self.ws = ws
            syms = sorted(self.sym)
            if self.venue == "coinbase":
                for s in syms:
                    self._send({"type": "subscribe", "product_ids": [s], "channels": ["ticker"]})
            elif self.venue == "kraken":
                self._send({"method": "subscribe", "params": {"channel": "ticker", "symbol": syms}})
            elif self.venue == "bitstamp":
                for s in syms:
                    self._send({"event": "bts:subscribe", "data": {"channel": f"live_trades_{s}"}})
            log.warning("fair-value feed %s connected (%s)", self.venue, ", ".join(syms))
            for raw in ws:
                for s, p in parse_venue(self.venue, raw):
                    c = self.sym.get(s)
                    if c:
                        self.last_msg = time.time()
                        self.sink(self.venue, c, p, time.time())


class Composite:
    """Latest price per (venue, coin) -> a once-a-second median index per coin."""

    def __init__(self, coins, fresh_s: float = 10.0, keep_s: int = 1800):
        self.coins = sorted(set(coins))
        self.fresh_s = fresh_s
        self.lock = threading.Lock()
        self.last: dict[tuple[str, str], tuple[float, float]] = {}
        self.series: dict[str, deque] = {c: deque(maxlen=keep_s) for c in self.coins}  # (sec, px, n)
        self.feeds: list = []

    def start(self, venues=("coinbase", "kraken", "bitstamp")):
        self.feeds = [VenueFeed(v, self.coins, self.on_price).start() for v in venues]
        return self

    def on_price(self, venue: str, coin: str, p: float, now: float) -> None:
        with self.lock:
            self.last[(venue, coin)] = (now, p)

    def now_px(self, coin: str, now: float) -> tuple[float | None, int]:
        with self.lock:
            px = [p for (v, c), (t, p) in self.last.items() if c == coin and now - t <= self.fresh_s]
        if not px:
            return None, 0
        return statistics.median(px), len(px)

    def sample(self, now: float) -> None:
        """Append one point per whole second (call often; extra calls are no-ops)."""
        sec = int(now)
        for c in self.coins:
            d = self.series[c]
            if d and d[-1][0] >= sec:
                continue
            p, n = self.now_px(c, now)
            if p is not None:
                d.append((sec, p, n))

    def window(self, coin: str, lo: int, hi: int) -> list[float]:
        return [p for s, p, _ in self.series.get(coin, ()) if lo <= s < hi]

    def sigma(self, coin: str, now: float, look_s: int = 900, floor: float = 2e-5) -> float | None:
        """Per-second log volatility over the last look_s seconds (gaps spread evenly)."""
        pts = [(s, p) for s, p, _ in self.series.get(coin, ()) if s >= now - look_s]
        if len(pts) < 60:
            return None
        var = sum(math.log(b / a) ** 2 for (_, a), (_, b) in zip(pts, pts[1:]))
        span = pts[-1][0] - pts[0][0]
        return max(math.sqrt(var / span), floor) if span > 0 else None

    def healthy(self, coin: str, now: float) -> bool:
        d = self.series.get(coin)
        return bool(d) and now - d[-1][0] <= 5


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def fair_yes(px: float, strike: float, sigma: float, left: float, fixed: list[float]) -> float:
    """P(average of the index over the last AVG_S seconds >= strike).
    px: index now; sigma: per-second log vol; left: seconds to close; fixed: index points
    already inside the averaging window (empty when left > AVG_S). Driftless random walk;
    the future part of a 60 s average of a random walk has variance sigma^2 * r^3 / 3 / 60^2
    (r = seconds of it still to come), plus sigma^2 * (left - 60) of travel before it starts."""
    left = max(float(left), 0.0)
    if left > AVG_S:
        mean = px
        var_t = (left - AVG_S) + AVG_S / 3.0
        sd = px * sigma * math.sqrt(var_t)
    else:
        r = left
        k = AVG_S - r
        a = (sum(fixed) / len(fixed)) if fixed else px
        mean = (k * a + r * px) / AVG_S
        sd = px * sigma * math.sqrt(r ** 3 / 3.0) / AVG_S
    if sd <= 1e-12:
        return 1.0 if mean >= strike else 0.0
    return _phi((mean - strike) / sd)


class FairShadow:
    """Log-only fair-value shadow. Attach with bot.fv = FairShadow(...)."""

    def __init__(self, composite: Composite, coins_by_series: dict, out_dir: Path,
                 every_s: float = 5.0, max_left_s: int = 900):
        self.comp = composite
        self.coin = dict(coins_by_series)
        self.dir = Path(out_dir)
        self.every_s = every_s
        self.max_left_s = max_left_s
        self.last_tick: dict[str, float] = {}
        self.uploaded: set[str] = set()

    def fair_value(self, m: dict, now: float) -> dict | None:
        """{"fv_yes", "index", "venues", "sigma"} for market m right now, or None."""
        coin = self.coin.get(m.get("series"))
        k = _f(m.get("strike"))
        if not coin or not k or not self.comp.healthy(coin, now):
            return None
        px, n = self.comp.now_px(coin, now)
        sig = self.comp.sigma(coin, now)
        if px is None or sig is None:
            return None
        left = m["close"] - now
        fixed = self.comp.window(coin, int(m["close"]) - AVG_S, int(now) + 1) if left <= AVG_S else []
        return {"fv_yes": fair_yes(px, k, sig, left, fixed), "index": px, "venues": n, "sigma": sig}

    def _row(self, m: dict, now: float, event: str, fv: dict, quote: dict | None) -> dict:
        o = m.get("order") or {}
        q = quote or {}
        return {"ts": round(now, 1), "event": event, "ticker": m["ticker"], "series": m["series"],
                "left_s": round(m["close"] - now, 1), "strike": m.get("strike"),
                "index": round(fv["index"], 6), "venues": fv["venues"],
                "sigma_bps_s": round(fv["sigma"] * 1e4, 3), "fv_yes": round(fv["fv_yes"], 4),
                "yes_bid": q.get("yes_bid", ""), "yes_ask": q.get("yes_ask", ""),
                "our_side": o.get("side") or m.get("fav_side") or "", "our_q": o.get("q", ""),
                "filled": m.get("filled", 0), "nth_fill": m.get("fv_fills", 0)}

    def _write(self, rows: list, now: float) -> None:
        if not rows:
            return
        path = self.dir / f"fv-{datetime.fromtimestamp(now, timezone.utc):%Y-%m-%d-%H}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FV_COLS)
            if new:
                w.writeheader()
            w.writerows(rows)

    def step(self, bot, now: float) -> None:
        self.comp.sample(now)
        rows = []
        for m in bot.state["markets"].values():
            if m.get("series") not in self.coin or not (0 < m["close"] - now <= self.max_left_s):
                continue
            if not _f(m.get("strike")):
                self._fetch_strike(bot, m, now)
            filled = float(m.get("filled") or 0)
            new_fill = filled > float(m.get("fv_seen", 0)) + 1e-9
            tick = now - self.last_tick.get(m["ticker"], 0) >= self.every_s
            if not (new_fill or tick):
                continue
            fv = self.fair_value(m, now)
            if fv is None:
                continue
            quote = bot.quote(m["ticker"], now) if hasattr(bot, "quote") else None
            if new_fill:
                m["fv_seen"] = filled
                m["fv_fills"] = int(m.get("fv_fills", 0)) + 1
                fav = m.get("fav_side") or (m.get("order") or {}).get("side")
                if m["fv_fills"] == 1:      # value of our side at the first fill (markets CSV)
                    m["fv_fill"] = round(fv["fv_yes"] if fav != "no" else 1 - fv["fv_yes"], 4)
                rows.append(self._row(m, now, "fill", fv, quote))
            if tick:
                self.last_tick[m["ticker"]] = now
                rows.append(self._row(m, now, "tick", fv, quote))
        self._write(rows, now)

    def _fetch_strike(self, bot, m: dict, now: float) -> None:
        """Markets found before they open have no strike yet: look it up (at most every 30 s)."""
        if now - float(m.get("fv_strike_try", 0)) < 30 or getattr(bot, "http", None) is None:
            return
        m["fv_strike_try"] = now
        from . import kalshi
        try:
            mk = kalshi.get_market(bot.http, m["ticker"]) or {}
        except Exception as exc:
            log.debug("strike %s: %s", m["ticker"], exc)
            return
        if _f(mk.get("floor_strike")):
            m["strike"] = float(mk["floor_strike"])

    def files(self, now: float) -> dict:
        """Hourly files whose hour has ended and that weren't uploaded yet (each goes once)."""
        out = {}
        cur = f"fv-{datetime.fromtimestamp(now, timezone.utc):%Y-%m-%d-%H}.csv"
        for p in sorted(self.dir.glob("fv-*.csv"))[-6:]:
            if p.name != cur and p.name not in self.uploaded:
                out[p.name] = p.read_text(encoding="utf-8")
        return out

    def mark_uploaded(self, names) -> None:
        self.uploaded |= set(names)
