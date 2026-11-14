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
           "fv_yes", "yes_bid", "yes_ask", "our_side", "our_q", "filled", "nth_fill",
           "cf_index", "fv_cf"]
# Kalshi's own websocket streams the official CF Benchmarks real-time indexes (channel
# cfbenchmarks_value_5hz, up to 5 ticks/s; found by the platform-changes check, Nov 10).
# Logged side by side with the rebuilt median so we can measure the gap before switching.
CF_IDS = {"BRTI": "BTC", "ETHUSD_RTI": "ETH", "SOLUSD_RTI": "SOL", "XRPUSD_RTI": "XRP",
          "DOGEUSD_RTI": "DOGE"}

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


def _q(q: dict, key: str):
    """Bot.quote() returns *_dollars keys; accept the plain ones too."""
    v = q.get(f"{key}_dollars", q.get(key))
    return "" if v is None else v


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


def parse_cf(raw) -> list[tuple[str, float, float | None]]:
    """(coin, value, source_ts_s) from a Kalshi cfbenchmarks message; [] otherwise."""
    try:
        js = json.loads(raw)
    except (TypeError, ValueError):
        return []
    msg = js.get("msg") if isinstance(js, dict) else None
    if not isinstance(msg, dict) or not str(js.get("type", "")).startswith("cfbenchmarks"):
        return []
    coin = CF_IDS.get(msg.get("index_id"))
    p = _f(msg.get("value_usd"))
    if not coin or not p:
        return []
    ts = _f(msg.get("source_ts_ms"))
    return [(coin, p, ts / 1000.0 if ts else None)]


class KalshiIndexFeed(_Thread):
    """Official CF Benchmarks ticks through Kalshi's authenticated websocket (read-only)."""

    def __init__(self, client, sink):
        super().__init__("fv-cf", None)
        self.client = client
        self.sink = sink

    def _session(self):
        from websockets.sync.client import connect

        from .m15_feeds import KALSHI_WS
        hdr = self.client.headers("GET", KALSHI_WS)
        with connect(KALSHI_WS, additional_headers=hdr, open_timeout=15,
                     ping_interval=10, ping_timeout=20) as ws:
            self.ws = ws
            self._send({"id": 1, "cmd": "subscribe",
                        "params": {"channels": ["cfbenchmarks_value_5hz"], "index_ids": ["all"]}})
            log.warning("fair-value feed cf (Kalshi CF Benchmarks) connected")
            for raw in ws:
                for coin, p, _ts in parse_cf(raw):
                    self.last_msg = time.time()
                    self.sink(coin, p, time.time())
                if '"error"' in str(raw)[:200]:
                    log.warning("cf feed: %s", str(raw)[:300])


