"""Always-on Kalshi websocket recorder for the main bot's markets (server; logs only, never
trades). Added Nov 21, 2026 (thread "Add Kalshi websocket speed, find speed-sensitive markets").

Why: the main bot checks Kalshi every 5 minutes and only learns about a fill on its next run.
The speed study (analysis/speed/RESULTS.md in the project files) replayed every real fill
against Kalshi's tape and found no live strategy where reacting faster to Kalshi's own prices
would have changed cents per contract: the fills that went badly were swept in one go, in the
same millisecond as the move. This recorder keeps the exact data needed to re-check that on
every new fill, and to replay any "pull the order on a fast move" guard on paper first.

What it records (raw websocket messages plus our receive time, one JSON line each):
  - ticker (best bid/ask) and trade for every market the main bot has a real order resting or
    held in, the other brackets of those events, and the S&P 500 / Nasdaq-100 range and
    biggest-quake markets;
  - orderbook_delta (full book) for the markets where a real order is resting;
  - fill: our own fills on the whole account, to the millisecond;
  - jump: a derived line whenever a watched market's mid moves 5c+ within 60 s.
Hourly files wsfeed-YYYY-MM-DD-HH.jsonl are gzipped and uploaded to branch wsfeed-data
(folder wsfeed/) once the hour is over; local copies older than 2 days are deleted.

Safety: the Kalshi key only signs the websocket login. This module has no order, cancel or
transfer code and never builds a trading client; every other secret is dropped at start.
Off:  sudo systemctl disable --now kalshi-wsfeed && sudo touch /var/lib/kalshi-wsfeed.disabled
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests

from .m15_feeds import KALSHI_WS, _dollars

log = logging.getLogger("wxbot")

BRANCH = "wsfeed-data"
REPO = "alexanderhabel22-commits/kalshi-weather-bot"
ORDERS_URL = f"https://raw.githubusercontent.com/{REPO}/main/data/trade_orders.csv"
API = "https://api.elections.kalshi.com/trade-api/v2"
# series watched even with no order of ours: speed-sensitive ones from the study
ALWAYS = {"KXINX": 36.0, "KXNASDAQ100": 36.0, "KXBIGGESTQUAKE": 72.0}   # series -> hours to close
OPEN = ("resting", "filled", "partial")
JUMP_C = 0.05
JUMP_S = 60.0


class Signer:
    """Signs the websocket login only. Deliberately not a KalshiClient: no order methods."""

    def __init__(self, key_id: str, pem: str):
        from .kalshi_api import _load_key
        self.key_id = key_id
        self.key = _load_key(pem.replace("\\n", "\n"))

    def headers(self, method: str, url: str) -> dict:
        import base64
        from urllib.parse import urlparse
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        ts = str(int(time.time() * 1000))
        sig = self.key.sign((ts + method.upper() + urlparse(url).path).encode(),
                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                        salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
        return {"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode()}


def open_orders(csv_text: str) -> tuple[list[str], list[str], list[str]]:
    """(resting tickers, held tickers, events) of the main bot's open REAL orders."""
    resting, held, events = [], [], []
    for r in csv.DictReader(io.StringIO(csv_text)):
        if r.get("mode") != "live" or r.get("status") not in OPEN or not r.get("ticker"):
            continue
        tk = r["ticker"]
        if r.get("status") == "resting" and tk not in resting:
            resting.append(tk)
        elif tk not in held and tk not in resting:
            held.append(tk)
        ev = r.get("event") or tk.rsplit("-", 1)[0]
        if ev not in events:
            events.append(ev)
    return resting, held, events


def pick(resting, held, siblings, always, cap: int, book_cap: int) -> tuple[list[str], list[str]]:
    """Watch list in priority order (our orders first), capped; full books for resting ones."""
    out: list[str] = []
    for group in (resting, held, siblings, always):
        for tk in group:
            if tk not in out:
                out.append(tk)
    return out[:cap], list(dict.fromkeys(resting))[:book_cap]


def _mid(q: dict) -> float | None:
    b, a = q.get("yes_bid"), q.get("yes_ask")
    if b is None or a is None or a <= 0 or a >= 1 or b <= 0:
        return None
    return (a + b) / 2


