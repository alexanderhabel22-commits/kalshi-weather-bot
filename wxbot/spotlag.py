"""Alt-coin spot-lag PAPER logger for the DOGE and NEAR 15-minute markets (Nov 24, 2026).

Thread "Build DOGE/NEAR spot-lag paper logger". Alex, Nov 24 ~12:30 AM ET: "Ok please do that
paper logger then". Design: section 5 of analysis/m15-permutations/RESULTS.md (project files).

PAPER ONLY. This module has no order, cancel or transfer code and never builds a trading client:
the Kalshi key only signs the websocket login (same Signer as wsfeed.py). Real money would need a
separate OK from Alex and a separate change.

Why: the Nov 24 permutation search found one new lead. When the spot price says a side of the
DOGE or NEAR 15-min market is worth 10c+ more than its Kalshi ask (after the taker fee), paying
that ask won about +4.9c per contract in held-out weeks (both coins). But the same trade one
minute later lost (DOGE -2.6c): Kalshi's alt quotes lag spot by seconds, and 1-minute candles
can't show whether a bot reacting in ~1 second still finds the stale ask. This logger measures
exactly that, second by second.

  Spot     Kalshi's own CF Benchmarks feed (cfbenchmarks_value_5hz: DOGEUSD_RTI / NEARUSD_RTI,
           what Kalshi settles on) when it is fresh, else the median of Coinbase / Kraken /
           Bitstamp mids (m15_fv.Composite). Volatility = 1-minute log returns over the last
           60 minutes (as in the research), needs 30+ minutes of history after a restart.
  Fair     m15_fv.fair_yes: P(60-s average before close >= floor_strike), driftless Gaussian.
  Book     Kalshi orderbook_delta websocket (snapshot + deltas) for every open KXDOGE15M /
           KXNEAR15M market: best ask and the contracts at it, both sides.
  Signal   fair - ask - fee >= 10c on either side, between 14 and 1 minutes before close
           (840 s >= left >= 60 s), 1+ contract at the ask, first signal per market only.
  Timing   ask, size at the ask, fair and edge again at +0.5, +1, +2 and +5 s.
  Paper    1-contract IOC at the +1 s ask if the edge there is still >= 5c and 1+ contract is on
           offer (taker fill at the displayed ask only; no maker fills). Settles from Kalshi's
           result: P&L = payout - ask - fee. The same check is scored at every delay too.
Outputs (branch m15-data): m15/spotlag.json (status for the dashboard TESTS tab, with the daily
"catchability" table that needs no settlements) and m15/spotlag-YYYY-MM-DD.csv (one row per
signal, ET date). Switch: spotlag_control.json on main (mode paper | off), read every 5 min.

Verdict at ~300 paper fills (~3-4 days): keep only if the +1 s fills make >= +3c/ct with the
90% range above 0. At 300 fills the SE is about +/-2.6c, so that is a first read, not proof.
Day-1 check (catchability): if the 10c gap is usually gone within a second, stop.

Run: python -m wxbot.spotlag  (systemd unit deploy/m15/kalshi-spotlag.service)
Off:  spotlag_control.json mode off, or
      sudo systemctl disable --now kalshi-spotlag && sudo touch /var/lib/kalshi-spotlag.disabled
"""
from __future__ import annotations

import csv
import io
import json
import logging
import math
import os
import re
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import requests

log = logging.getLogger("wxbot")

API = "https://api.elections.kalshi.com/trade-api/v2"
REPO = "alexanderhabel22-commits/kalshi-weather-bot"
CONTROL_URL = f"https://raw.githubusercontent.com/{REPO}/main/spotlag_control.json"
SERIES = {"KXDOGE15M": "DOGE", "KXNEAR15M": "NEAR"}
CF_COINS = {"DOGEUSDRTI": "DOGE", "NEARUSDRTI": "NEAR"}    # index ids with _ and - removed