class Composite:
    """Latest price per (venue, coin) -> a once-a-second median index per coin."""

    def __init__(self, coins, fresh_s: float = 10.0, keep_s: int = 1800):
        self.coins = sorted(set(coins))
        self.fresh_s = fresh_s
        self.lock = threading.Lock()
        self.last: dict[tuple[str, str], tuple[float, float]] = {}
        self.series: dict[str, deque] = {c: deque(maxlen=keep_s) for c in self.coins}  # (sec, px, n)
        self.cf_last: dict[str, tuple[float, float]] = {}
        self.cf_series: dict[str, deque] = {c: deque(maxlen=keep_s) for c in self.coins}
        self.feeds: list = []

    def start(self, venues=("coinbase", "kraken", "bitstamp"), client=None):
        self.feeds = [VenueFeed(v, self.coins, self.on_price).start() for v in venues]
        if client is not None:
            self.feeds.append(KalshiIndexFeed(client, self.on_cf).start())
        return self

    def on_cf(self, coin: str, p: float, now: float) -> None:
        if coin in self.cf_series:
            with self.lock:
                self.cf_last[coin] = (now, p)

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
            with self.lock:
                cf = self.cf_last.get(c)
            if cf and now - cf[0] <= self.fresh_s:
                self.cf_series[c].append((sec, cf[1], 1))

    def window(self, coin: str, lo: int, hi: int, cf: bool = False) -> list[float]:
        src = self.cf_series if cf else self.series
        return [p for s, p, _ in src.get(coin, ()) if lo <= s < hi]

    def cf_px(self, coin: str, now: float) -> float | None:
        d = self.cf_series.get(coin)
        return d[-1][1] if d and now - d[-1][0] <= 5 else None

    def sigma(self, coin: str, now: float, look_s: int = 900, floor: float = 2e-5,
              cf: bool = False) -> float | None:
        """Per-second log volatility over the last look_s seconds (gaps spread evenly)."""
        src = self.cf_series if cf else self.series
        pts = [(s, p) for s, p, _ in src.get(coin, ()) if s >= now - look_s]
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
                 every_s: float = 5.0, max_left_s: int = 900, lag: bool = False):
        self.comp = composite
        self.lag = LagProbe(self, out_dir) if lag else None
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
        out = {"fv_yes": fair_yes(px, k, sig, left, fixed), "index": px, "venues": n, "sigma": sig,
               "cf_index": None, "fv_cf": None}
        cpx, csig = self.comp.cf_px(coin, now), self.comp.sigma(coin, now, cf=True)
        if cpx is not None and csig is not None:
            cfix = (self.comp.window(coin, int(m["close"]) - AVG_S, int(now) + 1, cf=True)
                    if left <= AVG_S else [])
            out["cf_index"], out["fv_cf"] = cpx, fair_yes(cpx, k, csig, left, cfix)
        return out

    def _row(self, m: dict, now: float, event: str, fv: dict, quote: dict | None) -> dict:
        o = m.get("order") or {}
        q = quote or {}
        return {"ts": round(now, 1), "event": event, "ticker": m["ticker"], "series": m["series"],
                "left_s": round(m["close"] - now, 1), "strike": m.get("strike"),
                "index": round(fv["index"], 6), "venues": fv["venues"],
                "sigma_bps_s": round(fv["sigma"] * 1e4, 3), "fv_yes": round(fv["fv_yes"], 4),
                "yes_bid": _q(q, "yes_bid"), "yes_ask": _q(q, "yes_ask"),
                "our_side": o.get("side") or m.get("fav_side") or "", "our_q": o.get("q", ""),
                "filled": m.get("filled", 0), "nth_fill": m.get("fv_fills", 0),
                "cf_index": "" if fv.get("cf_index") is None else round(fv["cf_index"], 6),
                "fv_cf": "" if fv.get("fv_cf") is None else round(fv["fv_cf"], 4)}

    def _write(self, rows: list, now: float) -> None:
        if not rows:
            return
        path = self.dir / f"fv-{datetime.fromtimestamp(now, timezone.utc):%Y-%m-%d-%H}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():                    # an older header (e.g. before cf columns): new file
            with open(path, encoding="utf-8") as f:
                if f.readline().strip().split(",") != FV_COLS:
                    path = path.with_name(path.stem + "-b.csv")
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
                    y = fv["fv_cf"] if fv.get("fv_cf") is not None else fv["fv_yes"]
                    m["fv_fill"] = round(y if fav != "no" else 1 - y, 4)
                rows.append(self._row(m, now, "fill", fv, quote))
            if tick:
                self.last_tick[m["ticker"]] = now
                rows.append(self._row(m, now, "tick", fv, quote))
        self._write(rows, now)
        if self.lag is not None:
            try:
                self.lag.step(bot, now)
            except Exception as exc:          # paper probe: never disturb the bot
                log.warning("lag probe: %s", exc)

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
        cur = f"fv-{datetime.fromtimestamp(now, timezone.utc):%Y-%m-%d-%H}"
        for pat, stem in (("fv-*.csv", cur), ("lag-*.csv", "lag-" + cur[3:])):
            for p in sorted(self.dir.glob(pat))[-6:]:
                if not p.name.startswith(stem) and p.name not in self.uploaded:
                    out[p.name] = p.read_text(encoding="utf-8")
        return out

    def mark_uploaded(self, names) -> None:
        self.uploaded |= set(names)


