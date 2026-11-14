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

from . import kalshi, kalshi_api, notify, scaleup
from .m15_fillq import FillQuality
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
        "touched", "pulls", "spot_bps", "result", "won", "pnl", "fills", "fv_fill", "rule",
        "hour_move"]
# "fills" (Nov 10): each fill as contracts@price, ';'-separated (e.g. "1@0.77;1@0.85"), so a
# double's 1st and 2nd contract can be judged apart (the cost column only gives the blend).

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
    # Correlated markets (metals + oil move together). Once `group_cap` of them have filled in
    # the same window, the rest of the group stops bidding there (0 = off). Live replay Nov 10:
    # in windows where several metals filled, the FIRST to fill made +$7.29 on 162 contracts and
    # the later ones lost -$16.82 on 247 (they fill as the move spreads).
    "group": [],
    "group_cap": 0,
    "min_fav": 0.71,
    "series_min_fav": {},           # per-series floor above min_fav, e.g. {"KXSILVER15M": 0.80}
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
    "rolling_stop": False,          # True: the daily stop counts the last 24 hours, not the ET day
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
    "maker_fee_rate": 0.0,          # Kalshi charged $0 on every 15-min fill (ledger, Nov 10)
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
             "KXZEC15M": "ZEC-USD",
             # metals/oil via OANDA's practice stream (only when OANDA_TOKEN is set; Nov 10)
             "KXSILVER15M": "XAG_USD", "KXGOLD15M": "XAU_USD", "KXCOPPER15M": "XCU_USD",
             "KXPLATINUM15M": "XPT_USD", "KXPALLADIUM15M": "XPD_USD", "KXWTI15M": "WTICO_USD",
             "KXBRENT15M": "BCO_USD", "KXNATGAS15M": "NATGAS_USD"},
    # products that trip together: a jump in one pulls the bids on all (metals move as one)
    "guard_links": [["XAG_USD", "XAU_USD", "XCU_USD", "XPT_USD", "XPD_USD"]],
    # SHADOW fair value (wxbot/m15_fv.py, Nov 10): logs only, never trades. Rebuilds the
    # settlement index from free exchange feeds and logs fair value vs Kalshi's quotes.
    "fv": True,
    "fv_coins": {"KXBTC15M": "BTC", "KXETH15M": "ETH", "KXSOL15M": "SOL"},
    "fv_every_s": 5,
    "lag_probe": True,          # PAPER: real order book at each fair-value jump (m15_fv.LagProbe)
    # fair-value FILTER (changes trading when on): skip/pull the favourite bid when our side's
    # fair value < bid - margin, with >= min_left seconds left. OFF until the market's 500 review.
    "fv_skip": False,
    "fv_skip_series": ["KXBTC15M"],
    "fv_skip_margin": 0.0,
    "fv_skip_min_left_s": 180,
    "fv_skip_after_contracts": 0,     # >0: switch fv_skip on by itself at this many real contracts
    # EARLY WINDOW (rule E, pre-registered Nov 10 in reports/reviews/m15-candidate-rules-2026-11-10.md):
    # for early_series, bid only from early_start_s to early_end_s before close, favourite
    # early_min-early_max, no extra contract; a window without such a favourite by early_end_s is
    # skipped. 80-day replay: today's rule ~0c/ct since Oct 10, rule E +1.4c. Off unless early_on,
    # or switched on by itself at early_after_contracts real contracts in that series.
    "early_on": False,
    "early_series": ["KXBTC15M"],
    "early_start_s": 840,
    "early_end_s": 600,
    "early_min": 0.80,
    "early_max": 0.95,
    "early_after_contracts": 0,
    "early_contracts": 0,             # >0: contracts per window under rule E (0 = the usual base)
    # RULE E2 (Nov 13, Alex OK for real money at 1 ct): in these series, rule E bids ONLY when the
    # favourite bets AGAINST the index's move over the hour before the window (strike now vs the
    # strike of the window that opened 1 h earlier). Unknown move -> skip. 80-day replay +3.3c/ct
    # at the bid (1,216 windows, 10 of 13 weeks up) vs +1.1c with the trend. tools/e2_check.py.
    "e2_series": [],
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


def window_ticker(series: str, close_ts: int) -> str:
    """KXBTC15M-26NOV131430-30 for the window closing 2:30 PM ET Nov 13, 2026."""
    et = datetime.fromtimestamp(close_ts, ZoneInfo("America/New_York"))
    return f"{series}-{et.strftime('%y%b%d%H%M').upper()}-{et.strftime('%M')}"