SIGNAL_EDGE = 0.10        # fair - ask - fee to log a signal
FILL_EDGE = 0.05          # still needed at the paper-fill delay
MAX_LEFT_S = 840.0        # minute 14 before close ...
MIN_LEFT_S = 60.0         # ... down to minute 1
DELAYS = (0.5, 1.0, 2.0, 5.0)
FILL_DELAY = 1.0
SCORE_DELAYS = (0.0,) + DELAYS
CF_FRESH_S = 3.0
VOL_LOOK_S = 3600
VOL_MIN_RET = 30          # 1-minute returns needed (30 min of history)
VOL_FLOOR = 2e-5          # per-second log vol floor
SIG_EVERY_S = 15.0        # recompute the hour-long vol this often (CPU)
SCAN_EVERY_S = 0.1        # signal scan / probe cadence
KEEP_DAYS = 14
VERDICT_FILLS = 300


def fee1(q: float) -> float:
    """Kalshi taker fee on one contract at price q, rounded up to the cent."""
    return math.ceil(0.07 * q * (1 - q) * 100 - 1e-9) / 100


def _f(v) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def et_date(ts: float) -> str:
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(ts, ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def et_str(ts: float) -> str:
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(ts, ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S")


def _iso(s) -> float | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ------------------------------------------------------------------ order book (pure)

def _levels(msg: dict, side: str) -> list[tuple[float, float]]:
    """[(price $, qty)] of resting bids on one side from a snapshot, any Kalshi format."""
    for key, scale in ((f"{side}_dollars_fp", 1.0), (f"{side}_dollars", 1.0), (side, 0.01)):
        lv = msg.get(key)
        if isinstance(lv, list):
            out = []
            for row in lv:
                try:
                    p, q = float(row[0]) * scale, float(row[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if q > 0:
                    out.append((round(p, 4), q))
            return out
    return []


class Book:
    """Bids on both sides of one market. Buying YES at a = hitting a NO bid at 1 - a."""

    def __init__(self):
        self.bids: dict[str, dict[float, float]] = {"yes": {}, "no": {}}
        self.ok = False
        self.at = 0.0

    def snapshot(self, msg: dict, now: float) -> None:
        self.bids = {s: dict(_levels(msg, s)) for s in ("yes", "no")}
        self.ok, self.at = True, now

    def delta(self, msg: dict, now: float) -> None:
        side = msg.get("side")
        if side not in ("yes", "no"):
            return
        p = _f(msg.get("price_dollars"))
        if p is None:
            p = _f(msg.get("price"))
            p = None if p is None else p / 100.0
        d = _f(msg.get("delta_fp"))
        if d is None:
            d = _f(msg.get("delta"))
        if p is None or d is None:
            return
        p = round(p, 4)
        q = self.bids[side].get(p, 0.0) + d
        if q > 1e-9:
            self.bids[side][p] = q
        else:
            self.bids[side].pop(p, None)
        self.at = now

    def ask(self, side: str) -> tuple[float | None, float]:
        """(best ask to buy `side`, contracts at that ask)."""
        opp = self.bids["no" if side == "yes" else "yes"]
        if not opp:
            return None, 0.0
        p = max(opp)
        return round(1.0 - p, 4), opp[p]


# ------------------------------------------------------------------ spot + fair value

def sigma_1m(pts, now: float, look_s: int = VOL_LOOK_S) -> float | None:
    """Per-second log vol from 1-minute returns over the last look_s seconds.
    pts: iterable of (whole second, price). Uses the last price in each minute."""
    last: dict[int, tuple[int, float]] = {}
    for s, p, *_ in pts:
        if s >= now - look_s and p and p > 0:
            last[int(s) // 60] = (int(s), p)
    seq = [last[k] for k in sorted(last)]
    var = span = 0.0
    n = 0
    for (s0, a), (s1, b) in zip(seq, seq[1:]):
        dt = s1 - s0
        if dt <= 0 or dt > 300:
            continue
        var += math.log(b / a) ** 2
        span += dt
        n += 1
    if n < VOL_MIN_RET or span <= 0:
        return None
    return max(math.sqrt(var / span), VOL_FLOOR)


def cf_coin(index_id) -> str | None:
    return CF_COINS.get(re.sub(r"[^A-Z0-9]", "", str(index_id or "").upper()))


class Spot:
    """Fair value from the CF index (preferred) or the free-exchange median (fallback)."""

    def __init__(self, comp=None):
        if comp is None:
            from .m15_fv import Composite
            comp = Composite(sorted(set(SERIES.values())), keep_s=VOL_LOOK_S + 300)
        self.comp = comp
        self.cf_ids: dict[str, int] = {}
        self._sig: dict[tuple, tuple[float, float | None]] = {}   # (coin, src) -> (at, sigma)

    def sigma(self, coin: str, cf: bool, now: float) -> float | None:
        """sigma_1m, recomputed at most every SIG_EVERY_S (it walks an hour of points)."""
        k = (coin, cf)
        hit = self._sig.get(k)
        if hit and now - hit[0] < SIG_EVERY_S:
            return hit[1]
        src = self.comp.cf_series if cf else self.comp.series
        v = sigma_1m(src.get(coin, ()), now)
        self._sig[k] = (now, v)
        return v

    def start(self):
        self.comp.start(venues=("coinbase", "kraken", "bitstamp"), client=None)
        return self

    def on_cf(self, msg: dict, now: float) -> None:
        iid = msg.get("index_id")
        self.cf_ids[str(iid)] = self.cf_ids.get(str(iid), 0) + 1
        coin = cf_coin(iid)
        p = _f(msg.get("value_usd", msg.get("value")))
        if coin and p and p > 0:
            self.comp.on_cf(coin, p, now)

    def sample(self, now: float) -> None:
        self.comp.sample(now)

    def fair(self, coin: str, strike: float, close: float, now: float) -> dict | None:
        from .m15_fv import fair_yes
        left = close - now
        d = self.comp.cf_series.get(coin)
        if d and now - d[-1][0] <= CF_FRESH_S:
            sig = self.sigma(coin, True, now)
            if sig is not None:
                px = d[-1][1]
                return {"src": "cf", "px": px, "sigma": sig, "fv_yes": fair_yes(px, strike, sig, left, [])}
        if self.comp.healthy(coin, now):
            px, n = self.comp.now_px(coin, now)
            sig = self.sigma(coin, False, now)
            if px is not None and sig is not None:
                return {"src": f"mix{n}", "px": px, "sigma": sig,
                        "fv_yes": fair_yes(px, strike, sig, left, [])}
        return None

    def health(self, now: float) -> dict:
        out = {}
        for c in sorted(set(SERIES.values())):
            cf, mx = self.comp.cf_series.get(c), self.comp.series.get(c)
            out[c] = {"cf_age_s": round(now - cf[-1][0], 1) if cf else None,
                      "mix_age_s": round(now - mx[-1][0], 1) if mx else None,
                      "venues": self.comp.now_px(c, now)[1],
                      "cf_vol_ok": self.sigma(c, True, now) is not None,
                      "mix_vol_ok": self.sigma(c, False, now) is not None}
        return out


# ------------------------------------------------------------------ scoring (pure)

def _mean_se(xs: list[float]) -> tuple[float | None, float | None]:
    if not xs:
        return None, None
    m = sum(xs) / len(xs)
    if len(xs) < 2:
        return m, None
    return m, statistics.stdev(xs) / math.sqrt(len(xs))


def probe_pnl(sig: dict, key: str) -> float | None:
    """Paper P&L of 1 contract bought at the ask seen at probe `key` ('0', '0.5', '1', ...) when
    that probe still had edge >= FILL_EDGE and 1+ contract; None if no trade or unsettled."""
    if sig.get("result") not in ("yes", "no"):
        return None
    p = (sig.get("probes") or {}).get(key)
    if not p or p.get("ask") is None or (p.get("size") or 0) < 1:
        return None
    if key != "0" and (p.get("edge") is None or p["edge"] < FILL_EDGE - 1e-9):
        return None
    win = 1.0 if sig["result"] == sig["side"] else 0.0
    return round(win - p["ask"] - fee1(p["ask"]), 4)


def dkey(d: float) -> str:
    return f"{d:g}"


def catchability(sigs: list[dict]) -> list[dict]:
    """Per ET day: share of 10c+ signals whose gap is still >= 5c at each delay, median edge left."""
    by: dict[str, list[dict]] = {}
    for s in sigs:
        by.setdefault(s["day"], []).append(s)
    out = []
    for day in sorted(by, reverse=True):
        rows = by[day]
        r = {"day": day, "signals": len(rows), "by_coin": {}}
        for c in sorted(set(SERIES.values())):
            r["by_coin"][c] = sum(1 for s in rows if s["coin"] == c)
        for d in (0.0,) + DELAYS:
            k = dkey(d)
            done = [s for s in rows if k in (s.get("probes") or {})]
            eds = [s["probes"][k]["edge"] for s in done if s["probes"][k].get("edge") is not None]
            still = sum(1 for s in done
                        if (s["probes"][k].get("edge") or -9) >= FILL_EDGE - 1e-9
                        and (s["probes"][k].get("size") or 0) >= 1)
            same = sum(1 for s in done if s["probes"][k].get("ask") == s["probes"]["0"].get("ask"))
            r[k] = {"n": len(done), "still_5c": still,
                    "share_5c": round(still / len(done), 3) if done else None,
                    "median_edge_c": round(100 * statistics.median(eds), 1) if eds else None,
                    "same_ask": round(same / len(done), 3) if done else None}
        out.append(r)
    return out


def scores(sigs: list[dict]) -> dict:
    out = {}
    for d in SCORE_DELAYS:
        k = dkey(d)
        xs = [x for x in (probe_pnl(s, k) for s in sigs) if x is not None]
        m, se = _mean_se(xs)
        wins = sum(1 for x in xs if x > 0)
        out[k] = {"n": len(xs), "won": wins, "pnl": round(sum(xs), 2),
                  "c_per_ct": None if m is None else round(100 * m, 2),
                  "se_c": None if se is None else round(100 * se, 2),
                  "lo90_c": None if se is None else round(100 * (m - 1.645 * se), 2),
                  "hi90_c": None if se is None else round(100 * (m + 1.645 * se), 2)}
    return out


def verdict(sc: dict) -> dict:
    s = sc.get(dkey(FILL_DELAY)) or {}
    n = s.get("n", 0)
    if n < VERDICT_FILLS:
        return {"state": "collecting", "fills": n, "need": VERDICT_FILLS,
                "text": f"{n} of {VERDICT_FILLS} settled +1 s paper fills"}
    ok = (s.get("c_per_ct") or -99) >= 3.0 and (s.get("lo90_c") or -99) > 0
    return {"state": "pass" if ok else "fail", "fills": n, "need": VERDICT_FILLS,
            "text": ("+1 s fills >= +3c/ct with the 90% range above 0: bring Alex a 1-ct real test"
                     if ok else "+1 s fills below the bar (+3c/ct, 90% range above 0): stop")}


CSV_COLS = ["ts", "et", "ticker", "coin", "side", "left_s", "strike", "src", "spot", "sigma_bps_s"]
for _k in ("0",) + tuple(dkey(d) for d in DELAYS):
    CSV_COLS += [f"ask_{_k}", f"size_{_k}", f"fair_{_k}", f"edge_{_k}", f"dt_{_k}"]
CSV_COLS += ["paper_fill", "fill_ask", "result", "pnl_1s"] + [f"pnl_{dkey(d)}" for d in SCORE_DELAYS]


def csv_text(sigs: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLS, extrasaction="ignore")
    w.writeheader()
    for s in sigs:
        row = {k: s.get(k, "") for k in ("ts", "et", "ticker", "coin", "side", "left_s", "strike",
                                         "src", "spot", "sigma_bps_s", "result")}
        for k, p in (s.get("probes") or {}).items():
            for f in ("ask", "size", "fair", "edge", "dt"):
                row[f"{f}_{k}"] = "" if p.get(f) is None else p[f]
        row["paper_fill"] = 1 if s.get("fill") else 0
        row["fill_ask"] = (s.get("fill") or {}).get("ask", "")
        for d in SCORE_DELAYS:
            x = probe_pnl(s, dkey(d))
            row[f"pnl_{dkey(d)}"] = "" if x is None else x
        row["pnl_1s"] = row[f"pnl_{dkey(FILL_DELAY)}"]
        w.writerow(row)
    return buf.getvalue()


# ------------------------------------------------------------------ the logger

class Resync(Exception):
    """Order-book sequence gap: reconnect for fresh snapshots."""


class SpotLag:
    def __init__(self, out_dir: Path, spot: Spot, signer=None, get: Callable | None = None,
                 status_up: Callable[[dict], None] | None = None, clock: Callable[[], float] = time.time):
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.spot = spot
        self.signer = signer
        self.status_up = status_up
        self.clock = clock
        self._s = requests.Session()
        self._s.headers["User-Agent"] = "kalshi-spotlag/1 (paper research logger)"
        self.get = get or self._get
        self.mode = "paper"
        self.control_at = 0.0
        self.markets: dict[str, dict] = {}       # ticker -> {coin, close, strike}
        self.books: dict[str, Book] = {}
        self.signals: list[dict] = []
        self.signaled: set[str] = set()
        self.dirty: set[str] = set()             # ET days whose CSV changed since upload
        self.seq: dict = {}
        self.ws = None
        self.subscribed: set[str] = set()
        self._id = 0
        self.counts = {"book_msgs": 0, "cf_msgs": 0, "resyncs": 0, "connects": 0, "errors": 0,
                       "no_fair": 0, "scans": 0}
        self.last_msg = 0.0
        self.started = clock()
        self.last_err = ""
        self.load()

    # -- persistence
    @property
    def state_path(self) -> Path:
        return self.out / "spotlag-state.json"

    def load(self) -> None:
        try:
            js = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return
        self.signals = js.get("signals") or []
        self.signaled = {s["ticker"] for s in self.signals}

    def save(self) -> None:
        cut = self.clock() - KEEP_DAYS * 86400
        self.signals = [s for s in self.signals if s["ts"] >= cut]
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"signals": self.signals}, separators=(",", ":")))
        tmp.replace(self.state_path)

    # -- REST (public, GET only)
    def _get(self, url: str, params: dict | None = None):
        r = self._s.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def read_control(self, now: float) -> None:
        try:
            c = self.get(CONTROL_URL, {"t": int(now)}) or {}
            m = str(c.get("mode") or "paper").lower()
            self.mode = m if m in ("paper", "off") else "paper"
            self.control_at = now
        except Exception as exc:  # noqa: BLE001  keep the last mode (paper is harmless)
            log.warning("spotlag control: %s", exc)

    def refresh_markets(self, now: float) -> None:
        for series, coin in SERIES.items():
            try:
                js = self.get(f"{API}/markets", {"series_ticker": series, "status": "open", "limit": 20})
            except Exception as exc:  # noqa: BLE001
                log.warning("spotlag markets %s: %s", series, exc)
                continue
            for m in (js or {}).get("markets") or []:
                tk, close = m.get("ticker"), _iso(m.get("close_time"))
                if not tk or not close or close <= now:
                    continue
                k = _f(m.get("floor_strike"))
                cur = self.markets.setdefault(tk, {"coin": coin, "close": close, "strike": None})
                cur["close"] = close
                if k and k > 0:
                    cur["strike"] = k
        for tk in [t for t, m in self.markets.items() if m["close"] < now - 30]:
            self.markets.pop(tk, None)
            self.books.pop(tk, None)

    # -- websocket
    def _send(self, obj: dict) -> None:
        if self.ws is not None:
            self.ws.send(json.dumps(obj))

    def subscribe_new(self) -> None:
        new = sorted(t for t in self.markets if t not in self.subscribed)
        if not new:
            return
        self._id += 1
        self._send({"id": self._id, "cmd": "subscribe",
                    "params": {"channels": ["orderbook_delta"], "market_tickers": new}})
        self.subscribed |= set(new)

    def handle(self, raw, now: float) -> None:
        try:
            js = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(js, dict):
            return
        typ = str(js.get("type") or "")
        msg = js.get("msg") if isinstance(js.get("msg"), dict) else {}
        self.last_msg = now
        if typ.startswith("cfbenchmarks"):
            self.counts["cf_msgs"] += 1
            self.spot.on_cf(msg, now)
            return
        if typ not in ("orderbook_snapshot", "orderbook_delta"):
            if typ == "error":
                self.last_err = json.dumps(msg)[:200]
                log.warning("spotlag ws error: %s", msg)
            return
        sid, seq = js.get("sid"), js.get("seq")
        if sid is not None and isinstance(seq, int):
            prev = self.seq.get(sid)
            if typ == "orderbook_delta" and prev is not None and seq != prev + 1:
                self.counts["resyncs"] += 1
                raise Resync(f"sid {sid} seq {prev} -> {seq}")
            self.seq[sid] = seq
        tk = msg.get("market_ticker")
        if not tk:
            return
        self.counts["book_msgs"] += 1
        b = self.books.setdefault(tk, Book())
        if typ == "orderbook_snapshot":
            b.snapshot(msg, now)
        elif b.ok:
            b.delta(msg, now)

    # -- signals
    def side_edges(self, tk: str, now: float):
        """[(edge, side, ask, size, fair_side)] for both sides, plus the fair dict."""
        m, b = self.markets.get(tk), self.books.get(tk)
        if not m or not b or not b.ok or not m.get("strike"):
            return [], None
        fv = self.spot.fair(m["coin"], m["strike"], m["close"], now)
        if fv is None:
            return [], None
        out = []
        for side in ("yes", "no"):
            ask, size = b.ask(side)
            f = fv["fv_yes"] if side == "yes" else 1.0 - fv["fv_yes"]
            e = None if ask is None or not (0 < ask < 1) else f - ask - fee1(ask)
            out.append((e, side, ask, size, f))
        return out, fv

    def probe(self, s: dict, now: float, key: str) -> None:
        edges, _ = self.side_edges(s["ticker"], now)
        row = next((x for x in edges if x[1] == s["side"]), None)
        e, _, ask, size, f = row if row else (None, s["side"], None, 0.0, None)
        b = self.books.get(s["ticker"])
        if row is None and b is not None and b.ok:   # book fine, fair missing: still log the ask
            ask, size = b.ask(s["side"])
        s["probes"][key] = {"ask": ask, "size": size, "fair": None if f is None else round(f, 4),
                            "edge": None if e is None else round(e, 4), "dt": round(now - s["ts"], 3)}

    def scan(self, now: float) -> None:
        if self.mode != "paper":
            return
        self.counts["scans"] += 1
        for tk, m in self.markets.items():
            left = m["close"] - now
            if tk in self.signaled or not (MIN_LEFT_S <= left <= MAX_LEFT_S):
                continue
            edges, fv = self.side_edges(tk, now)
            if fv is None:
                continue
            best = max((x for x in edges if x[0] is not None and x[3] >= 1), default=None,
                       key=lambda x: x[0])
            if best is None or best[0] < SIGNAL_EDGE - 1e-9:
                continue
            e, side, ask, size, f = best
            s = {"ts": round(now, 3), "et": et_str(now), "day": et_date(now), "ticker": tk,
                 "coin": m["coin"], "side": side, "left_s": round(left, 1), "close": m["close"],
                 "strike": m["strike"], "src": fv["src"], "spot": round(fv["px"], 6),
                 "sigma_bps_s": round(fv["sigma"] * 1e4, 3), "todo": [dkey(d) for d in DELAYS],
                 "probes": {"0": {"ask": ask, "size": size, "fair": round(f, 4), "edge": round(e, 4),
                                  "dt": 0.0}},
                 "fill": None, "result": None}
            self.signals.append(s)
            self.signaled.add(tk)
            self.dirty.add(s["day"])
            log.info("spotlag signal %s %s ask %.3f fair %.3f edge %.3f", tk, side, ask, f, e)

    def run_probes(self, now: float) -> None:
        for s in self.signals:
            todo = s.get("todo")
            while todo and now - s["ts"] >= float(todo[0]) - 1e-6:
                k = todo.pop(0)
                self.probe(s, now, k)
                if k == dkey(FILL_DELAY):
                    p = s["probes"][k]
                    if p["ask"] is not None and (p["size"] or 0) >= 1 and (p["edge"] or -9) >= FILL_EDGE - 1e-9:
                        s["fill"] = {"ask": p["ask"], "fee": fee1(p["ask"]), "ts": round(now, 3)}
                self.dirty.add(s["day"])
            if todo == []:
                s.pop("todo", None)

    def settle(self, now: float, budget: int = 6) -> None:
        for s in self.signals:
            if budget <= 0:
                return
            if s.get("result") or now < s["close"] + 90 or now - s.get("settle_try", 0) < 60:
                continue
            if now - s["close"] > 3 * 86400:
                s["result"] = "unsettled"
                continue
            s["settle_try"] = now
            budget -= 1
            try:
                m = (self.get(f"{API}/markets/{s['ticker']}", None) or {}).get("market") or {}
            except Exception as exc:  # noqa: BLE001
                log.warning("spotlag settle %s: %s", s["ticker"], exc)
                continue
            r = str(m.get("result") or "").lower()
            if r in ("yes", "no"):
                s["result"] = r
                s["exp_value"] = m.get("expiration_value")
                self.dirty.add(s["day"])

    # -- status
    def status(self, now: float) -> dict:
        sigs = [s for s in self.signals if s.get("todo") is None]
        sc = scores(sigs)
        by_coin = {c: scores([s for s in sigs if s["coin"] == c]) for c in sorted(set(SERIES.values()))}
        src = {}
        for s in sigs:
            k = "cf" if s["src"] == "cf" else "mix"
            src[k] = src.get(k, 0) + 1
        recent = []
        for s in self.signals[-25:][::-1]:
            p0, p1 = s["probes"].get("0", {}), s["probes"].get(dkey(FILL_DELAY), {})
            recent.append({"et": s["et"], "ticker": s["ticker"], "coin": s["coin"], "side": s["side"],
                           "left_s": s["left_s"], "src": s["src"], "ask0": p0.get("ask"),
                           "edge0": p0.get("edge"), "ask1": p1.get("ask"), "edge1": p1.get("edge"),
                           "size0": p0.get("size"), "size1": p1.get("size"),
                           "fill": bool(s.get("fill")), "result": s.get("result"),
                           "pnl": probe_pnl(s, dkey(FILL_DELAY))})
        return {"at": et_str(now), "mode": self.mode, "paper_only": True,
                "since": et_str(min([s["ts"] for s in self.signals] or [self.started])),
                "rule": {"signal_edge_c": 100 * SIGNAL_EDGE, "fill_edge_c": 100 * FILL_EDGE,
                         "minutes": "14 to 1 before close", "fill_delay_s": FILL_DELAY,
                         "delays_s": list(DELAYS), "contracts": 1},
                "signals": len(sigs), "paper_fills": sum(1 for s in sigs if s.get("fill")),
                "open": sum(1 for s in sigs if s.get("fill") and not s.get("result")),
                "scores": sc, "by_coin": by_coin, "verdict": verdict(sc), "src": src,
                "catch": catchability(sigs)[:10], "recent": recent,
                "feeds": {"spot": self.spot.health(now), "cf_ids": dict(sorted(getattr(self.spot, "cf_ids", {}).items())[:20]),
                          "ws_age_s": round(now - self.last_msg, 1) if self.last_msg else None,
                          "markets": len(self.markets), "books_ok": sum(1 for b in self.books.values() if b.ok),
                          **self.counts, "last_err": self.last_err}}

    def upload(self, now: float) -> None:
        if not self.status_up:
            return
        files = {"spotlag.json": json.dumps(self.status(now), indent=1)}
        for day in sorted(self.dirty):
            files[f"spotlag-{day}.csv"] = csv_text([s for s in self.signals if s["day"] == day])
        try:
            self.status_up(files)
            self.dirty = set()
        except Exception as exc:  # noqa: BLE001  retried next round
            log.warning("spotlag upload: %s", exc)

    # -- loop
    def tick(self, now: float, timers: dict) -> None:
        self.spot.sample(now)
        if now >= timers.get("ctl", 0):
            timers["ctl"] = now + 300
            self.read_control(now)
        if now >= timers.get("mk", 0):
            timers["mk"] = now + 20
            self.refresh_markets(now)
            self.subscribe_new()
        if now - timers.get("scan", 0) >= SCAN_EVERY_S:   # not once per websocket message
            timers["scan"] = now
            self.scan(now)
            self.run_probes(now)
        if now >= timers.get("st", 0):
            timers["st"] = now + 20
            self.settle(now)
        if now >= timers.get("save", 0):
            timers["save"] = now + 60
            self.save()
        if now >= timers.get("up", 0):
            timers["up"] = now + 600
            self.upload(now)

    def session(self, timers: dict) -> None:
        from websockets.sync.client import connect

        from .m15_feeds import KALSHI_WS
        hdr = self.signer.headers("GET", KALSHI_WS) if self.signer else {}
        started = self.clock()
        with connect(KALSHI_WS, additional_headers=hdr, open_timeout=15, ping_interval=20,
                     ping_timeout=60, max_size=2 ** 23) as ws:
            self.ws = ws
            self.subscribed, self.seq, self.books = set(), {}, {}
            self.counts["connects"] += 1
            self._id += 1
            self._send({"id": self._id, "cmd": "subscribe",
                        "params": {"channels": ["cfbenchmarks_value_5hz"], "index_ids": ["all"]}})
            self.refresh_markets(self.clock())
            self.subscribe_new()
            log.warning("spotlag connected (%d markets)", len(self.markets))
            while True:
                try:
                    raw = ws.recv(timeout=0.05)
                except TimeoutError:
                    raw = None
                now = self.clock()
                if raw is not None:
                    self.handle(raw, now)
                self.tick(now, timers)
                if self.last_msg and now - self.last_msg > 60:
                    raise Resync("no websocket messages for 60 s")
                if now - started > 6 * 3600:
                    return

    def run(self) -> None:
        timers: dict = {}
        backoff = 5
        while True:
            t0 = self.clock()
            try:
                self.session(timers)
                backoff = 5
            except Exception as exc:  # noqa: BLE001  never die; log and reconnect
                if self.clock() - t0 > 120:      # it ran fine for a while: quick retry
                    backoff = 5
                self.counts["errors"] += 1
                self.last_err = str(exc)[:200]
                log.warning("spotlag session: %s", exc)
                time.sleep(1 if isinstance(exc, Resync) else backoff)
                if not isinstance(exc, Resync):
                    backoff = min(backoff * 2, 300)
            finally:
                self.ws = None
                self.last_msg = 0.0
            try:
                self.save()
            except Exception as exc:  # noqa: BLE001
                log.warning("spotlag save: %s", exc)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from .wsfeed import Signer
    out = Path(os.environ.get("SPOTLAG_DIR", "/var/lib/kalshi-spotlag"))
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    kid = os.environ.get("KALSHI_API_KEY_ID", "").strip()
    pem = os.environ.get("KALSHI_PRIVATE_KEY", "").strip()
    path = os.environ.get("KALSHI_PRIVATE_KEY_FILE")
    if not pem and path:
        try:
            pem = Path(path).read_text()
        except OSError as exc:
            log.warning("key file %s: %s", path, exc)
    for k in list(os.environ):          # paper only: drop every secret it doesn't need
        if k.startswith(("KALSHI", "NTFY", "ANTHROPIC", "OANDA")):
            os.environ.pop(k, None)
    if not kid or not pem:
        log.warning("spotlag: no Kalshi key (the websocket needs a signed login); idle")
        while True:
            time.sleep(3600)
    status_up = None
    if token:
        from .m15 import github_uploader
        from .util import Http
        status_up = github_uploader(Http("kalshi-spotlag/1"), token)
    SpotLag(out, Spot().start(), signer=Signer(kid, pem), status_up=status_up).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
