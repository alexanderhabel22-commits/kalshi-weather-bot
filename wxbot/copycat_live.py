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
  - each account has its own stop (account_stop_pct of the bankroll, at most account_stop_usd = $3); Copycat as a whole halts at
    total_stop_pct. The boss (copycat_boss.py) and copycat_control.json "bench" can bench anyone.
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
DEFAULTS = {"mode": "off", "pool_pct": 0.15, "account_stop_pct": 0.10, "account_stop_usd": 3.0, "total_stop_pct": 0.25,
            "max_contracts": 3, "slip": 0.02, "max_lag_s": 60, "min_px": 0.05, "max_px": 0.95,
            "exclude_series": ["KXBTC15M", "KXSILVER15M"], "exclude_categories": ["Sports"], "bench": []}
CONTROL_STALE_S = 600


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

    def check_stops(self, now: float) -> None:
        b = self.bankroll or 0
        c = self.cfg
        cap = _f(c.get("account_stop_usd"))      # hard $ stop per account (Nov 14), whichever is smaller
        for nick, pnl in self.state["pnl"].items():
            lims = [x for x in (c["account_stop_pct"] * b if b else None, cap) if x is not None]
            lim = min(lims) if lims else None
            if lim is not None and pnl <= -lim and nick not in self.state["benched"]:
                self.state["benched"][nick] = f"own stop: lost ${-pnl:.2f} copying it"
                self.alert("Copycat benched an account",
                           f"{nick}: lost ${-pnl:.2f} on real copies (stop ${lim:.2f}). Back to paper only.")
        if not b:
            return
        if self.state["total"] < -c["total_stop_pct"] * b and not self.state.get("halted"):
            self.state["halted"] = f"total stop: Copycat lost ${-self.state['total']:.2f}"
            self.alert("Copycat halted", f"Copycat lost ${-self.state['total']:.2f} in total "
                       f"(stop {c['total_stop_pct']:.0%} of bankroll). No more copies until reset.")

    def summary(self, now: float) -> dict:
        pos = self.state["positions"]
        return {"mode": self.mode(now), "control": self.cfg.get("mode"), "bankroll": self.bankroll,
                "pool": round((self.cfg["pool_pct"] * self.bankroll) if self.bankroll else 0, 2),
                "open_cost": round(self.open_cost(), 2), "total_pnl": self.state["total"],
                "pnl": self.state["pnl"], "benched": self.benched(), "halted": self.state.get("halted"),
                "open": [dict(p, ticker=t) for t, p in pos.items() if not p.get("settled")][-30:],
                "settled": sorted([dict(p, ticker=t) for t, p in pos.items() if p.get("settled")],
                                  key=lambda p: -p["settled"])[:30]}