def e2_ok(side: str, move: float | None) -> bool:
    """Rule E2: bid only when the favourite bets against the last hour's move."""
    if not move:
        return False
    return (side == "yes") == (move < 0)


def qty_str(x) -> int | float:
    """Whole numbers stay ints in the CSV (2, not 2.0); partial fills keep 2 decimals."""
    x = round(float(x or 0), 2)
    return int(x) if x == int(x) else x


def target_contracts(q: float | None, s: dict, series: str | None = None) -> int:
    """Contracts wanted in a market while the favourite's bid is at q. Series in
    single_series never take the extra contract (new live tests, Nov 9)."""
    base = int(s["contracts"])
    n = int((s.get("series_contracts") or {}).get(series, base)) if series is not None else base
    if series is not None and series in (s.get("single_series") or []):
        return n
    if q is not None and s.get("extra_contracts") and \
            s["extra_min"] - 1e-9 <= q <= s["extra_max"] + 1e-9:
        extra = int(s["extra_contracts"])
        if n != base and s.get("step_scales_extra", True) and base > 0:
            extra = extra * n // base              # scale-up steps: 1+1 -> 2+2 -> 4+4
        n += extra
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


class Bot(FillQuality):
    def __init__(self, cfg: Config, http: Http, client: KalshiClient | None = None,
                 clock=time.time, fetch_json=None, uploader=None, kfeed=None, spot=None,
                 wake=None, fv=None):
        self.cfg = cfg
        self.fv = fv                    # shadow fair value (logs only), m15_fv.FairShadow
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
                      "fv_skip_on": st.get("fv_skip_on", {}), "early_on": st.get("early_on", {}),
                      "code_version": st.get("code_version"), "saved_at": st.get("saved_at"),
                      "fillq": st.get("fillq", [])}
        self.last = {"discover": 0.0, "switch": 0.0, "upload": 0.0, "balance": 0.0,
                     "switch_ok": 0.0, "quotes": 0.0, "sync": 0.0, "queue": 0.0}
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

    def limits(self, now: float) -> dict:
        """Settings with the scale-up kit applied (wxbot/scaleup.py): tighter stops by bankroll
        tier and each live coin's contract step. While the kit is off this is just self.s."""
        s = self.s
        p = scaleup.params(self.cfg)
        if not p["enabled"]:
            return s
        b = self.bankroll(now)
        s, tr = scaleup.m15_limits(s, p, b)
        s["scaleup_tier"] = (tr or {}).get("name")
        s["series_contracts"] = {ser: scaleup.step_contracts(p, self.state.get("series", {}).get(ser), b,
                                                             int(s["contracts"]))
                                 for ser in s["live_series"]}
        s["step_scales_extra"] = p["step_scales_extra"]
        return s

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

    @staticmethod
    def fillq_dir() -> Path:
        return M15_DIR

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
            if self.state.get("roll_stop"):
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

    def stop_pnl(self, now: float) -> float:
        """Live P&L the daily stop counts: the last 24 hours (rolling_stop) or the ET day."""
        pnl = self.state["pnl"]
        if self.s.get("rolling_stop"):
            if "live_log" not in pnl:      # first run: seed with today's ET P&L
                pnl["live_log"] = [[now, pnl.get("live_days", {}).get(self.day(now), 0.0)]]
            log24 = [e for e in pnl.get("live_log", []) if e[0] > now - 86400]
            pnl["live_log"] = log24
            return round(sum(e[1] for e in log24), 4)
        return pnl.get("live_days", {}).get(self.day(now), 0.0)

    def check_stops(self, now: float) -> None:
        s = self.limits(now)
        b = self.bankroll(now)
        if not b:
            return
        today = self.day(now)
        pnl = self.state["pnl"]
        day_pnl = self.stop_pnl(now)
        total = pnl.get("live_total", 0.0) - pnl.get("halt_base", 0.0)
        if total <= -s["total_loss_pct"] * b and not self.state.get("halted"):
            self.state["halted"] = {"at": int(now), "pnl": round(total, 2)}
            self.cancel_all("total loss stop")
            self.alert("15-min bot HALTED (total loss stop)",
                       f"Live P&L ${total:+.2f} hit -{s['total_loss_pct']:.0%} of ${b:.2f}. "
                       "No real orders until Claude/Alex clears it.", 4)
        elif s.get("rolling_stop"):
            hit = day_pnl <= -s["daily_loss_pct"] * b
            if hit and not self.state.get("roll_stop"):
                self.state["roll_stop"] = {"at": int(now), "pnl": round(day_pnl, 2)}
                self.cancel_all("24h loss stop")
                self.alert("15-min bot paused (24h loss stop)",
                           f"Live P&L over the last 24h ${day_pnl:+.2f} hit -{s['daily_loss_pct']:.0%} of ${b:.2f}. "
                           "Resumes by itself once older losses roll out of the 24h window.", 4)
            elif not hit and self.state.get("roll_stop"):
                self.state["roll_stop"] = None
                self.alert("15-min bot resumed", f"Last-24h live P&L back to ${day_pnl:+.2f}.", 3)
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
                                             "pulls": 0, "tape_at": 0,
                                             "strike": mk.get("floor_strike")}
                if mk.get("floor_strike") is not None:
                    hist = self.state.setdefault("strikes", {}).setdefault(series, {})
                    hist[str(int(ct.timestamp()))] = float(mk["floor_strike"])
                    for old in sorted(hist, key=int)[:-12]:
                        del hist[old]
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
            lag = getattr(self.fv, "lag", None) if self.fv is not None else None
            if lag is not None:                  # shadow: is the cached quote stale vs the real book?
                try:
                    lag.place_check(self, m, side, q, n, now)
                except Exception as exc:
                    log.warning("place check %s: %s", m["ticker"], exc)
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
        if m["mode"] == "live":
            # give Kalshi's records a moment to settle before the next order on this market
            m["retry_at"] = max(m.get("retry_at", 0), self.clock() + 2)
        return True

    @staticmethod
    def _remember(m: dict, o: dict) -> None:
        """Note what an ended order already booked. If it later turns up resting (a cancel
        that didn't take), the orphan sweep books only fills beyond this, never twice."""
        if o.get("id"):
            m.setdefault("ended", {})[o["id"]] = o.get("booked", 0)

    @staticmethod
    def _has_fill_count(ko: dict) -> bool:
        return any(ko.get(k) not in (None, "") for k in ("fill_count_fp", "fill_count"))

    def _unbook(self, m: dict, o: dict, truth: float) -> None:
        """Take back contracts booked from the socket that Kalshi's order record doesn't show."""
        extra = round(o.get("booked", 0) - truth, 4)
        log.warning("%s: booked %.2f but Kalshi shows %.2f filled; correcting", m["ticker"],
                    o.get("booked", 0), truth)
        o["booked"] = truth
        o["filled"] = truth
        m["filled"] = round(max(0.0, m["filled"] - extra), 4)
        m["cost"] = round(max(0.0, m["cost"] - extra * o["q"]), 4)
        m["fee"] = round(max(0.0, m["fee"] - maker_fee(o["q"], extra, self.s["maker_fee_rate"])), 4)
        self.state["unbooked"] = round(float(self.state.get("unbooked", 0)) + extra, 2)

    def _book(self, m: dict, o: dict, ts: float | None) -> None:
        """Move newly filled contracts of order `o` into the position."""
        new = o["filled"] - o.get("booked", 0)
        if new <= 0:
            return
        o["booked"] = o.get("booked", 0) + new
        m["filled"] += new
        m["cost"] = round(m["cost"] + new * o["q"], 4)
        m.setdefault("fills", []).append(f"{qty_str(new)}@{o['q']:.2f}")
        m["fee"] = round(m["fee"] + maker_fee(o["q"], new, self.s["maker_fee_rate"]), 4)
        if not m.get("fill_at"):
            m["fill_at"] = int(ts or self.clock())
            prod = self.s["spot"].get(m["series"])
            if self.spot is not None and prod:
                mv = self.spot.move_bps(prod)
                if mv is not None:
                    # signed so that + = spot moved toward our side's win (assumes YES = up)
                    m["spot_bps"] = round(mv if m.get("fav_side") == "yes" else -mv, 1)
        self.fillq_open(m, o, new, ts)           # shadow fill-quality log (never trades)

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
                # a full fill seen on the socket is confirmed by the REST check below before
                # the order is dropped (Nov 10: palladium booked 3 of max 2 twice overnight)
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
            truth = fill_qty(ko, o["n"])
            if gone and self._has_fill_count(ko) and truth < o.get("booked", 0) - 1e-9:
                self._unbook(m, o, truth)                # final record shows fewer than we booked
            o["filled"] = max(o["filled"], truth)
            self._book(m, o, self.clock())
            # drop the order only when Kalshi's own record says it's done, not on a socket
            # count alone (a double-counted fill would leave the rest of it resting, untracked)
            if gone or truth >= o["n"]:
                self._remember(m, o)
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
        s = self.limits(now)
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
        early = self.early_active(m, now)
        floor = (s.get("series_min_fav") or {}).get(m["series"])
        if early:
            band = dict(s, min_fav=s["early_min"], max_fav=s["early_max"])
        elif floor is not None:
            band = dict(s, min_fav=max(float(s["min_fav"]), float(floor)))   # per-market floor (Nov 11)
        else:
            band = s
        want = desired_quote(quote, band) if quote else None
        m["why"] = "no quote" if not quote else ("out of band" if want is None else "ok")
        if early and m["series"] in (s.get("e2_series") or []):
            m["rule"] = "E2"
            mv = self.hour_move(m)
            m["hour_move"] = mv
            if want and not e2_ok(want[0], mv):
                want = None
                m["why"] = "E2: with the trend" if mv else "E2: last hour unknown"
                m["e2_skip"] = True
        if quote:
            m["bid_ask"] = [quote.get("yes_bid_dollars"), quote.get("yes_ask_dollars")]
        base_n = int((s.get("series_contracts") or {}).get(m["series"], s["contracts"]))
        if early and int(s.get("early_contracts") or 0) > 0:
            base_n = max(base_n, int(s["early_contracts"]))  # rule E size (Alex Nov 10: keep daily volume);
                                                             # a bigger scale-up step still wins
        target = base_n if early else target_contracts(want[1] if want else None, s, m["series"])
        if m["mode"] == "live":
            m["base"] = base_n
        room = target - m["filled"] - (o["n"] - o["filled"] if o else 0)
        if early:
            out = left < float(s["early_end_s"]) or left > float(s["early_start_s"])
        else:
            out = left > float((s.get("max_left_s") or {}).get(m["series"], 1e9))
        if left <= s["stop_quote_s"] or out:
            if o:
                self._end_order(m)
            return
        if self.group_full(m):
            m["why"] = "group cap"
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
                and (self.spot is None or not self.spot.healthy(prod))):
            m["why"] = "spot feed down"
            if o:
                self._end_order(m)
            return
        if self.fv_blocks(m, want, now):
            m["why"] = "fair value below bid"
            if o:
                self._end_order(m)                      # pull a resting bid the model now dislikes
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
            day_pnl = self.stop_pnl(now)
            room_to_stop = s["daily_loss_pct"] * b + day_pnl
            if s.get("stop_guard", True) and self.exposure("live") + q * n_new > room_to_stop - 0.01:
                m["why"] = f"daily-stop guard (room ${room_to_stop:.2f})"
                return
        if m["mode"] == "live" and self.client is not None and hasattr(self.client, "position"):
            # Kalshi's own position is the truth (Nov 10: fills the bot never saw left 3-4
            # contracts where it booked 2). Book anything unseen and re-check room next step.
            try:
                held = self.client.position(m["ticker"])
            except Exception as exc:
                log.warning("position %s: %s", m["ticker"], exc)
                m["why"] = "position check failed"
                return
            if held > m["filled"] + 0.01:
                extra = round(held - m["filled"], 2)
                log.warning("%s: Kalshi shows %.2f held, bot booked %.2f; booking the rest",
                            m["ticker"], held, m["filled"])
                self._book(m, {"q": m.get("last_q") or q, "n": extra, "filled": extra}, now)
                m["why"] = "caught unbooked fill"
                self.state["caught"] = round(float(self.state.get("caught", 0)) + extra, 2)
                return
        m["trade_ts"] = max(m["trade_ts"], now - 2)
        self._place(m, side, q, n_new, now)

    def early_active(self, m: dict, now: float) -> bool:
        """Rule E (BTC early window) on for this market's series? On when early_on, or by itself
        once the series reaches early_after_contracts real contracts (Alex Nov 10 7:56 PM ET:
        "just make them go live at 500"). Pushes one alert when it switches on."""
        s = self.s
        if m["series"] not in (s.get("early_series") or []):
            return False
        if s.get("early_on"):
            return True
        on = self.state.setdefault("early_on", {})
        if m["series"] in on:
            return True
        after = int(s.get("early_after_contracts") or 0)
        done = float((self.state.get("series", {}).get(m["series"]) or {}).get("fills", 0))
        if not after or done < after:
            return False
        if m.get("filled") or m.get("order"):
            return False                     # never switch rules in the middle of a window
        on[m["series"]] = int(now)
        self.alert(f"15-min bot: early-window rule ON for {m['series']}",
                   f"{done:.0f} real contracts reached (switch-on at {after}). Bids now only "
                   f"{int(s['early_start_s']) // 60}-{int(s['early_end_s']) // 60} min before close, "
                   f"favourite {s['early_min']:.2f}-{s['early_max']:.2f}, 1 contract per window.", 3)
        return True

    def hour_move(self, m: dict) -> float | None:
        """Index move over the hour before this window: its strike minus the strike of the window
        that opened 1 h earlier (strikes = index average at each open). Kalshi API on a miss."""
        hist = self.state.setdefault("strikes", {}).setdefault(m["series"], {})
        now_k, ago_k = str(int(m["close"])), str(int(m["close"]) - 3600)
        if m.get("strike") is not None:
            hist.setdefault(now_k, float(m["strike"]))
        for k in (now_k, ago_k):
            if k not in hist:
                try:
                    mk = kalshi.get_market(self.http, window_ticker(m["series"], int(k)))
                    if mk and mk.get("floor_strike") is not None:
                        hist[k] = float(mk["floor_strike"])
                except Exception as exc:
                    log.warning("hour move %s: %s", m["ticker"], exc)
        if now_k not in hist or ago_k not in hist:
            return None
        return round(hist[now_k] - hist[ago_k], 2)

    def fv_blocks(self, m: dict, want, now: float) -> bool:
        """Fair-value filter (OFF by default; Alex Nov 10 "ok then do them", switch on only at a
        market's 500 review). True = don't bid / pull our bid: our side's fair value (official CF
        index when its feed is up, else the rebuilt one) is below the bid by more than the margin.
        Only with >= fv_skip_min_left_s left (the model is worse than Kalshi's price late).
        Fails OPEN: no fair value -> behave exactly as before."""
        s = self.s
        if self.fv is None or not want or m["series"] not in (s.get("fv_skip_series") or []):
            return False
        if not s.get("fv_skip"):
            # auto switch-on at the market's Nth real contract (Alex Nov 10: "make them go live at 500")
            after = int(s.get("fv_skip_after_contracts") or 0)
            done = float((self.state.get("series", {}).get(m["series"]) or {}).get("fills", 0))
            if not after or done < after:
                return False
            on = self.state.setdefault("fv_skip_on", {})
            if m["series"] not in on:
                on[m["series"]] = int(now)
                self.alert(f"15-min bot: fair-value filter ON for {m['series']}",
                           f"{done:.0f} real contracts reached (switch-on at {after}). Bids are now "
                           "skipped when the fair price says they're overpriced (3+ min left).", 3)
        if m["close"] - now < float(s.get("fv_skip_min_left_s", 180)):
            return False
        try:
            fv = self.fv.fair_value(m, now)
        except Exception as exc:
            log.warning("fair value %s: %s", m["ticker"], exc)
            return False
        if not fv:
            return False
        y = fv["fv_cf"] if fv.get("fv_cf") is not None else fv["fv_yes"]
        side, q = want
        fair = y if side == "yes" else 1 - y
        if fair < q - float(s.get("fv_skip_margin", 0.0)) - 1e-9:
            m["fv_skips"] = int(m.get("fv_skips", 0)) + 1
            return True
        return False

    def group_full(self, m: dict) -> bool:
        """True when this market is in the correlated group, holds nothing yet, and enough
        other group markets closing at the same time already filled."""
        cap = int(self.s.get("group_cap") or 0)
        grp = self.s.get("group") or []
        if cap <= 0 or m["series"] not in grp or m.get("filled"):
            return False
        n = sum(1 for k in self.state["markets"].values()
                if k is not m and k["close"] == m["close"] and k["series"] in grp
                and k.get("mode") == m.get("mode") and k.get("filled", 0) > 0)
        return n >= cap

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
            if m["mode"] == "live" and m["quotes"]:
                self.true_up(m)
            won = None
            pnl = 0.0
            if m["filled"] and r in ("yes", "no"):
                won = (r == m["fav_side"])
                pnl = round(m["filled"] * (1.0 if won else 0.0) - m["cost"] - m["fee"], 4)
            m.update({"graded": True, "result": r, "won": won, "pnl": pnl})
            if self.kfeed is not None:
                self.kfeed.forget(m["ticker"])
            if m["quotes"] or m.get("e2_skip"):     # E2 skips get a row too (dashboard counts them)
                self.record(m)
            if m["filled"] and m["mode"] in ("paper", "live"):
                p = self.state["pnl"]
                p[f"{m['mode']}_total"] = round(p.get(f"{m['mode']}_total", 0.0) + pnl, 4)
                days = p.setdefault(f"{m['mode']}_days", {})
                d = self.day(m["close"])
                days[d] = round(days.get(d, 0.0) + pnl, 4)
                if m["mode"] == "live":
                    p.setdefault("live_log", []).append([m["close"], pnl])
                if m["mode"] == "live":
                    self.e2_check(m, pnl)
                    self.series_check(m, pnl)
        # forget graded markets after a day
        self.state["markets"] = {k: v for k, v in self.state["markets"].items()
                                 if not (v.get("graded") and now - v["close"] > 86400)}

    def true_up(self, m: dict) -> None:
        """Replace the booked contracts/cost/fee with Kalshi's own fills for this market."""
        if self.client is None or not hasattr(self.client, "fills"):
            return
        try:
            t = kalshi_api.fill_totals(self.client.fills(ticker=m["ticker"])).get(m["ticker"])
        except Exception as exc:
            log.warning("true-up %s: %s", m["ticker"], exc)
            m["unverified"] = True
            return
        n, cost, fee = t or (0.0, 0.0, 0.0)
        if abs(n - m["filled"]) > 0.01 or abs(cost - m["cost"]) > 0.01 or abs(fee - m["fee"]) > 0.005:
            log.warning("%s true-up: bot %.2f ct $%.2f fee %.2f -> Kalshi %.2f ct $%.2f fee %.2f",
                        m["ticker"], m["filled"], m["cost"], m["fee"], n, cost, fee)
            self.state["trued"] = int(self.state.get("trued", 0)) + 1
        m.update({"filled": n, "cost": round(cost, 4), "fee": round(fee, 4)})

    def retrue_history(self, version: int = 1) -> None:
        """One-time rewrite of every local markets CSV (live rows) and the P&L state from
        Kalshi's fills, then a full upload. Nov 10: the bot had booked a 1.75% fee Kalshi
        never charged and miscounted 29 of 683 markets (about -$7 logged vs +$8 real)."""
        if self.client is None or not hasattr(self.client, "fills") or self.state.get("retrued") == version:
            return
        try:
            tot = kalshi_api.fill_totals(self.client.fills(min_ts=1794000000, max_pages=200))
        except Exception as exc:
            log.warning("history true-up: %s", exc)
            return
        series = tuple(self.s["series"])
        p = self.state["pnl"]
        live_total, days, log24, ser = 0.0, {}, [], {}
        files = sorted(M15_DIR.glob("markets-*.csv"))
        for path in files:
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            for r in rows:
                if r.get("mode") != "live" or not r.get("ticker", "").startswith(series):
                    continue
                n, cost, fee = tot.get(r["ticker"], (0.0, 0.0, 0.0))
                r["filled"], r["cost"], r["fee"] = qty_str(n), round(cost, 2), round(fee, 2)
                res, side = r.get("result"), r.get("fav_side")
                if n and res in ("yes", "no"):
                    won = res == side
                    pnl = round(n * (1.0 if won else 0.0) - cost - fee, 4)
                    r["won"], r["pnl"] = int(won), round(pnl, 2)
                    close = datetime.fromisoformat(r["close"]).timestamp()
                    live_total += pnl
                    d = self.day(close)
                    days[d] = round(days.get(d, 0.0) + pnl, 4)
                    log24.append([close, pnl])
                    st = ser.setdefault(r["series"], {"fills": 0.0, "pnl": 0.0, "recent": []})
                    st["fills"] += n
                    st["pnl"] = round(st["pnl"] + pnl, 4)
                    st["recent"].append([n, round(pnl, 4)])
                elif not n:
                    r["won"], r["pnl"] = "", 0.0
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
                w.writeheader()
                w.writerows([{k: r.get(k, "") if r.get(k) is not None else "" for k in COLS} for r in rows])
        old = p.get("live_total", 0.0)
        p["live_total"] = round(live_total, 4)
        p["live_days"] = days
        p["live_log"] = [e for e in log24 if e[0] > self.clock() - 86400]
        p["halt_base"] = round(p.get("halt_base", 0.0) + (live_total - old), 4) if p.get("halt_base") else p.get("halt_base", 0.0)
        win = int(self.s.get("decay_window", 300))
        for k, v in ser.items():
            st = self.state.setdefault("series", {}).setdefault(k, {})
            rec = v["recent"]
            while len(rec) > 1 and sum(x[0] for x in rec) - rec[0][0] >= win:
                rec.pop(0)
            st.update({"fills": round(v["fills"], 2), "pnl": v["pnl"], "recent": rec})
        self.state["retrued"] = version
        self.state["roll_stop"] = None                 # re-judged on the corrected numbers
        log.warning("history true-up: live P&L $%.2f -> $%.2f over %d files", old, live_total, len(files))
        self.alert("15-min bot books corrected",
                   f"Re-read every real fill from Kalshi: all-time live P&L ${old:+.2f} -> ${live_total:+.2f} "
                   "(the bot had assumed a fee Kalshi doesn't charge and miscounted some fills).", 3)
        if self.uploader is not None:
            up = {path.name: path.read_text(encoding="utf-8") for path in files}
            try:
                self.uploader(up)
            except Exception as exc:
                log.warning("history upload failed: %s", exc)

    def e2_check(self, m: dict, pnl: float) -> None:
        """E2 money cap (Nov 13): real E2 trading goes back to paper once its real P&L (plus
        e2_loss_seed, the loss before this cap shipped) reaches -e2_stop_usd. Phone push."""
        cap = float(self.s.get("e2_stop_usd") or 0)
        if m.get("rule") != "E2" or not cap:
            return
        st = self.state.setdefault("e2_live", {"pnl": float(self.s.get("e2_loss_seed") or 0), "n": 0})
        st["pnl"] = round(st["pnl"] + pnl, 4)
        st["n"] += 1
        cut = self.state.setdefault("cut", {})
        # tighter one-session cap (Nov 13 evening, Alex: "needs to show some promise this evening"):
        # counted from 0 for each new e2_session tag; at -e2_session_stop_usd -> paper + push
        tag, scap = self.s.get("e2_session"), float(self.s.get("e2_session_stop_usd") or 0)
        if tag and scap:
            ss = self.state.get("e2_session")
            if not ss or ss.get("tag") != tag:
                ss = self.state["e2_session"] = {"tag": tag, "pnl": 0.0, "n": 0, "won": 0}
            ss["pnl"] = round(ss["pnl"] + pnl, 4)
            ss["n"] += 1
            ss["won"] += 1 if pnl > 0 else 0
            if ss["pnl"] <= -scap and m["series"] not in cut:
                cut[m["series"]] = {"at": self.day(m["close"]), "why": "E2 session cap", "e2_pnl": ss["pnl"]}
                self.alert(f"15-min bot: E2 tonight's cap hit, {m['series']} back to PAPER",
                           f"Real E2 since tonight's restart: ${ss['pnl']:+.2f} over {ss['n']} windows "
                           f"(cap -${scap:.2f}). Paper keeps running.", 4)
                return
        if st["pnl"] <= -cap and m["series"] not in cut:
            cut[m["series"]] = {"at": self.day(m["close"]), "why": "E2 money cap", "e2_pnl": st["pnl"]}
            self.alert(f"15-min bot: E2 money cap hit, {m['series']} back to PAPER",
                       f"Real E2 is at ${st['pnl']:+.2f} over {st['n']} windows (cap -${cap:.0f}). "
                       "No more real orders on it; paper keeps running. Claude reviews.", 4)

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
        # scale-up kit: the coin's record at its contract step (kept even while the kit is off)
        p = scaleup.params(self.cfg)
        now = self.clock()
        moved = scaleup.record_step(p, st, m["filled"], pnl, now, self.bankroll(now), m["series"] in cut)
        if moved:
            n = scaleup.step_contracts(p, st, self.bankroll(now), int(self.s["contracts"]))
            self.alert(f"15-min bot: {m['series']} steps {moved} to {n} contracts",
                       f"Scale-up kit: {'passed' if moved == 'up' else 'failed'} its step test "
                       f"({st['fills']:.0f} real contracts all-time). Pool and stops unchanged.", 4)
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
            "last_q": m.get("last_q", ""), "quotes": m["quotes"], "contracts": m.get("base", self.s["contracts"]),
            "filled": qty_str(m["filled"]), "cost": round(m["cost"], 2), "fee": round(m["fee"], 2),
            "fill_at": m.get("fill_at") or "",
            "fill_ttc_s": (m["close"] - m["fill_at"]) if m.get("fill_at") else "",
            "model_filled": 1 if m.get("model_filled") else 0,
            "touched": 1 if m.get("touched") else 0, "pulls": m.get("pulls", 0),
            "spot_bps": m.get("spot_bps", ""),
            "result": m.get("result", ""), "won": "" if m.get("won") is None else int(m["won"]),
            "pnl": round(m.get("pnl", 0.0), 2), "fills": ";".join(m.get("fills") or []),
            "fv_fill": m.get("fv_fill", ""), "rule": m.get("rule", ""),
            "hour_move": "" if m.get("hour_move") is None else m["hour_move"]}
        if not new:
            with open(path, newline="", encoding="utf-8") as f:
                rd = csv.DictReader(f)
                if rd.fieldnames != COLS:          # older header (e.g. before "fills"): upgrade in place
                    old = list(rd)
                else:
                    old = None
            if old is not None:
                with open(path, "w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
                    w.writeheader()
                    w.writerows([{k: r.get(k) or "" for k in COLS} for r in old])
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
                  "halted": self.state.get("halted"), "day_stop": self.state.get("day_stop"),
                  "roll_stop": self.state.get("roll_stop"),
                  "pnl_24h": self.stop_pnl(now) if self.s.get("rolling_stop") else None, "cut": self.state.get("cut", {}), "series": self.state.get("series", {}),
                  "last_reject": self.state.get("last_reject"),
                  "fv_skip_on": self.state.get("fv_skip_on", {}),
                  "early_on": self.state.get("early_on", {}),
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
            for kind in ("markets", "fills"):
                p = M15_DIR / f"{kind}-{d}.csv"
                if p.exists():
                    files[p.name] = p.read_text(encoding="utf-8")
        fv_files = {}
        if self.fv is not None:
            try:
                fv_files = self.fv.files(now)
            except Exception as exc:
                log.warning("fair-value files: %s", exc)
        files.update(fv_files)
        try:
            self.uploader(files)
            if fv_files:
                self.fv.mark_uploaded(fv_files)
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
        if self.fv is not None:
            try:
                self.fv.step(self, now)          # shadow only: logs, never trades
            except Exception as exc:
                log.warning("fair-value shadow: %s", exc)
        self.queue_check(now)                    # shadow fill-quality log (never trades)
        self.grade(now)
        self.fillq_step(now)
        self.daily(now)
        self.upload(now)
        self.save()

    def run_forever(self) -> None:
        self.startup_cleanup()
        self.retrue_history()
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
            from .m15_feeds import KalshiFeed, OandaFeed, SpotFeed, Spots
            if client is not None:
                kfeed = KalshiFeed(client, wake).start()
            prods = sorted({s["spot"][x] for x in s["series"] if x in s["spot"]})
            coin = [p for p in prods if "_" not in p]          # Coinbase: BTC-USD ...
            oanda = [p for p in prods if "_" in p]             # OANDA: XAG_USD ...
            otoken = os.environ.get("OANDA_TOKEN", "").strip()
            if s["guard"] and (coin or (oanda and otoken)):
                args = (wake, s["guard_window_s"], s["guard_z"], s["guard_min_bps"],
                        s["guard_cooldown_s"])
                feeds = [SpotFeed(coin, *args).start() if coin else None,
                         OandaFeed(otoken, oanda, *args,
                                   env=os.environ.get("OANDA_ENV", "practice")).start() if oanda and otoken else None]
                spot = Spots(feeds, s.get("guard_links") or [])
        except ImportError as exc:
            log.warning("websockets not installed, polling only: %s", exc)
    fv = None
    coins = {k: v for k, v in (s.get("fv_coins") or {}).items() if k in s["series"]}
    if not once and s.get("fv") and coins:
        try:
            from .m15_fv import Composite, FairShadow
            fv = FairShadow(Composite(coins.values()).start(client=client), coins, M15_DIR,
                            every_s=float(s.get("fv_every_s", 5)), lag=bool(s.get("lag_probe", True)))
        except Exception as exc:               # shadow only: never stop the bot over it
            log.warning("fair-value shadow off: %s", exc)
    bot = Bot(cfg, http, client, uploader=github_uploader(http, token) if token else None,
              kfeed=kfeed, spot=spot, wake=wake, fv=fv)
    if once:
        bot.step()
        bot.upload(time.time(), force=True)
        return 0
    bot.run_forever()
    return 0
