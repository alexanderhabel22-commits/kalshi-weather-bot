"""Copycat real-money mode (Nov 10, 2026). Off unless copycat_control.json on main says "live".

When a followed account whose verdict is "passes" makes a new trade, buy the same side of the
same market right away (immediate-or-cancel, never resting), at most a couple of cents worse than
the price they got. Rails, all checked before every order:
  - copycat_control.json mode "live", the global kill switch (TRADING_MODE) not off/halted,
    the control file read within the last 10 minutes, and a Kalshi key present;
  - the trade was seen within MAX_LAG_S of when it happened; 5-95c only;
  - one copy per market (we never add to a market we already hold, including markets another
    bot of ours holds), and never the 15-min bot's live series;
  - size from the account's grade (1-3 contracts) and the hard client ceiling;
  - money at risk in open copies <= pool_pct of the bankroll;
  - each account has a hard total-at-risk limit (Alex, Nov 14): its settled real losses plus the cost
    of its open real copies never exceed account_stop_usd ($5; or account_stop_pct of the bankroll if
    smaller), plus any real profit it has banked for us. A copy that would pass it is skipped; settled losses alone at the limit send the
    account back to paper. One copy per event (game) as well. Copycat as a whole halts at
    the smaller of total_stop_pct and total_stop_usd ($8). Free cash on the main index never drops
    below cash_floor_usd ($10), so the main bot is never starved. The boss (copycat_boss.py) and copycat_control.json "bench" can bench anyone.
Every order and settlement is written to the hourly log and to live_state.json.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Callable

from .kalshi_api import fill_qty

log = logging.getLogger("wxbot")
REPO = "alexanderhabel22-commits/kalshi-weather-bot"
CONTROL_URL = f"https://raw.githubusercontent.com/{REPO}/main/copycat_control.json"
SWITCH_URL = f"https://raw.githubusercontent.com/{REPO}/main/data/trader_state.json"
BANKROLL_URL = f"https://raw.githubusercontent.com/{REPO}/main/data/bankroll.json"
DEFAULTS = {"mode": "off", "pool_pct": 0.15, "account_stop_pct": 0.10, "account_stop_usd": 5.0, "total_stop_pct": 0.25, "total_stop_usd": 8.0, "cash_floor_usd": 10.0,
            "max_contracts": 3, "slip": 0.02, "max_lag_s": 60, "min_px": 0.05, "max_px": 0.95,
            "exclude_series": ["KXBTC15M", "KXSILVER15M"], "exclude_categories": ["Sports"], "bench": [],
            "keep": [], "keep_until": None}  # keep_until "YYYY-MM-DD" (ET): the keep list's real-results test expires     # accounts Alex keeps on real money whatever their score (inside their $ limit)
CONTROL_STALE_S = 600
EARN_N = 50                 # earn-back: new settled paper copies needed since the bench
EARN_SCORE = 80.0           # ... at this score or better, with a positive average
EARN_FINAL_S = 30 * 86400   # a second bench within 30 days of coming back is final for 30 days


def event_of(ticker: str) -> str:
    """A market's event (e.g. one game): the ticker without its last part."""
    return ticker.rsplit("-", 1)[0] if ticker.count("-") >= 2 else ticker


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


