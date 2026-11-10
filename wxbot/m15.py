"""Always-on 15-minute longshot fade bot (runs on a small server, not in GitHub Actions).

Research (thread "Audit every Kalshi category", Nov 8 2026): in Kalshi's 15-minute
up/down markets, whoever sells the 5-29c longshot (owns the 71-95c favourite as a
resting maker bid) earned BTC +7.3c, silver/copper +5.2c, alt crypto +3.1c per
contract after maker fees, counting a fill only when a later trade went THROUGH the
price (back of the queue). These markets close every 15 minutes, which the 5-minute
Actions bot can't follow, so this runs as its own process:

  python bot.py m15            run forever (systemd keeps it up)
  python bot.py m15 --once     one step, for testing

Every few seconds it:
  1. finds the open 15-minute markets of the configured series (every minute),
  2. reads their best bids (one batch request),
  3. keeps ONE resting buy order per market on the favourite side, joining the best
     bid while that bid is 71-95c (longshot 5-29c), at most `contracts` per market,
  4. paper: counts a fill only when a later longshot trade prints through our price;
     live: real post-only orders, fills read from Kalshi (the paper model is logged
     next to them so we can see how far reality is from the backtest),
  5. after close, grades each market from its result and appends one row to
     data/m15/markets-YYYY-MM-DD.csv (quoted-but-unfilled markets too: fill rate).

Safety rails:
  - real orders only when [m15] mode = "live" AND the series is in live_series AND the
    repo kill switch allows it (the TRADING_MODE variable, which the Actions trader
    records in data/trader_state.json "mode"; off -> cancel and stop, paper -> paper,
    live total-loss halt -> stop). If that file can't be read for 30 min, no real orders.
  - live money at risk (open positions + resting orders) <= pool_pct of the bankroll
    (the 25% the Actions strategies' 75% cap leaves free).
  - daily stop: m15 live P&L today (ET) <= -daily_loss_pct of the bankroll -> cancel,
    no new orders until tomorrow. Total stop: m15 live P&L since start <=
    -total_loss_pct -> halted until data/m15/state.json "halted" is cleared.
  - on start and on any stop, every resting order on these series is cancelled.
  - orders are post-only limit buys (never cross the spread), 1-25 contracts.
"""
from __future__ import annotations

import base64
import csv
import json
import logging
import math
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import kalshi, notify
from .kalshi_api import KalshiClient, OrderRejected, fill_qty
from .util import DATA_DIR, Config, Http, HttpError, parse_iso, read_json, write_json

log = logging.getLogger("wxbot")

M15_DIR = Path(os.environ.get("M15_DATA_DIR") or (DATA_DIR / "m15"))
STATE = M15_DIR / "state.json"
REPO = os.environ.get("GITHUB_REPO", "alexanderhabel22-commits/kalshi-weather-bot")
SWITCH_URL = f"https://raw.githubusercontent.com/{REPO}/main/data/trader_state.json"
# Claude's own fast switch for this bot alone: {"mode": "live" | "paper" | "off"}.
# Read every minute (no restart needed), so a commit to main stops it in ~5 min.
CONTROL_URL = f"https://raw.githubusercontent.com/{REPO}/main/m15_control.json"
BANKROLL_URL = f"https://raw.githubusercontent.com/{REPO}/main/data/bankroll.json"
COLS = ["ticker", "series", "mode", "close", "fav_side", "first_q", "last_q", "quotes",
        "contracts", "filled", "cost", "fee", "fill_at", "fill_ttc_s", "model_filled",
        "touched", "pulls", "spot_bps", "result", "won", "pnl"]

DEFAULTS = {
    "mode": "paper",
    "series": ["KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M", "KXBNB15M",
               "KXNEAR15M", "KXHYPE15M", "KXZEC15M", "KXSILVER15M", "KXCOPPER15M",
               "KXGOLD15M", "KXWTI15M", "KXBRENT15M", "KXNATGAS15M", "KXPLATINUM15M",
               "KXPALLADIUM15M"],
    "live_series": ["KXBTC15M", "KXSILVER15M", "KXCOPPER15M"],
    "contracts": 1,
    # one more contract when the favourite is in the extra band. Live (Nov 8) the bot bought its
    # 1 contract at ~73c early in nearly every window and then sat out the 80-87c stretch, which
    # earned +11c/contract in the backtest (70-79c +14c, 88-95c +4c).
    "extra_contracts": 0,
    "extra_min": 0.80,
    "extra_max": 0.87,
    "single_series": [],
    "min_fav": 0.71,
    "max_fav": 0.95,
    "stop_quote_s": 0,
    # bid only in the last N seconds of the window, per series. Backtest split (Nov 8):
    # silver/copper earn ~0c with 5-15 min left and +6c in the last 5 min; BTC earns at
    # every point (+9c at 5-10 min, +6c at 0-5 min).
    # BTC 600 (Nov 8 data check): the old BTC trade pull kept only the last 10,000 trades per
    # market, which covers ~5 min on average, so the 10-15 min bucket had just 10 markets and was
    # never really tested. Live agreed: 45 fills with 10-15 min left +1.5c/ct, 19 fills with
    # <=10 min left +9.5c/ct. So BTC bids only in the part of the window the data covers.
    # Alt coins (paper only): same pattern in the backtest (+5c last 5 min, ~-1 to -3c earlier),
    # and live paper agreed (Nov 7-8: 569 early fills -4.5c/ct, 11 late fills 10 won). So the
    # paper test now runs the last-5-minute version, the only one that could go live.
    "max_left_s": {"KXBTC15M": 600, "KXSILVER15M": 300, "KXCOPPER15M": 300,
                   "KXPLATINUM15M": 300, "KXGOLD15M": 300, "KXWTI15M": 300, "KXBRENT15M": 300,
                   "KXNATGAS15M": 300, "KXETH15M": 300, "KXSOL15M": 300,
                   "KXXRP15M": 300, "KXDOGE15M": 300, "KXBNB15M": 300, "KXNEAR15M": 300,
                   "KXHYPE15M": 300, "KXZEC15M": 300},
    "requote_gap_s": 5,
    "pool_pct": 0.25,
    "daily_loss_pct": 0.10,
    "window_pct": 0.07,              # max live $ at risk across markets closing at the same time
    "shard": 2,                     # Kalshi exchange shard of crypto + commodities markets
    "fund_s": 300,                  # top the shard up to the pool this often (live only)
    "cut_after_fills": 100,         # a live series below cut_below_ct per contract after this
    "cut_below_ct": -0.05,
    # edge-decay watch (Nov 8, Alex: "let's put safeguards in place"): over the most recent
    # decay_window contracts per series, below decay_alert_ct -> push an alert (still trades);
    # below cut_below_ct -> cut to paper, even if the all-time record is still positive.
    "decay_window": 300,
    "decay_alert_ct": 0.01,          # many real fills goes back to paper by itself
    "total_loss_pct": 0.125,
    "poll_s": 3.0,
    "discover_s": 60,
    "switch_s": 60,
    "switch_stale_s": 1800,
    "summary_hour": 21,
    "upload_s": 900,
    "maker_fee_rate": 0.0175,
    "rest_quote_s": 5,          # REST best-bid refresh (the websocket fills in between)
    "rest_tape_s": 15,          # REST trade tape per market (truth for paper fills)
    "sync_s": 5,                # REST order sync (websocket fills arrive in between)
    "min_step_s": 0.25,
    # jump guard: pull our bids when spot moves > guard_z x its usual 10-second move
    "guard": True,
    "require_guard_live": True,  # no real orders on a coin whose spot feed is down
    "guard_window_s": 10,
    "guard_z": 4.0,
    "guard_min_bps": 4.0,
    "guard_cooldown_s": 20,
    "spot": {"KXBTC15M": "BTC-USD", "KXETH15M": "ETH-USD", "KXSOL15M": "SOL-USD",
             "KXXRP15M": "XRP-USD", "KXDOGE15M": "DOGE-USD", "KXNEAR15M": "NEAR-USD",
             "KXZEC15M": "ZEC-USD"},
}