class WsFeed:
    def __init__(self, out_dir: Path, signer=None, uploader: Callable[[str, bytes], None] | None = None,
                 get: Callable | None = None, clock: Callable[[], float] = time.time,
                 max_markets: int = 400, book_markets: int = 60, hour_cap_bytes: int = 60_000_000):
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.signer = signer
        self.uploader = uploader
        self.get = get or self._get
        self.clock = clock
        self.max_markets, self.book_markets = max_markets, book_markets
        self.hour_cap = hour_cap_bytes
        self.ws = None
        self.tickers: list[str] = []
        self.books: list[str] = []
        self.subscribed: set[str] = set()
        self.booked: set[str] = set()
        self.quotes: dict[str, dict] = {}
        self.mids: dict[str, deque] = {}
        self.last_jump: dict[str, float] = {}
        self.uploaded: set[str] = set()
        self.capped_hour = ""
        self._id = 0
        self._s = requests.Session()
        self._s.headers["User-Agent"] = "kalshi-wsfeed/1.0"

    # -- files
    def _hour_file(self, ts: float) -> Path:
        h = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d-%H")
        return self.out / f"wsfeed-{h}.jsonl"

    def write(self, row: dict) -> None:
        now = self.clock()
        row.setdefault("t", round(now, 3))
        p = self._hour_file(now)
        line = json.dumps(row, separators=(",", ":")) + "\n"
        with p.open("a") as f:
            f.write(line)

    def over_cap(self, now: float) -> bool:
        p = self._hour_file(now)
        try:
            big = p.stat().st_size > self.hour_cap
        except OSError:
            return False
        if big and self.capped_hour != p.name:
            self.capped_hour = p.name
            self.write({"kind": "cap", "note": "hour file over the size cap: order books skipped this hour"})
        return big

    def upload_done_hours(self, now: float) -> None:
        cur = self._hour_file(now).name
        for p in sorted(self.out.glob("wsfeed-*.jsonl")):
            if p.name == cur:
                continue
            if self.uploader and p.name not in self.uploaded:
                try:
                    self.uploader(f"wsfeed/{p.name}.gz", gzip.compress(p.read_bytes()))
                    self.uploaded.add(p.name)
                except Exception as exc:  # noqa: BLE001  retried next round
                    log.warning("wsfeed upload %s: %s", p.name, exc)
                    continue
            if now - p.stat().st_mtime > 2 * 86400 and (p.name in self.uploaded or not self.uploader):
                p.unlink(missing_ok=True)

    # -- what to watch
    def _get(self, url: str, params: dict | None = None):
        r = self._s.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.json() if "json" in r.headers.get("content-type", "") or url.startswith(API) else r.text

    def _markets(self, **params) -> list[dict]:
        try:
            js = self.get(f"{API}/markets", {"limit": 1000, "status": "open", **params})
            return (js or {}).get("markets") or []
        except Exception as exc:  # noqa: BLE001
            log.warning("wsfeed markets %s: %s", params, exc)
            return []

    def watch_list(self, now: float) -> tuple[list[str], list[str]]:
        try:
            text = self.get(ORDERS_URL, None)
            resting, held, events = open_orders(text if isinstance(text, str) else "")
        except Exception as exc:  # noqa: BLE001  keep the old list
            log.warning("wsfeed orders: %s", exc)
            return self.tickers, self.books
        siblings = [m["ticker"] for ev in events[:60] for m in self._markets(event_ticker=ev) if m.get("ticker")]
        always = []
        for series, hours in ALWAYS.items():
            for m in self._markets(series_ticker=series):
                try:
                    close = datetime.fromisoformat(str(m.get("close_time")).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    continue
                if 0 < close - now <= hours * 3600:
                    always.append(m["ticker"])
        return pick(resting, held, siblings, always, self.max_markets, self.book_markets)

    # -- websocket
    def _send(self, obj: dict) -> None:
        if self.ws is not None:
            self.ws.send(json.dumps(obj))

    def subscribe_new(self) -> None:
        new = [t for t in self.tickers if t not in self.subscribed]
        for i in range(0, len(new), 100):
            self._id += 1
            self._send({"id": self._id, "cmd": "subscribe",
                        "params": {"channels": ["ticker", "trade"], "market_tickers": new[i:i + 100]}})
        self.subscribed |= set(new)
        nb = [t for t in self.books if t not in self.booked]
        for i in range(0, len(nb), 50):
            self._id += 1
            self._send({"id": self._id, "cmd": "subscribe",
                        "params": {"channels": ["orderbook_delta"], "market_tickers": nb[i:i + 50]}})
        self.booked |= set(nb)
        if new or nb:
            self.write({"kind": "watch", "markets": len(self.tickers), "books": len(self.books),
                        "added": new[:50], "books_added": nb})

    def handle(self, raw, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        try:
            js = json.loads(raw)
        except (TypeError, ValueError):
            return
        typ = js.get("type")
        msg = js.get("msg") if isinstance(js.get("msg"), dict) else {}
        if typ in ("orderbook_snapshot", "orderbook_delta") and self.over_cap(now):
            return
        if typ in ("subscribed", "ok"):
            return
        self.write({"t": round(now, 3), "type": typ, "sid": js.get("sid"), "seq": js.get("seq"), "msg": msg})
        if typ == "error":
            log.warning("wsfeed error: %s", msg)
        if typ != "ticker":
            return
        tk = msg.get("market_ticker")
        if not tk:
            return
        q = self.quotes.setdefault(tk, {})
        for k in ("yes_bid", "yes_ask"):
            v = _dollars(msg, k)
            if v is not None:
                q[k] = v
        m = _mid(q)
        if m is None:
            return
        d = self.mids.setdefault(tk, deque())
        d.append((now, m))
        while d and now - d[0][0] > JUMP_S:
            d.popleft()
        lo, hi = min(x[1] for x in d), max(x[1] for x in d)
        if hi - lo >= JUMP_C - 1e-9 and now - self.last_jump.get(tk, 0) > JUMP_S:
            self.last_jump[tk] = now
            first = d[0][1]
            self.write({"kind": "jump", "ticker": tk, "from": round(first, 3), "to": round(m, 3),
                        "lo": round(lo, 3), "hi": round(hi, 3), "window_s": round(now - d[0][0], 1)})

    def _connect(self):
        from websockets.sync.client import connect
        hdr = self.signer.headers("GET", KALSHI_WS) if self.signer else {}
        return connect(KALSHI_WS, additional_headers=hdr, open_timeout=15, ping_interval=10,
                       ping_timeout=20, max_size=2 ** 23)

    def session(self) -> None:
        """One connection: subscribe, then read until it drops, refreshing the list every 5 min."""
        started = self.clock()
        with self._connect() as ws:
            self.ws = ws
            self.subscribed, self.booked = set(), set()
            self._id += 1
            self._send({"id": self._id, "cmd": "subscribe", "params": {"channels": ["fill"]}})
            self.subscribe_new()
            self.write({"kind": "connect", "markets": len(self.tickers), "books": len(self.books)})
            next_refresh = self.clock() + 300
            while True:
                try:
                    raw = ws.recv(timeout=5)
                except TimeoutError:
                    raw = None
                if raw is not None:
                    self.handle(raw)
                now = self.clock()
                if now >= next_refresh:
                    next_refresh = now + 300
                    self.upload_done_hours(now)
                    old = set(self.tickers)
                    self.tickers, self.books = self.watch_list(now)
                    gone = len(old - set(self.tickers))
                    # dropped markets stay subscribed until a reconnect; reconnect when many
                    # are stale, and every 6 h anyway (keeps the subscription list clean)
                    if gone > 100 or now - started > 6 * 3600:
                        self.write({"kind": "reconnect", "stale": gone})
                        return
                    self.subscribe_new()

    def run(self) -> None:
        self.write({"kind": "start"})
        backoff = 5
        while True:
            try:
                self.tickers, self.books = self.watch_list(self.clock())
                self.session()
                backoff = 5
            except Exception as exc:  # noqa: BLE001  never die; log and reconnect
                log.warning("wsfeed session: %s", exc)
                self.write({"kind": "disconnect", "error": str(exc)[:200]})
                time.sleep(backoff)
                backoff = min(backoff * 2, 300)
            finally:
                self.ws = None
            try:
                self.upload_done_hours(self.clock())
            except Exception as exc:  # noqa: BLE001
                log.warning("wsfeed upload: %s", exc)


def main() -> int:
    from .srclog import github_put
    out = Path(os.environ.get("WSFEED_DIR", "/var/lib/kalshi-wsfeed"))
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    kid = os.environ.get("KALSHI_API_KEY_ID", "").strip()
    pem = os.environ.get("KALSHI_PRIVATE_KEY", "").strip()
    path = os.environ.get("KALSHI_PRIVATE_KEY_FILE")
    if not pem and path:
        try:
            pem = Path(path).read_text()
        except OSError as exc:
            log.warning("key file %s: %s", path, exc)
    for k in list(os.environ):          # logs only: drop every secret it doesn't need
        if k.startswith(("KALSHI", "NTFY", "ANTHROPIC", "OANDA")):
            os.environ.pop(k, None)
    if not kid or not pem:
        log.warning("wsfeed: no Kalshi key (the websocket needs a signed login); idle")
        while True:
            time.sleep(3600)
    WsFeed(out, signer=Signer(kid, pem), uploader=github_put(token, branch=BRANCH) if token else None).run()
    return 0