class Live:
    def __init__(self, out_dir: Path, client=None, fetch_json: Callable[[str], dict] | None = None,
                 clock=time.time, alert: Callable[[str, str], None] | None = None,
                 write: Callable[[dict], None] | None = None):
        self.out = out_dir
        self.client = client
        self.fetch_json = fetch_json
        self.clock = clock
        self.alert = alert or (lambda t, b: None)
        self.write = write or (lambda r: None)
        self.cfg = dict(DEFAULTS)
        self.switch = {"mode": None, "halted": False}
        self.bankroll = None
        self.read_at = 0.0
        self.ok_at = 0.0
        self._cash: tuple[float, float] | None = None
        p = self.out / "live_state.json"
        try:
            self.state = json.loads(p.read_text())
        except (OSError, ValueError):
            self.state = {}
        self.state.setdefault("positions", {})     # ticker -> position
        self.state.setdefault("pnl", {})           # nick -> realized $
        self.state.setdefault("total", 0.0)
        self.state.setdefault("benched", {})       # nick -> reason (own stop)
        self.state.setdefault("halted", None)
        self.state.setdefault("bench_log", {})     # nick -> {at, back_at, final_until} (earn-back, Nov 14)
        self.state.setdefault("pnl_hist", {})      # nick -> real $ from before its last earn-back

    def save(self) -> None:
        (self.out / "live_state.json").write_text(json.dumps(self.state, indent=1))

    # -- switches
    def refresh(self, now: float, force: bool = False) -> None:
        if not self.fetch_json or (not force and now - self.read_at < 60):
            return
        self.read_at = now
        try:
            c = self.fetch_json(CONTROL_URL) or {}
            self.cfg = {**DEFAULTS, **{k: v for k, v in c.items() if k in DEFAULTS}}
            self.cfg["mode"] = str(self.cfg.get("mode") or "off").lower()
            sw = self.fetch_json(SWITCH_URL) or {}
            halted = sw.get("halted") or {}
            self.switch = {"mode": (sw.get("mode") or "").lower() or None,
                           "halted": bool(isinstance(halted, dict) and halted.get("live"))}
            b = self.fetch_json(BANKROLL_URL) or {}
            self.bankroll = _f(b.get("total"), self.bankroll)
            self.ok_at = now
        except Exception as exc:  # noqa: BLE001
            log.warning("copycat control read: %s", exc)

    def mode(self, now: float) -> str:
        if self.cfg.get("mode") != "live" or self.client is None or not self.bankroll:
            return "off"
        if self.switch["mode"] in ("off", "paper") or self.switch["halted"] or self.state.get("halted"):
            return "off"
        if now - self.ok_at > CONTROL_STALE_S:
            return "off"
        return "live"

    def benched(self) -> dict:
        b = dict(self.state["benched"])
        for n in self.cfg.get("bench") or []:
            b.setdefault(n, "benched in copycat_control.json")
        return b

    def open_cost(self) -> float:
        return sum(p["cost"] for p in self.state["positions"].values() if not p.get("settled"))

    def account_limit(self, nick: str | None = None) -> float | None:
        """Hard $ limit per account: the smaller of account_stop_usd and account_stop_pct of bankroll.
        With nick: plus the real profit that account has banked for us ("earned room", Alex Nov 14:
        more leeway for traders who made us actual money). The shared pool still bounds it."""
        c, b = self.cfg, self.bankroll or 0
        lims = [x for x in (c["account_stop_pct"] * b if b else None, _f(c.get("account_stop_usd"))) if x is not None]
        if not lims:
            return None
        return min(lims) + (max(0.0, self.state["pnl"].get(nick, 0.0)) if nick else 0.0)

    def at_risk(self, nick: str) -> float:
        """Settled real losses on this account plus the cost of its open real copies."""
        lost = max(0.0, -self.state["pnl"].get(nick, 0.0))
        return lost + sum(p["cost"] for p in self.state["positions"].values()
                          if p.get("nick") == nick and not p.get("settled"))

    # -- copying
    def maybe_copy(self, acct: dict | None, fill: dict, snap: dict, now: float) -> str:
        """Returns what happened ('copied', or why not). acct is the account's status row."""
        c = self.cfg
        nick, ticker, side = fill.get("nick"), fill.get("ticker") or "", fill.get("side")
        if self.mode(now) != "live":
            return "off"
        if not acct or acct.get("verdict") != "passes":
            return "not passing"
        if nick in self.benched():
            return "benched"
        if side not in ("yes", "no") or fill.get("yes_price") is None:
            return "bad fill"
        if (fill.get("lag_s") or 0) > c["max_lag_s"]:
            return "too late"
        if any(ticker.startswith(s + "-") or ticker == s for s in c["exclude_series"]):
            return "excluded series"
        if snap.get("category") == "unknown" or snap.get("category") in (c.get("exclude_categories") or []):
            return f"excluded category ({snap.get('category')})"
        if ticker in self.state["positions"]:
            return "already copied this market"
        ev = event_of(ticker)
        if any(not p.get("settled") and event_of(t) == ev for t, p in self.state["positions"].items()):
            return "already copied this event"
        bid, ask = snap.get("bid"), snap.get("ask")
        if bid is None or ask is None:
            return "no quote"
        their = fill["yes_price"] if side == "yes" else 1 - fill["yes_price"]
        pay = ask if side == "yes" else round(1 - bid, 2)
        limit = round(min(their + c["slip"], c["max_px"]), 2)
        if pay > limit:
            return f"price ran ({round(pay * 100)}c > {round(limit * 100)}c)"
        if pay < c["min_px"]:
            return "too cheap"
        n = int(max(1, min(int(acct.get("size") or 1), int(c["max_contracts"]))))
        if self.open_cost() + limit * n > c["pool_pct"] * self.bankroll:
            n = 1
            if self.open_cost() + limit > c["pool_pct"] * self.bankroll:
                return "pool full"
        floor = _f(c.get("cash_floor_usd"))
        if floor:                               # never starve the main bot's orders (Nov 14)
            cash = self.free_cash(now)
            if cash is None:
                return "cash unknown"
            if cash - limit * n < floor:
                n = 1
                if cash - limit < floor:
                    return f"cash floor (${cash:.2f} free, keep ${floor:.0f} for the main bot)"
        lim = self.account_limit(nick)
        if lim is not None and self.at_risk(nick) + limit * n > lim:
            n = 1
            if self.at_risk(nick) + limit > lim:
                return f"trader limit (${lim:.2f} at risk)"
        try:
            if abs(self.client.position(ticker)) > 0:
                return "we already hold this market"
        except Exception as exc:  # noqa: BLE001
            return f"position check failed: {exc}"
        coid = f"cc-{int(now)}-{abs(hash((nick, fill.get('trade_id')))) % 10**8}"
        try:
            o = self.client.buy_now(ticker, side, limit, n, coid)
        except Exception as exc:  # noqa: BLE001
            self.write({"kind": "order", "nick": nick, "ticker": ticker, "side": side, "n": n,
                        "limit": limit, "error": str(exc)[:300], "trade_id": fill.get("trade_id")})
            return f"order failed: {str(exc)[:80]}"
        got = fill_qty(o, n)
        cost = _f(o.get("taker_fill_cost_dollars")) or got * pay
        fee = _f(o.get("taker_fees_dollars"))
        if fee is None:
            import math
            fee = math.ceil(round(7 * got * pay * (1 - pay), 6)) / 100 if got else 0.0
        self.write({"kind": "order", "nick": nick, "ticker": ticker, "side": side, "n": n, "filled": got,
                    "limit": limit, "cost": round(cost, 4), "fee": round(fee, 4),
                    "trade_id": fill.get("trade_id"), "order_id": o.get("order_id")})
        if got <= 0:
            return "no fill"
        self.state["positions"][ticker] = {"nick": nick, "side": side, "n": got, "cost": round(cost + fee, 4),
                                           "ts": now, "trade_id": fill.get("trade_id")}
        self.save()
        return "copied"

    # -- settlement and stops
    def settle(self, results: dict[str, str], now: float) -> list[dict]:
        done = []
        for t, p in self.state["positions"].items():
            if p.get("settled") or results.get(t) not in ("yes", "no"):
                continue
            pnl = round((p["n"] if results[t] == p["side"] else 0.0) - p["cost"], 4)
            p.update(settled=now, result=results[t], pnl=pnl)
            nick = p["nick"]
            self.state["pnl"][nick] = round(self.state["pnl"].get(nick, 0.0) + pnl, 4)
            self.state["total"] = round(self.state["total"] + pnl, 4)
            self.write({"kind": "settle", "nick": nick, "ticker": t, "pnl": pnl, "result": results[t]})
            done.append(dict(p, ticker=t))
        if done:
            self.check_stops(now)
            self.save()
        # keep the file small: drop settled positions older than 30 days
        old = [t for t, p in self.state["positions"].items() if p.get("settled") and now - p["settled"] > 30 * 86400]
        for t in old:
            self.state["positions"].pop(t)
        return done

    def free_cash(self, now: float) -> float | None:
        """Available cash on the main exchange index (index 0), read at most every 30 s."""
        if self._cash is not None and now - self._cash[0] < 30:
            return self._cash[1]
        try:
            sh = self.client.shard_balances() if hasattr(self.client, "shard_balances") else {}
            cash = sh.get(0) if sh else self.client.balance()
        except Exception as exc:  # noqa: BLE001
            log.warning("copycat cash read: %s", exc)
            return None
        self._cash = (now, float(cash))
        return self._cash[1]

    def total_limit(self) -> float | None:
        """Copycat's total real loss halt: the smaller of total_stop_pct of bankroll and total_stop_usd."""
        b, c = self.bankroll or 0, self.cfg
        lims = [x for x in (c["total_stop_pct"] * b if b else None, _f(c.get("total_stop_usd"))) if x is not None]
        return min(lims) if lims else None

    # -- earn-back (Alex, Nov 14): a benched trader keeps paper copying and can earn real money back
    def bench(self, nick: str, reason: str, now: float) -> None:
        """Real-money bench (own $ stop or real-lag). A second bench within EARN_FINAL_S of coming
        back is final for EARN_FINAL_S."""
        log_ = self.state["bench_log"].setdefault(nick, {})
        back = log_.get("back_at")
        log_.update(at=now, final_until=now + EARN_FINAL_S if back and now - back < EARN_FINAL_S else None)
        self.state["benched"][nick] = reason
        self.save()

    def earn_back_note(self, nick: str) -> str:
        fu = (self.state["bench_log"].get(nick) or {}).get("final_until")
        if fu:
            return "second bench within 30 days, so no earn-back for 30 days"
        return f"it can earn real money back after {EARN_N} new settled paper copies at score {EARN_SCORE:.0f}+ and a positive average"

    def earn_back_open(self, now: float) -> dict:
        """{nick: bench time} for benched traders allowed to earn back now."""
        out = {}
        for nick in self.state["benched"]:
            lg = self.state["bench_log"].get(nick)
            if lg and lg.get("at") and not (lg.get("final_until") and now < lg["final_until"]):
                out[nick] = lg["at"]
        return out

    def unbench(self, nick: str, now: float) -> None:
        """Back on real money with a fresh $ limit (its old real P&L moves to pnl_hist)."""
        self.state["benched"].pop(nick, None)
        lg = self.state["bench_log"].setdefault(nick, {})
        lg.update(back_at=now, final_until=None)
        self.state["pnl_hist"][nick] = round(self.state["pnl_hist"].get(nick, 0.0) + self.state["pnl"].get(nick, 0.0), 4)
        self.state["pnl"][nick] = 0.0
        self.save()

    def check_stops(self, now: float) -> None:
        lim = self.account_limit()               # hard $ stop per account (Nov 14), whichever is smaller
        for nick, pnl in self.state["pnl"].items():
            if lim is not None and pnl <= -lim and nick not in self.state["benched"]:
                self.bench(nick, f"own stop: lost ${-pnl:.2f} copying it", now)
                self.alert("Copycat benched an account",
                           f"{nick}: lost ${-pnl:.2f} on real copies (stop ${lim:.2f}). Back to paper only; "
                           f"{self.earn_back_note(nick)}.")
        tl = self.total_limit()
        if tl is not None and self.state["total"] <= -tl and not self.state.get("halted"):
            self.state["halted"] = f"total stop: Copycat lost ${-self.state['total']:.2f}"
            self.alert("Copycat halted", f"Copycat lost ${-self.state['total']:.2f} in total "
                       f"(stop ${tl:.2f}). Paper only from here until reset.")

    def summary(self, now: float) -> dict:
        pos = self.state["positions"]
        return {"mode": self.mode(now), "control": self.cfg.get("mode"), "bankroll": self.bankroll,
                "pool": round((self.cfg["pool_pct"] * self.bankroll) if self.bankroll else 0, 2),
                "open_cost": round(self.open_cost(), 2), "total_pnl": self.state["total"],
                "pnl": self.state["pnl"], "benched": self.benched(), "halted": self.state.get("halted"),
                "open": [dict(p, ticker=t) for t, p in pos.items() if not p.get("settled")][-30:],
                "settled": sorted([dict(p, ticker=t) for t, p in pos.items() if p.get("settled")],
                                  key=lambda p: -p["settled"])[:30]}