def settings(cfg: Config) -> dict:
    s = dict(DEFAULTS)
    s.update(cfg.raw.get("m15", {}))
    env = (os.environ.get("M15_MODE") or "").strip().lower()
    if env in ("off", "paper", "live"):
        s["mode"] = env
    return s


def maker_fee(p: float, n: int, rate: float) -> float:
    """Kalshi maker fee, rounded up to the cent per order."""
    return math.ceil(round(rate * n * p * (1 - p) * 100, 6)) / 100.0


def _num(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def parse_trade(t: dict) -> tuple[float, float, float, str] | None:
    """(unix ts, YES price $, contracts, taker side) or None."""
    yp = _num(t.get("yes_price_dollars"))
    if yp is None:
        yp = _num(t.get("yes_price"))
        if yp is not None and yp > 1:
            yp /= 100.0
    n = _num(t.get("count_fp"), _num(t.get("count")))
    dt = parse_iso(t.get("created_time"))
    side = (t.get("taker_side") or "").lower()
    if yp is None or n is None or dt is None or side not in ("yes", "no"):
        return None
    return dt.timestamp(), yp, n, side


def best_bids(m: dict) -> tuple[float | None, float | None]:
    yb = kalshi.price(m, "yes_bid")
    nb = kalshi.price(m, "no_bid")
    if nb is None:
        ya = kalshi.price(m, "yes_ask")
        nb = round(1 - ya, 2) if ya is not None else None
    return (yb if yb else None), (nb if nb else None)


def desired_quote(m: dict, s: dict) -> tuple[str, float] | None:
    """Join the favourite's best bid while it's in the 71-95c band."""
    for side, b in zip(("yes", "no"), best_bids(m)):
        if b is not None and s["min_fav"] - 1e-9 <= b <= s["max_fav"] + 1e-9:
            return side, round(b, 2)
    return None


def qty_str(x) -> int | float:
    """Whole numbers stay ints in the CSV (2, not 2.0); partial fills keep 2 decimals."""
    x = round(float(x or 0), 2)
    return int(x) if x == int(x) else x


def target_contracts(q: float | None, s: dict, series: str | None = None) -> int:
    """Contracts wanted in a market while the favourite's bid is at q. Series in
    single_series never take the extra contract (new live tests, Nov 9)."""
    n = int(s["contracts"])
    if series is not None and series in (s.get("single_series") or []):
        return n
    if q is not None and s.get("extra_contracts") and \
            s["extra_min"] - 1e-9 <= q <= s["extra_max"] + 1e-9:
        n += int(s["extra_contracts"])
    return n


def through(trades: list, side: str, q: float, after: float) -> tuple[float | None, bool]:
    """Back-of-queue paper fill for a resting bid on `side` at q placed at `after`:
    the first later trade where a longshot taker paid MORE than 1 - q (so every bid at
    q was used up). Also reports whether anything printed exactly at our level."""
    long_side = "no" if side == "yes" else "yes"
    lim = 1 - q
    touched = False
    for ts, yp, _n, tside in trades:
        if ts <= after or tside != long_side:
            continue
        p = yp if long_side == "yes" else 1 - yp
        if p > lim + 0.005:
            return ts, True
        if abs(p - lim) < 0.005:
            touched = True
    return None, touched


class Bot:
    def __init__(self, cfg: Config, http: Http, client: KalshiClient | None = None,
                 clock=time.time, fetch_json=None, uploader=None, kfeed=None, spot=None,
                 wake=None):
        self.cfg = cfg
        self.kfeed = kfeed
        self.spot = spot
        self.wake = wake
        self.rest_quotes: dict = {}
        self.http = http
        self.client = client
        self.clock = clock
        self.fetch_json = fetch_json or (lambda url: http.get_json(url, params={"t": int(clock())}))
        self.uploader = uploader
        self.tz = ZoneInfo(cfg.tz)
        st = read_json(STATE, {}) or {}
        self.state = {"markets": st.get("markets", {}), "halted": st.get("halted"),
                      "day_stop": st.get("day_stop"), "pnl": st.get("pnl", {}),
                      "summary_day": st.get("summary_day"), "started": st.get("started"),
                      "cut": st.get("cut", {}), "series": st.get("series", {}),
                      "reset_seen": st.get("reset_seen"), "fund_alerted": st.get("fund_alerted"),
                      "code_version": st.get("code_version"), "saved_at": st.get("saved_at")}
        self.last = {"discover": 0.0, "switch": 0.0, "upload": 0.0, "balance": 0.0,
                     "switch_ok": 0.0, "quotes": 0.0, "sync": 0.0}
        self.switch = {"mode": None, "halted": False}
        self.control = None
        self.balance = None
        self.paper_bankroll = None
        self.errors = 0
        self.mode_was = None

    # ------------------------------------------------------------ helpers

    @property
    def s(self) -> dict:
        return settings(self.cfg)

    def alert(self, title: str, body: str, priority: int = 3) -> None:
        try:
            notify.send(self.http, self.cfg, title, body, priority=priority, tags=["zap"])
        except Exception as exc:          # alerts must never stop the bot
            log.warning("alert failed: %s", exc)

    def save(self) -> None:
        self.state["saved_at"] = self.clock()
        write_json(STATE, self.state)

    @staticmethod
    def code_version() -> str:
        try:
            return subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent.parent,
                                  capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            return ""

    def startup_notice(self) -> None:
        """Push "started" only when it means something. A restart by the hourly updater (new code,
        bot was running a moment ago) is routine and only logged; a crash, reboot or first start
        still pushes (Alex, Nov 8: six "started" pushes in a day were all code updates)."""
        ver, prev, saved = self.code_version(), self.state.get("code_version"), self.state.get("saved_at")
        self.state["code_version"] = ver or prev
        body = (f"mode {self.s['mode']} · live series {', '.join(self.s['live_series'])} · "
                f"{self.s['contracts']} contract(s)/market")
        if ver and prev and ver != prev and saved and self.clock() - float(saved) < 600:
            log.info("restarted for a code update %s -> %s (no push): %s", prev[:7], ver[:7], body)
            return
        self.alert("15-min bot started", body, 2)

    def day(self, ts: float) -> str:
        return datetime.fromtimestamp(ts, self.tz).date().isoformat()

    # ------------------------------------------------------------ kill switch

    def refresh_switch(self, now: float) -> None:
        if now - self.last["switch"] < self.s["switch_s"]:
            return
        self.last["switch"] = now
        try:
            js = self.fetch_json(SWITCH_URL) or {}
            halted = js.get("halted") or {}
            self.switch = {"mode": (js.get("mode") or "").lower() or None,
                           "halted": bool(isinstance(halted, dict) and halted.get("live"))}
            self.last["switch_ok"] = now
        except Exception as exc:
            log.warning("kill switch read failed: %s", exc)
        try:
            c = self.fetch_json(CONTROL_URL) or {}
            self.control = (c.get("mode") or "").lower() or None
            if c.get("reset_halt") and self.state.get("halted"):
                if self.state.get("reset_seen") != c.get("reset_halt"):
                    self.state["reset_seen"] = c.get("reset_halt")
                    self.state["halted"] = None
                    self.state["pnl"]["halt_base"] = self.state["pnl"].get("live_total", 0.0)
                    log.warning("halt cleared by m15_control.json")
        except Exception as exc:
            log.info("control file: %s", exc)
        try:
            b = self.fetch_json(BANKROLL_URL) or {}
            self.paper_bankroll = _num(b.get("total")) or self.paper_bankroll
        except Exception:
            pass

    def effective_mode(self, now: float) -> str:
        s = self.s
        mode = s["mode"]
        if mode == "off" or self.switch["mode"] == "off" or self.control == "off":
            return "off"
        if mode == "live":
            if (self.state.get("halted") or self.switch["halted"] or self.switch["mode"] == "paper"
                    or self.control == "paper"):
                return "paper"
            if now - self.last["switch_ok"] > s["switch_stale_s"]:
                return "paper"
            if self.client is None:
                return "paper"
            if self.state.get("day_stop") == self.day(now):
                return "paper"
        return mode

    # ------------------------------------------------------------ money

    def bankroll(self, now: float) -> float | None:
        if self.client is not None and now - self.last["balance"] > 300:
            try:
                self.balance = self.client.balance()
                self.last["balance"] = now
            except Exception as exc:
                log.warning("balance: %s", exc)
        # the whole account (cash + every position, from the Actions bot's data/bankroll.json);
        # Kalshi's balance is cash only, which would shrink the pool as other strategies buy
        if self.paper_bankroll:
            return self.paper_bankroll
        if self.balance is not None:
            return self.balance + self.exposure("live")
        return None

    def fund_shard(self, now: float) -> float:
        """Kalshi keeps a separate cash balance per exchange shard, and API orders don't move
        cash for us (the app does). Keep this bot's shard funded up to the pool: cash there +
        live money at risk <= pool_pct of the account. Only moves cash between shards of
        this same account."""
        s = self.s
        if now - self.last.get("fund", 0.0) < s["fund_s"] or not hasattr(self.client, "shard_balances"):
            return 0.0
        self.last["fund"] = now
        try:
            bal = self.client.shard_balances()
        except Exception as exc:
            log.warning("shard balances: %s", exc)
            return 0.0
        b = self.bankroll(now) or 0
        dst = int(s["shard"])
        have = bal.get(dst, 0.0) + self.exposure("live")
        need = math.floor((s["pool_pct"] * b - have) * 100) / 100
        src_free = bal.get(0, 0.0)
        amt = min(need, src_free - 1.0)               # leave $1 on the main shard at least
        self.state["shards"] = {str(k): v for k, v in bal.items()}
        if amt < 1.0:
            return 0.0
        try:
            self.client.shard_transfer(amt, 0, dst)
        except Exception as exc:
            log.warning("shard transfer %.2f: %s", amt, exc)
            self.state["fund_error"] = str(exc)[:300]
            if not self.state.get("fund_alerted"):
                self.state["fund_alerted"] = True
                self.alert("15-min bot can't fund its shard",
                           f"Moving ${amt:.2f} to exchange {dst} failed: {str(exc)[:200]}", 4)
            return 0.0
        log.warning("moved $%.2f from exchange 0 to exchange %d (pool)", amt, dst)
        self.state["fund_error"] = None
        return amt

    def exposure(self, mode: str, close: int | None = None) -> float:
        tot = 0.0
        for m in self.state["markets"].values():
            if m.get("mode") != mode or m.get("graded") or (close is not None and m.get("close") != close):
                continue
            tot += m.get("cost", 0.0)
            o = m.get("order")
            if o and not m.get("closed"):
                tot += o["q"] * (o["n"] - o.get("filled", 0))
        return tot

    def check_stops(self, now: float) -> None:
        s = self.s
        b = self.bankroll(now)
        if not b:
            return
        today = self.day(now)
        pnl = self.state["pnl"]
        day_pnl = pnl.get("live_days", {}).get(today, 0.0)
        total = pnl.get("live_total", 0.0) - pnl.get("halt_base", 0.0)
        if total <= -s["total_loss_pct"] * b and not self.state.get("halted"):
            self.state["halted"] = {"at": int(now), "pnl": round(total, 2)}
            self.cancel_all("total loss stop")
            self.alert("15-min bot HALTED (total loss stop)",
                       f"Live P&L ${total:+.2f} hit -{s['total_loss_pct']:.0%} of ${b:.2f}. "
                       "No real orders until Claude/Alex clears it.", 4)
        elif day_pnl <= -s["daily_loss_pct"] * b and self.state.get("day_stop") != today:
            self.state["day_stop"] = today
            self.cancel_all("daily loss stop")
            self.alert("15-min bot paused for today",
                       f"Live P&L today ${day_pnl:+.2f} hit -{s['daily_loss_pct']:.0%} of ${b:.2f}.", 4)

    # ------------------------------------------------------------ markets

    def discover(self, now: float) -> None:
        if now - self.last["discover"] < self.s["discover_s"]:
            return
        self.last["discover"] = now
        for series in self.s["series"]:
            try:
                ms = kalshi.list_open_markets(self.http, series)
            except HttpError as exc:
                log.warning("list %s: %s", series, exc)
                continue
            for mk in ms:
                tk = mk.get("ticker")
                ct = parse_iso(mk.get("close_time"))
                if not tk or ct is None or ct.timestamp() <= now or tk in self.state["markets"]:
                    continue
                self.state["markets"][tk] = {"ticker": tk, "series": series,
                                             "close": int(ct.timestamp()), "mode": None,
                                             "order": None, "quotes": 0, "filled": 0,
                                             "cost": 0.0, "fee": 0.0, "trade_ts": 0,
                                             "model_filled": None, "touched": False,
                                             "pulls": 0, "tape_at": 0}
        if self.kfeed is not None:
            self.kfeed.watch([k for k, m in self.state["markets"].items() if m["close"] > now])

    def trades(self, m: dict) -> list:
        out, cursor = [], None
        for _ in range(5):
            params = {"ticker": m["ticker"], "limit": 1000, "min_ts": int(m["trade_ts"]) - 1}
            if cursor:
                params["cursor"] = cursor
            js = self.http.get_json(f"{kalshi.BASE}/markets/trades", params=params)
            for t in js.get("trades") or []:
                p = parse_trade(t)
                if p:
                    out.append(p)
            cursor = js.get("cursor")
            if not cursor:
                break
        out.sort()
        return out

    # ------------------------------------------------------------ orders

    def _place(self, m: dict, side: str, q: float, n: int, now: float) -> bool:
        o = {"side": side, "q": q, "n": n, "filled": 0, "t0": now, "id": None}
        if m["mode"] == "live":
            cid = f"m15-{m['ticker']}-{int(now)}"[:60]
            try:
                r = self.client.place_limit(m["ticker"], side, q, n, cid)
            except (HttpError, OrderRejected) as exc:
                # A retried POST can be refused ("post only cross", 5xx ...) after the first try
                # already placed the order. Look before calling it a reject, or that order rests
                # untracked and fills on top of the next one (Nov 9: WTI held 4, max is 2).
                try:
                    found = self.client.find_order(m["ticker"], cid) or {}
                except Exception:
                    found = {}
                if found.get("order_id"):
                    log.warning("place %s: %s, but the order exists: adopting it", m["ticker"], exc)
                    r = found
                else:
                    self._rejected(m, exc, now)
                    return False
            self.rejects = 0
            o["id"] = r.get("order_id")
            if not o["id"]:
                return False
        m["order"] = o
        m["quotes"] += 1
        m.setdefault("first_q", q)
        m["last_q"] = q
        m["fav_side"] = side
        return True

    def _rejected(self, m: dict, exc: Exception, now: float) -> None:
        log.warning("place %s: %s", m["ticker"], exc)
        m["retry_at"] = now + 3
        m["why"] = f"order rejected: {str(exc)[:200]}"
        self.state["last_reject"] = {"at": int(now), "ticker": m["ticker"], "error": str(exc)[:300]}
        if "post only cross" in str(exc):
            # benign race: the ask moved onto our price before the order landed. The next
            # step re-reads the book and requotes; don't count it toward the alert
            # (Nov 8: 5 of these in one fast window pushed a false "rejected" alert).
            return
        self.rejects = getattr(self, "rejects", 0) + 1
        if self.rejects == 5:
            self.alert("15-min bot: live orders rejected",
                       f"Kalshi rejected 5 orders in a row: {str(exc)[:300]}", 4)

    def _end_order(self, m: dict) -> bool:
        """Cancel the current order and book whatever filled. False if the cancel failed:
        the order is then kept (still tracked) so nothing new is placed on top of it."""
        o = m.get("order")
        if not o:
            return True
        if m["mode"] == "live" and o.get("id"):
            try:
                self.client.cancel(o["id"], m["ticker"])
            except HttpError as exc:
                log.warning("cancel %s: %s", o["id"], exc)
                try:
                    ko = self.client.get_order(o["id"])
                except HttpError:
                    return False
                if (ko.get("status") or "") not in ("canceled", "cancelled", "executed"):
                    # resting, pending or anything unknown: it may still fill, so keep
                    # tracking it rather than letting it rest as an orphan (Nov 9)
                    o["filled"] = max(o["filled"], fill_qty(ko, o["n"]))
                    self._book(m, o, None)
                    return False
            try:
                ko = self.client.get_order(o["id"])
                o["filled"] = max(o["filled"], fill_qty(ko, o["n"]))
            except HttpError as exc:
                log.warning("order %s: %s", o["id"], exc)
        self._book(m, o, None)
        self._remember(m, o)
        m["order"] = None
        return True

    @staticmethod
    def _remember(m: dict, o: dict) -> None:
        """Note what an ended order already booked. If it later turns up resting (a cancel
        that didn't take), the orphan sweep books only fills beyond this, never twice."""
        if o.get("id"):
            m.setdefault("ended", {})[o["id"]] = o.get("booked", 0)

    def _book(self, m: dict, o: dict, ts: float | None) -> None:
        """Move newly filled contracts of order `o` into the position."""
        new = o["filled"] - o.get("booked", 0)
        if new <= 0:
            return
        o["booked"] = o.get("booked", 0) + new
        m["filled"] += new
        m["cost"] = round(m["cost"] + new * o["q"], 4)
        m["fee"] = round(m["fee"] + maker_fee(o["q"], new, self.s["maker_fee_rate"]), 4)
        if not m.get("fill_at"):
            m["fill_at"] = int(ts or self.clock())
            prod = self.s["spot"].get(m["series"])
            if self.spot is not None and prod:
                mv = self.spot.move_bps(prod)
                if mv is not None:
                    # signed so that + = spot moved toward our side's win (assumes YES = up)
                    m["spot_bps"] = round(mv if m.get("fav_side") == "yes" else -mv, 1)

    def cancel_all(self, why: str) -> None:
        for m in self.state["markets"].values():
            if m.get("order") and m["mode"] == "live":
                self._end_order(m)
        log.warning("cancelled all live m15 orders: %s", why)

    def startup_cleanup(self) -> None:
        """Cancel any resting order on our series left over from a crash."""
        if self.client is None:
            return
        try:
            resting = self.client.resting_orders()
        except Exception as exc:
            log.warning("startup listing failed: %s", exc)
            return
        series = tuple(self.s["series"])
        for oid, o in resting.items():
            if (o.get("ticker") or "").startswith(series):
                try:
                    self.client.cancel(oid, o.get("ticker", ""))
                except HttpError as exc:
                    log.warning("startup cancel %s: %s", oid, exc)
        for m in self.state["markets"].values():
            o = m.get("order")
            if o and m["mode"] == "live" and o.get("id"):
                try:
                    o["filled"] = max(o["filled"], fill_qty(self.client.get_order(o["id"]), o["n"]))
                except HttpError:
                    pass
                self._book(m, o, None)
                self._remember(m, o)
                m["order"] = None

    def sync_live(self, force: bool = False) -> None:
        mine = [m for m in self.state["markets"].values()
                if m.get("mode") == "live" and m.get("order") and m["order"].get("id")]
        if self.client is None:
            return
        if self.kfeed is not None:
            for m in mine:
                o = m["order"]
                got = round(float(self.kfeed.fills.get(o["id"], 0)), 2)
                if got > o["filled"]:
                    o["filled"] = min(o["n"], got)
                    self._book(m, o, self.clock())
                if o["filled"] >= o["n"]:
                    m["order"] = None
            mine = [m for m in mine if m.get("order")]
        now = self.clock()
        if not force and now - self.last["sync"] < self.s["sync_s"]:
            return
        self.last["sync"] = now
        try:
            resting = self.client.resting_orders()
        except Exception as exc:
            log.warning("resting listing: %s", exc)
            return
        self.sweep_orphans(resting)
        for m in mine:
            o = m["order"]
            ko = resting.get(o["id"])
            gone = ko is None
            if gone:
                try:
                    ko = self.client.get_order(o["id"])
                except HttpError as exc:
                    log.warning("order %s: %s", o["id"], exc)
                    continue
            o["filled"] = max(o["filled"], fill_qty(ko, o["n"]))
            self._book(m, o, self.clock())
            if gone or o["filled"] >= o["n"]:
                m["order"] = None

    def sweep_orphans(self, resting: dict) -> None:
        """Cancel any resting order on our series that the bot isn't tracking, and book what
        it filled into its market so caps and P&L stay true. Belt and braces for any path
        that loses track of an order (retried POSTs, failed cancels)."""
        tracked = {m["order"]["id"] for m in self.state["markets"].values()
                   if m.get("order") and m["order"].get("id")}
        series = tuple(self.s["series"])
        for oid, ko in resting.items():
            tk = ko.get("ticker") or ""
            if oid in tracked or not tk.startswith(series):
                continue
            try:
                self.client.cancel(oid, tk)
                ko = self.client.get_order(oid)
            except HttpError as exc:
                log.warning("orphan %s: %s", oid, exc)
                continue
            m = self.state["markets"].get(tk)
            got = fill_qty(ko, 0)
            log.warning("cancelled untracked order %s on %s (%.2f filled)", oid, tk, got)
            self.state["orphans"] = int(self.state.get("orphans", 0)) + 1
            prior = (m or {}).get("ended", {}).get(oid, 0)
            new = round(max(0.0, got - prior), 2)
            if m is not None:
                m.setdefault("ended", {})[oid] = max(prior, got)
                if new > 0:
                    q = m.get("last_q") or 0.85
                    self._book(m, {"q": q, "n": new, "filled": new}, None)
            # Alex (Nov 9): no pings for harmless sweeps. Only when a stray actually filled
            # (real extra exposure), or if they pile up enough to mean something is wrong.
            got = new
            if got > 0 or self.state["orphans"] in (100, 500):
                self.alert("15-min bot: untracked order cancelled",
                           f"{tk}: an order the bot wasn't tracking was cancelled "
                           f"({got:.2f} had filled). Total so far: {self.state['orphans']}.", 3)

    # ------------------------------------------------------------ one market

    def tape_check(self, m: dict, force: bool = False) -> None:
        """Paper fills (and the paper model next to live orders) from the trade tape."""
        o = m.get("order")
        if not o or (m["mode"] != "paper" and m.get("model_filled") is not None):
            return
        now = self.clock()
        tr = []
        ws_ok = self.kfeed is not None and self.kfeed.healthy()
        if ws_ok:
            tr = self.kfeed.tape(m["ticker"], o["t0"])
        if force or not ws_ok or now - m.get("tape_at", 0) >= self.s["rest_tape_s"]:
            m["tape_at"] = now
            try:
                tr = sorted(set(tr) | set(self.trades(m)))
            except HttpError as exc:
                log.info("trades %s: %s", m["ticker"], exc)
        if tr:
            m["trade_ts"] = max(m["trade_ts"], tr[-1][0])
        ts, touched = through(tr, o["side"], o["q"], o["t0"])
        m["touched"] = m["touched"] or touched
        if ts is None:
            return
        if m.get("model_filled") is None:
            m["model_filled"] = int(ts)
        if m["mode"] == "paper":
            o["filled"] = o["n"]
            self._book(m, o, ts)
            m["order"] = None

    def fast_pull(self, now: float) -> int:
        """Spot just jumped: cancel resting bids on that coin first, before any REST housekeeping."""
        if not self.s["guard"] or self.spot is None:
            return 0
        n = 0
        for m in list(self.state["markets"].values()):
            prod = self.s["spot"].get(m["series"])
            if m.get("order") and m["close"] > now and prod and self.spot.blocked(prod, now):
                self._end_order(m)
                m["pulls"] += 1
                n += 1
        return n

    def manage(self, m: dict, quote: dict | None, mode: str, now: float) -> None:
        s = self.s
        left = m["close"] - now
        eligible = (mode == "live" and m["series"] in s["live_series"]
                    and m["series"] not in self.state.get("cut", {}))
        if m["mode"] is None and mode != "off":
            m["mode"] = "live" if eligible else "paper"
        elif m["mode"] == "paper" and eligible and not m["filled"] and not m.get("order"):
            m["mode"] = "live"          # went live mid-window: don't leave this market on paper
        self.tape_check(m)
        o = m.get("order")
        if mode == "off" or (m["mode"] == "live" and mode != "live"):
            if o:
                self._end_order(m)
            return
        want = desired_quote(quote, s) if quote else None
        m["why"] = "no quote" if not quote else ("out of band" if want is None else "ok")
        if quote:
            m["bid_ask"] = [quote.get("yes_bid_dollars"), quote.get("yes_ask_dollars")]
        target = target_contracts(want[1] if want else None, s, m["series"])
        room = target - m["filled"] - (o["n"] - o["filled"] if o else 0)
        if left <= s["stop_quote_s"] or left > float((s.get("max_left_s") or {}).get(m["series"], 1e9)):
            if o:
                self._end_order(m)
            return
        prod = s["spot"].get(m["series"]) if s["guard"] else None
        if prod and self.spot is not None and self.spot.blocked(prod):
            m["why"] = "jump guard"
            if o:
                self._end_order(m)                      # spot just jumped: pull, wait
                m["pulls"] += 1
            return
        if (prod and m["mode"] == "live" and s["require_guard_live"]
                and (self.spot is None or not self.spot.healthy())):
            m["why"] = "spot feed down"
            if o:
                self._end_order(m)
            return
        if o:
            if want is None:
                return                                   # keep resting (still in band)
            side, q = want
            if side != o["side"] or (q > o["q"] + 1e-9 and now - o["t0"] >= s["requote_gap_s"]):
                if not self._end_order(m):
                    return                               # cancel failed: never stack a 2nd order
                o = None
                room = target - m["filled"]
            else:
                return
        if want is None or room <= 0 or now < m.get("retry_at", 0):
            return
        n_new = int(room + 1e-9)                         # whole contracts only; a partial fill
        if n_new < 1:                                    # (e.g. 1.5 held of 2) leaves no room
            return
        side, q = want
        if m.get("fav_side") and side != m["fav_side"] and m["filled"]:
            return                                       # one side per market
        if m["mode"] == "live":
            b = self.bankroll(now) or 0
            if self.exposure("live") + q * n_new > s["pool_pct"] * b + 1e-9:
                m["why"] = f"pool full (bankroll {b:.2f})"
                return
            # markets closing together can all lose together (Nov 8 10:30 PM ET: BTC, silver and
            # copper all flipped). Never let everything open losing at once reach the daily stop:
            # open live $ + this order must fit in what's left before the stop. Up on the day =
            # more room; already down = less (Alex, Nov 9).
            day_pnl = self.state["pnl"].get("live_days", {}).get(self.day(now), 0.0)
            room_to_stop = s["daily_loss_pct"] * b + day_pnl
            if self.exposure("live") + q * n_new > room_to_stop - 0.01:
                m["why"] = f"daily-stop guard (room ${room_to_stop:.2f})"
                return
        m["trade_ts"] = max(m["trade_ts"], now - 2)
        self._place(m, side, q, n_new, now)

    # ------------------------------------------------------------ grading

    def grade(self, now: float) -> None:
        due = [m for m in self.state["markets"].values()
               if m["close"] <= now and not m.get("graded") and now >= m.get("grade_at", 0)]
        for m in due:
            m["grade_at"] = now + 20                  # results take a minute or two
            if m.get("order"):
                self.tape_check(m, force=True)  # last trades before the close
            if m.get("order"):
                if m["mode"] == "live":
                    self._end_order(m)
                m["order"] = None
            m["closed"] = True
        need = [m["ticker"] for m in due if m["quotes"]]
        res = {}
        if need:
            try:
                res = kalshi.get_markets(self.http, need)
            except Exception as exc:
                log.warning("grade fetch: %s", exc)
                return
        for m in due:
            r = (res.get(m["ticker"]) or {}).get("result") if m["quotes"] else ""
            if m["quotes"] and r not in ("yes", "no"):
                if now - m["close"] > 6 * 3600:
                    r = "void"
                else:
                    continue
            won = None
            pnl = 0.0
            if m["filled"] and r in ("yes", "no"):
                won = (r == m["fav_side"])
                pnl = round(m["filled"] * (1.0 if won else 0.0) - m["cost"] - m["fee"], 4)
            m.update({"graded": True, "result": r, "won": won, "pnl": pnl})
            if self.kfeed is not None:
                self.kfeed.forget(m["ticker"])
            if m["quotes"]:
                self.record(m)
            if m["filled"] and m["mode"] in ("paper", "live"):
                p = self.state["pnl"]
                p[f"{m['mode']}_total"] = round(p.get(f"{m['mode']}_total", 0.0) + pnl, 4)
                days = p.setdefault(f"{m['mode']}_days", {})
                d = self.day(m["close"])
                days[d] = round(days.get(d, 0.0) + pnl, 4)
                if m["mode"] == "live":
                    self.series_check(m, pnl)
        # forget graded markets after a day
        self.state["markets"] = {k: v for k, v in self.state["markets"].items()
                                 if not (v.get("graded") and now - v["close"] > 86400)}

    def series_check(self, m: dict, pnl: float) -> None:
        """Pre-registered cut rule: below cut_below_ct per contract after cut_after_fills real fills."""
        st = self.state.setdefault("series", {}).setdefault(m["series"], {"fills": 0, "pnl": 0.0})
        st["fills"] += m["filled"]
        st["pnl"] = round(st["pnl"] + pnl, 4)
        # rolling window of the most recent ~decay_window contracts, as [contracts, pnl] per market
        rec = st.setdefault("recent", [])
        rec.append([m["filled"], round(pnl, 4)])
        win = int(self.s.get("decay_window", 300))
        while len(rec) > 1 and sum(r[0] for r in rec) - rec[0][0] >= win:
            rec.pop(0)
        cut = self.state.setdefault("cut", {})
        if m["series"] in cut or st["fills"] < int(self.s["cut_after_fills"]):
            return
        per = st["pnl"] / st["fills"]
        rn = sum(r[0] for r in rec)
        rper = sum(r[1] for r in rec) / rn if rn else 0.0
        if rn >= win and rper < float(self.s["cut_below_ct"]):
            # edge died after a good start: the recent window alone is below the cut line
            per, why = rper, f"the last {rn} contracts"
        else:
            why = None
        if rn >= win and why is None and rper < float(self.s.get("decay_alert_ct", 0.01)):
            day = self.day(m["close"])
            if st.get("decay_alerted") != day:
                st["decay_alerted"] = day
                self.alert(f"15-min bot: {m['series']} edge fading",
                           f"last {rn} contracts {rper * 100:+.1f}c per contract (all-time "
                           f"{per * 100:+.1f}c over {st['fills']}). Still trading; Claude reviews.", 3)
        if why or per < float(self.s["cut_below_ct"]):
            cut[m["series"]] = {"at": self.day(m["close"]), "fills": st["fills"], "per_ct": round(per, 4)}
            self.alert(f"15-min bot: {m['series']} cut",
                       f"{per * 100:+.1f}c per contract over {why or str(st['fills']) + ' real fills'} (rule: below "
                       f"{float(self.s['cut_below_ct']) * 100:+.0f}c after {self.s['cut_after_fills']}). "
                       "It runs on paper from now on.", 4)

    def record(self, m: dict) -> None:
        path = M15_DIR / f"markets-{self.day(m['close'])}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        row = {
            "ticker": m["ticker"], "series": m["series"], "mode": m["mode"],
            "close": datetime.fromtimestamp(m["close"], timezone.utc).isoformat(timespec="seconds"),
            "fav_side": m.get("fav_side", ""), "first_q": m.get("first_q", ""),
            "last_q": m.get("last_q", ""), "quotes": m["quotes"], "contracts": self.s["contracts"],
            "filled": qty_str(m["filled"]), "cost": round(m["cost"], 2), "fee": round(m["fee"], 2),
            "fill_at": m.get("fill_at") or "",
            "fill_ttc_s": (m["close"] - m["fill_at"]) if m.get("fill_at") else "",
            "model_filled": 1 if m.get("model_filled") else 0,
            "touched": 1 if m.get("touched") else 0, "pulls": m.get("pulls", 0),
            "spot_bps": m.get("spot_bps", ""),
            "result": m.get("result", ""), "won": "" if m.get("won") is None else int(m["won"]),
            "pnl": round(m.get("pnl", 0.0), 2)}
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLS)
            if new:
                w.writeheader()
            w.writerow(row)

    # ------------------------------------------------------------ summary / upload

    def summary_text(self, day: str) -> str:
        path = M15_DIR / f"markets-{day}.csv"
        if not path.exists():
            return "No graded markets today."
        with open(path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        lines = []
        for mode in ("live", "paper"):
            rs = [r for r in rows if r["mode"] == mode]
            if not rs:
                continue
            fills = [r for r in rs if float(r["filled"] or 0)]
            n = round(sum(float(r["filled"]) for r in fills), 2)
            pnl = sum(float(r["pnl"] or 0) for r in fills)
            cpc = 100 * pnl / n if n else 0.0
            lines.append(f"{mode.upper()}: {len(fills)}/{len(rs)} markets filled, {n} contracts, "
                         f"${pnl:+.2f} ({cpc:+.1f}c/contract)")
            by = {}
            for r in fills:
                b = by.setdefault(r["series"], [0, 0.0])
                b[0] += float(r["filled"])
                b[1] += float(r["pnl"])
            for ser, (k, p) in sorted(by.items(), key=lambda x: -x[1][1]):
                lines.append(f"  {ser.replace('KX', '').replace('15M', '')}: {k} @ {100 * p / k:+.1f}c")
        p = self.state["pnl"]
        lines.append(f"All time: live ${p.get('live_total', 0):+.2f}, paper ${p.get('paper_total', 0):+.2f}")
        return "\n".join(lines)

    def daily(self, now: float) -> None:
        dt = datetime.fromtimestamp(now, self.tz)
        today = dt.date().isoformat()
        if dt.hour >= int(self.s["summary_hour"]) and self.state.get("summary_day") != today:
            self.state["summary_day"] = today
            self.alert("15-min bot today", self.summary_text(today))

    def upload(self, now: float, force: bool = False) -> None:
        if self.uploader is None:
            return
        # on the clock: ~2 min after each 15-min close (:02, :17, :32, :47) so results are graded
        per = float(self.s["upload_s"])
        slot = (now - 120) // per * per + 120
        if not force and self.last["upload"] >= slot:
            return
        self.last["upload"] = now
        status = {"at": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
                  "mode": self.effective_mode(now), "switch": self.switch, "control": self.control,
                  "halted": self.state.get("halted"), "day_stop": self.state.get("day_stop"), "cut": self.state.get("cut", {}), "series": self.state.get("series", {}),
                  "last_reject": self.state.get("last_reject"),
                  "shards": self.state.get("shards"), "fund_error": self.state.get("fund_error"),
                  "spot_ok": bool(self.spot is not None and self.spot.healthy()),
                  "live_now": {k: {"mode": m.get("mode"), "why": m.get("why"), "bid_ask": m.get("bid_ask"),
                                   "order": bool(m.get("order"))}
                               for k, m in self.state["markets"].items()
                               if m["series"] in self.s["live_series"] and not m.get("graded")},
                  "pnl": self.state["pnl"], "open_markets": sum(1 for m in self.state["markets"].values()
                                                                 if not m.get("graded")),
                  "live_exposure": round(self.exposure("live"), 2), "errors": self.errors}
        files = {"status.json": json.dumps(status, indent=1, sort_keys=True)}
        for d in {self.day(now), self.day(now - 86400)}:
            p = M15_DIR / f"markets-{d}.csv"
            if p.exists():
                files[p.name] = p.read_text(encoding="utf-8")
        try:
            self.uploader(files)
        except Exception as exc:
            log.warning("upload failed: %s", exc)

    # ------------------------------------------------------------ loop

    def quote(self, ticker: str, now: float) -> dict | None:
        """Freshest best bid/ask: the websocket's if newer than the last REST read."""
        q = self.rest_quotes.get(ticker)
        if q and now - q["at"] > 3 * self.s["rest_quote_s"] + 5:
            q = None
        w = self.kfeed.quote(ticker) if self.kfeed is not None else None
        if w and (q is None or w["at"] >= q["at"]):
            q = {**(q or {}), **w}
        if not q:
            return None
        return {"yes_bid_dollars": q.get("yes_bid"), "yes_ask_dollars": q.get("yes_ask")}

    def step(self) -> None:
        now = self.clock()
        self.fast_pull(now)
        self.refresh_switch(now)
        if self.state.get("started") is None:
            self.state["started"] = int(now)
        mode = self.effective_mode(now)
        if self.mode_was == "live" and mode != "live":
            self.cancel_all(f"mode now {mode}")
        if mode != self.mode_was:
            log.warning("m15 mode: %s", mode)
            self.mode_was = mode
        if self.client is not None:
            self.check_stops(now)
            mode = self.effective_mode(now)
            if mode == "live":
                self.fund_shard(now)
            self.sync_live()
        self.discover(now)
        live_open = [m for m in self.state["markets"].values() if m["close"] > now]
        if live_open and mode != "off" and now - self.last["quotes"] >= self.s["rest_quote_s"]:
            self.last["quotes"] = now
            try:
                got = kalshi.get_markets(self.http, [m["ticker"] for m in live_open])
                self.rest_quotes = {tk: {"yes_bid": kalshi.price(x, "yes_bid"),
                                         "yes_ask": kalshi.price(x, "yes_ask"), "at": now}
                                    for tk, x in got.items()}
            except Exception as exc:
                log.warning("quotes: %s", exc)
        for m in live_open:
            try:
                self.manage(m, self.quote(m["ticker"], now), mode, now)
            except Exception as exc:
                log.warning("manage %s: %s", m["ticker"], exc)
        self.grade(now)
        self.daily(now)
        self.upload(now)
        self.save()

    def run_forever(self) -> None:
        self.startup_cleanup()
        self.startup_notice()
        while True:
            t0 = self.clock()
            try:
                self.step()
                self.errors = 0
            except Exception as exc:
                self.errors += 1
                log.exception("m15 step failed (%d in a row): %s", self.errors, exc)
                if self.errors == 20:
                    self.alert("15-min bot is failing", f"20 steps in a row failed: {exc}", 4)
            spent = self.clock() - t0
            if self.wake is not None:
                self.wake.wait(max(0.0, float(self.s["poll_s"]) - spent))
                self.wake.clear()
                try:
                    self.fast_pull(self.clock())         # react to a jump right away
                except Exception as exc:
                    log.warning("fast pull: %s", exc)
                time.sleep(max(0.0, float(self.s["min_step_s"]) - (self.clock() - t0)))
            else:
                time.sleep(max(0.5, float(self.s["poll_s"]) - spent))


# ---------------------------------------------------------------- GitHub upload

def github_uploader(http: Http, token: str, repo: str = REPO, branch: str = "m15-data"):
    """Writes files to a data branch with the GitHub contents API (no git needed)."""
    api = f"https://api.github.com/repos/{repo}"
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    def ensure_branch():
        try:
            http.get_json(f"{api}/git/ref/heads/{branch}", headers=hdr)
        except HttpError as exc:
            if exc.status != 404:
                raise
            main = http.get_json(f"{api}/git/ref/heads/main", headers=hdr)
            http.post_json(f"{api}/git/refs", {"ref": f"refs/heads/{branch}",
                                                "sha": main["object"]["sha"]}, headers=hdr)

    def put(files: dict) -> None:
        ensure_branch()
        for name, text in files.items():
            url = f"{api}/contents/m15/{name}"
            sha = None
            try:
                sha = http.get_json(url, params={"ref": branch}, headers=hdr).get("sha")
            except HttpError as exc:
                if exc.status != 404:
                    raise
            body = {"message": f"m15: {name} [skip ci]", "branch": branch,
                    "content": base64.b64encode(text.encode()).decode()}
            if sha:
                body["sha"] = sha
            http._request("PUT", url, json=body, headers=hdr)
    return put


def client_from_env(http: Http) -> KalshiClient | None:
    path = os.environ.get("KALSHI_PRIVATE_KEY_FILE")
    if path and not os.environ.get("KALSHI_PRIVATE_KEY"):
        try:
            os.environ["KALSHI_PRIVATE_KEY"] = Path(path).read_text()
        except OSError as exc:
            log.warning("key file %s: %s", path, exc)
    return KalshiClient.from_env(http)


def main(cfg: Config, once: bool = False) -> int:
    http = Http(cfg.user_agent, min_interval=0.06)
    s = settings(cfg)
    client = client_from_env(http)          # also used (read-only) for the fast feed in paper
    if s["mode"] == "live" and client is None:
        log.warning("m15 mode live but no Kalshi key: paper only")
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    wake = threading.Event()
    kfeed = spot = None
    if not once:
        try:
            from .m15_feeds import KalshiFeed, SpotFeed
            if client is not None:
                kfeed = KalshiFeed(client, wake).start()
            prods = [s["spot"][x] for x in s["series"] if x in s["spot"]]
            if s["guard"] and prods:
                spot = SpotFeed(prods, wake, s["guard_window_s"], s["guard_z"],
                                s["guard_min_bps"], s["guard_cooldown_s"]).start()
        except ImportError as exc:
            log.warning("websockets not installed, polling only: %s", exc)
    bot = Bot(cfg, http, client, uploader=github_uploader(http, token) if token else None,
              kfeed=kfeed, spot=spot, wake=wake)
    if once:
        bot.step()
        bot.upload(time.time(), force=True)
        return 0
    bot.run_forever()
    return 0