# ------------------------------------------------------------------ lag probe (PAPER)
LAG_COLS = ["ts", "jump_id", "stage", "ticker", "series", "left_s", "side", "fair_before", "fair",
            "ws_ask", "book_ask", "depth_ask", "depth_edge", "book_bid", "book_ms",
            "tape_ct_at_ask", "tape_ct_any"]


class LagProbe:
    """PAPER ONLY (Nov 13, 2026): checks whether Kalshi's 15-min crypto quotes really lag the
    CF index after a jump, and whether the stale price could actually be bought.

    The shadow logs showed Kalshi's mid catching up to our fair value 10-15 s after the fair
    value jumps 8+ points (BTC/ETH/SOL alike), worth ~+6c/ct after fees on paper. That used
    5-second websocket quote samples, so the stale ask may already be gone, or be 1 contract
    deep. At each jump this pulls the real order book (REST) right away and again at +5 s and
    +15 s, and counts contracts other traders bought on our side at or below that ask
    (websocket trade tape). Nothing here places an order. Rows go to lag-YYYY-MM-DD-HH.csv.
    """

    def __init__(self, shadow: "FairShadow", out_dir: Path, jump: float = 0.08,
                 look_s: float = 5.0, cooldown_s: float = 30.0, min_left_s: float = 120.0,
                 max_left_s: float = 780.0, edge: float = 0.03, stages=(5.0, 15.0)):
        self.sh = shadow
        self.dir = Path(out_dir)
        self.jump, self.look_s, self.cooldown_s = jump, look_s, cooldown_s
        self.min_left_s, self.max_left_s, self.edge = min_left_s, max_left_s, edge
        self.stages = tuple(stages)
        self.hist: dict[str, deque] = {}          # ticker -> (t, fair_yes)
        self.last_jump: dict[str, float] = {}
        self.open: list[dict] = []                # jumps waiting for their +5/+15 s looks
        self.n = 0

    def _fair(self, m, now):
        fv = self.sh.fair_value(m, now)
        if not fv:
            return None
        return fv["fv_cf"] if fv.get("fv_cf") is not None else fv["fv_yes"]

    def _book(self, bot, ticker):
        http = getattr(bot, "http", None)
        if http is None:
            return None, ""
        from . import kalshi
        t0 = time.time()
        book = kalshi.get_orderbook(http, ticker)
        return book, round((time.time() - t0) * 1000)

    @staticmethod
    def _best(book, side):
        """(best ask to BUY side, contracts at it, best bid for side) from a bids-only book."""
        if not book:
            return None, None, None
        opp = book.get("no" if side == "yes" else "yes") or []
        own = book.get(side) or []
        ask = round(1.0 - max(p for p, _ in opp), 4) if opp else None
        depth = sum(q for p, q in opp if ask is not None and abs((1.0 - p) - ask) < 1e-9) if opp else 0
        bid = max(p for p, _ in own) if own else None
        return ask, depth, bid

    def _tape(self, bot, ticker, since, side, ask):
        kf = getattr(bot, "kfeed", None)
        if kf is None or not hasattr(kf, "tape"):
            return "", ""
        at = anyc = 0.0
        for t in kf.tape(ticker, since):
            _, yp, n, tside = t[:4]
            if tside != side:
                continue
            px = yp if side == "yes" else 1.0 - yp
            anyc += n
            if ask is not None and px <= ask + 1e-9:
                at += n
        return at, anyc

    def step(self, bot, now: float) -> None:
        rows = []
        for m in list(bot.state["markets"].values()):
            if m.get("series") not in self.sh.coin:
                continue
            left = m["close"] - now
            if not (self.min_left_s <= left <= self.max_left_s):
                continue
            h = self.hist.setdefault(m["ticker"], deque(maxlen=64))
            if h and now - h[-1][0] < 1.0:
                continue
            y = self._fair(m, now)
            if y is None:
                continue
            h.append((now, y))
            old = [v for t, v in h if now - t >= self.look_s]
            if not old or now - self.last_jump.get(m["ticker"], -1e9) < self.cooldown_s:
                continue
            y0 = old[-1]
            dy = y - y0
            if abs(dy) < self.jump or not (0.15 < y < 0.85):
                continue
            side = "yes" if dy > 0 else "no"
            fair = y if side == "yes" else 1.0 - y
            q = bot.quote(m["ticker"], now) if hasattr(bot, "quote") else None
            ws_ask = None
            if q:
                ws_ask = _q(q, "yes_ask") if side == "yes" else (
                    round(1.0 - float(_q(q, "yes_bid")), 4) if _q(q, "yes_bid") not in ("", None) else None)
            book, ms = self._book(bot, m["ticker"])
            ask, depth, bid = self._best(book, side)
            edge = ""
            if book:
                from . import kalshi
                edge = kalshi.depth_to_buy(book, side, fair - self.edge)
            self.n += 1
            jid = f"{m['ticker']}@{int(now)}"
            self.last_jump[m["ticker"]] = now
            ev = {"jid": jid, "m": m, "side": side, "t0": now, "ask": ask, "y0": y0,
                  "todo": list(self.stages)}
            self.open.append(ev)
            rows.append(self._row(now, ev, "jump", y, ws_ask, ask, depth, edge, bid, ms, "", ""))
        for ev in list(self.open):
            if not ev["todo"] or now - ev["t0"] < ev["todo"][0]:
                continue
            stage = ev["todo"].pop(0)
            m, side = ev["m"], ev["side"]
            y = self._fair(m, now)
            book, ms = self._book(bot, m["ticker"])
            ask, depth, bid = self._best(book, side)
            at, anyc = self._tape(bot, m["ticker"], ev["t0"], side, ev["ask"])
            rows.append(self._row(now, ev, f"+{int(stage)}s", y, "", ask, depth, "", bid, ms, at, anyc))
            if not ev["todo"]:
                self.open.remove(ev)
        self._write(rows, now)

    def place_check(self, bot, m: dict, side: str, q: float, n: int, now: float) -> None:
        """Right before a LIVE bid goes in: the real REST book next to the cached quote the
        bot priced from. stage=place; ws_ask = the cached best bid for our side (what we're
        joining), book_bid / book_ask = the real ones, depth_ask = contracts already bid at
        our price q, depth_edge = our price q, fair = our side's fair value."""
        y = self._fair(m, now) if self.sh.coin.get(m.get("series")) else None
        cq = bot.quote(m["ticker"], now) if hasattr(bot, "quote") else None
        cached = None
        if cq and _q(cq, "yes_bid") not in ("", None):
            cached = float(_q(cq, "yes_bid")) if side == "yes" else (
                round(1.0 - float(_q(cq, "yes_ask")), 4) if _q(cq, "yes_ask") not in ("", None) else None)
        book, ms = self._book(bot, m["ticker"])
        ask, _, bid = self._best(book, side)
        at_q = sum(c for p, c in (book or {}).get(side, []) if abs(p - q) < 1e-9) if book else ""
        ev = {"jid": f"{m['ticker']}@{int(now)}", "m": m, "side": side, "y0": None}
        self._write([self._row(now, ev, "place", y, cached, ask, at_q, q, bid, ms, n, "")], now)

    def _row(self, now, ev, stage, y, ws_ask, ask, depth, edge, bid, ms, at, anyc):
        m, side = ev["m"], ev["side"]
        sv = (lambda v: "" if v is None else round(v if side == "yes" else 1.0 - v, 4))
        nz = (lambda v: "" if v is None else v)
        return {"ts": round(now, 2), "jump_id": ev["jid"], "stage": stage, "ticker": m["ticker"],
                "series": m["series"], "left_s": round(m["close"] - now, 1), "side": side,
                "fair_before": sv(ev["y0"]), "fair": sv(y), "ws_ask": nz(ws_ask),
                "book_ask": nz(ask), "depth_ask": nz(depth), "depth_edge": nz(edge),
                "book_bid": nz(bid), "book_ms": ms, "tape_ct_at_ask": at, "tape_ct_any": anyc}

    def _write(self, rows, now):
        if not rows:
            return
        path = self.dir / f"lag-{datetime.fromtimestamp(now, timezone.utc):%Y-%m-%d-%H}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=LAG_COLS)
            if new:
                w.writeheader()
            w.writerows(rows)
