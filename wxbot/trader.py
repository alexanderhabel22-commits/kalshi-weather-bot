"""Multi-strategy order layer with hard risk limits.

Runs after every scan (`python bot.py trade`, from the trade workflow). Each
strategy proposes resting BUY limit orders; this module decides which are
allowed, places them (live), records them (paper) or prints them (dry run),
follows fills, grades settled orders and enforces the stops.

Modes ([trading] mode, or the TRADING_MODE GitHub variable, which wins):
  off    nothing happens (kill switch)
  paper  orders are recorded, never sent; fills are judged from Kalshi's public
         trade tape after close (filled if a trade printed at our price or better)
  live   orders are sent to Kalshi with the API key (needs Alex's OK)

Hard rules, whatever a strategy asks for:
  - limit orders only, post-only (never cross the spread), price 1-99c,
    whole contracts, at most max_contracts_per_order (and the client's ceiling)
  - one order per market and side (no duplicates, ever)
  - max open dollars overall and per strategy, max per event
  - daily loss stop: no new orders for the rest of the day (ET)
  - total loss stop: HALT. Cancel every resting order, no new orders until
    Alex clears data/trader_state.json "halted" (or sets reset_halt = true)
  - ntfy alert on every fill and every stop
"""
from __future__ import annotations

import csv
import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import kalshi, notify
from .daybefore import fill_flags, trade_prices
from .kalshi_api import KalshiClient, OrderRejected, check_order, order_fill_count
from .util import DATA_DIR, Config, Http, HttpError, parse_iso, read_json, write_json

log = logging.getLogger("wxbot")

ORDERS = DATA_DIR / "trade_orders.csv"
STATE = DATA_DIR / "trader_state.json"
FIELDS = ["id", "client_id", "order_id", "mode", "strategy", "placed_at", "ticker", "event",
          "title", "side", "price", "count", "filled", "status", "close", "result", "pnl",
          "settled_at", "note", "filled_at"]
# status: resting -> filled (all filled, waiting for result) -> settled
#         resting -> done-unfilled / cancelled ; settled/partial handled by `filled`
OPEN = ("resting", "filled")

# Limits are fractions of the bankroll ([account] bankroll / BANKROLL variable),
# so they scale as the bankroll grows. settings() turns them into dollars.
DEFAULTS = {"mode": "off", "max_contracts_per_order": 3, "max_orders_per_run": 15,
            "max_open_pct": 0.60,         # at most 60% of the bankroll at risk at once
            "daily_loss_pct": 0.10,       # down 10% today -> no new orders until tomorrow
            "total_loss_pct": 0.25}       # down 25% overall -> HALT and cancel everything
PCT_KEYS = {"max_open_pct": "max_open_dollars", "max_event_pct": "max_event_dollars"}


@dataclass
class Intent:
    strategy: str
    ticker: str
    event: str
    side: str
    price: float
    count: int
    close: str = ""
    title: str = ""
    note: str = ""


@dataclass
class Context:
    cfg: Config
    http: Http
    now: datetime
    state: dict
    orders: list[dict]
    params: dict = field(default_factory=dict)    # this strategy's [trading.<name>] table


# ---------------------------------------------------------------- ledger

def load() -> list[dict]:
    if not ORDERS.exists():
        return []
    with open(ORDERS, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save(rows: list[dict]) -> None:
    ORDERS.parent.mkdir(parents=True, exist_ok=True)
    tmp = ORDERS.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(ORDERS)


def _f(x, d=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def exposure(rows: list[dict], strategy: str | None = None, event: str | None = None) -> float:
    """Dollars at risk: resting orders (full size) + filled, unsettled contracts."""
    tot = 0.0
    for r in rows:
        if r.get("status") not in OPEN:
            continue
        if strategy and r.get("strategy") != strategy:
            continue
        if event and r.get("event") != event:
            continue
        n = int(_f(r.get("count"))) if r["status"] == "resting" else int(_f(r.get("filled")))
        tot += n * _f(r.get("price"))
    return tot


def realized(rows: list[dict], since: datetime | None = None, strategy: str | None = None,
             mode: str | None = None) -> float:
    tot = 0.0
    for r in rows:
        if r.get("status") != "settled" or r.get("pnl") in (None, ""):
            continue
        if strategy and r.get("strategy") != strategy:
            continue
        if mode and r.get("mode") != mode:
            continue
        if since and (parse_iso(r.get("settled_at")) or since) < since:
            continue
        tot += _f(r["pnl"])
    return tot


# ---------------------------------------------------------------- settings

def settings(cfg: Config) -> dict:
    t = {**DEFAULTS, **{k: v for k, v in cfg.raw.get("trading", {}).items() if not isinstance(v, dict)}}
    env = (os.environ.get("TRADING_MODE") or "").strip().lower()
    if env:
        t["mode"] = env
    if t["mode"] not in ("off", "paper", "live"):
        t["mode"] = "off"
    b = t["bankroll"] = float(cfg.bankroll)
    t["max_open_dollars"] = round(float(t["max_open_pct"]) * b, 2)
    t["daily_loss_stop"] = -round(float(t["daily_loss_pct"]) * b, 2)
    t["total_loss_stop"] = -round(float(t["total_loss_pct"]) * b, 2)
    return t


def strategy_settings(cfg: Config, name: str, t: dict) -> dict:
    """A strategy's [trading.<name>] table, with its % caps turned into dollars."""
    sp = dict(cfg.raw.get("trading", {}).get(name, {}) or {})
    for pct, dollars in PCT_KEYS.items():
        if pct in sp:
            sp[dollars] = round(float(sp[pct]) * t["bankroll"], 2)
    return sp


def strategies() -> dict:
    """Every module in wxbot/strats with NAME and propose(ctx) is a strategy.
    Adding an edge = drop in a module + a [trading.<NAME>] table in config.toml."""
    import importlib
    import pkgutil
    from . import strats
    found = {}
    for mod in sorted(pkgutil.iter_modules(strats.__path__), key=lambda m: m.name):
        m = importlib.import_module(f"{strats.__name__}.{mod.name}")
        if hasattr(m, "NAME") and hasattr(m, "propose"):
            found[m.NAME] = m.propose
    return found


# ---------------------------------------------------------------- checks

def vet(intent: Intent, rows: list[dict], t: dict, sp: dict, now: datetime,
        planned: list[Intent]) -> str | None:
    """Why this intent is NOT allowed (None = allowed)."""
    try:
        check_order(intent.side, intent.price, intent.count)
    except OrderRejected as exc:
        return str(exc)
    if intent.count > int(t["max_contracts_per_order"]) or intent.count > int(sp.get("max_contracts", 99)):
        return "too many contracts"
    lo, hi = float(sp.get("min_price", 0.01)), float(sp.get("max_price", 0.99))
    if not (lo - 1e-9 <= intent.price <= hi + 1e-9):
        return "price outside strategy band"
    close = parse_iso(intent.close)
    if close is None or close <= now + timedelta(minutes=10):
        return "market closing"
    key = (intent.ticker, intent.side)
    if any((r.get("ticker"), r.get("side")) == key for r in rows) or \
            any((p.ticker, p.side) == key for p in planned):
        return "duplicate"
    cost = intent.price * intent.count
    pend = lambda f: sum(p.price * p.count for p in planned if f(p))  # noqa: E731
    if exposure(rows) + pend(lambda p: True) + cost > float(t["max_open_dollars"]) + 1e-9:
        return "overall open-$ cap"
    if exposure(rows, intent.strategy) + pend(lambda p: p.strategy == intent.strategy) + cost \
            > float(sp.get("max_open_dollars", t["max_open_dollars"])) + 1e-9:
        return "strategy open-$ cap"
    ev_cap = sp.get("max_event_dollars")
    if ev_cap is not None and exposure(rows, event=intent.event) + \
            pend(lambda p: p.event == intent.event) + cost > float(ev_cap) + 1e-9:
        return "event open-$ cap"
    return None


def client_id(intent: Intent, mode: str) -> str:
    h = hashlib.sha1(f"{mode}|{intent.strategy}|{intent.ticker}|{intent.side}".encode()).hexdigest()
    return f"wx-{intent.strategy[:8]}-{h[:20]}"


# ---------------------------------------------------------------- follow-up

def _alert(http, cfg, title, body, dry_run, priority=3, tags=None):
    try:
        notify.send(http, cfg, title, body, priority=priority, tags=tags or ["moneybag"],
                    dry_run=dry_run)
    except Exception as exc:  # alerts must never break trading bookkeeping
        log.warning("trade alert failed: %s", exc)


def sync_live(client: KalshiClient, rows: list[dict], cfg, http, dry_run) -> None:
    for r in rows:
        if r.get("mode") != "live" or r.get("status") != "resting" or not r.get("order_id"):
            continue
        try:
            o = client.get_order(r["order_id"])
        except HttpError as exc:
            log.warning("order %s: %s", r["order_id"], exc)
            continue
        placed = int(_f(r["count"]))
        got = order_fill_count(o, placed)
        before = int(_f(r.get("filled")))
        if got > before:
            r["filled"] = got
            _alert(http, cfg, f"FILLED: {r['side'].upper()} {got - before} @ {round(_f(r['price']) * 100)}¢",
                   f"{r['title'] or r['ticker']}\n{r['strategy']} · {got}/{placed} filled",
                   dry_run)
        st = (o.get("status") or "").lower()
        if got >= placed:
            r["status"] = "filled"
        elif st in ("canceled", "cancelled", "executed"):
            r["status"] = "filled" if got else "cancelled"


def crossed(r: dict, m: dict | None) -> bool:
    """The book moved through a resting paper bid: someone now offers our side
    at or below our price, which would have matched our order on Kalshi."""
    if not m:
        return False
    price = _f(r.get("price"))
    if r.get("side") == "yes":
        ask = kalshi.price(m, "yes_ask")
        return ask is not None and 0 < ask <= price + 1e-9
    yb = kalshi.price(m, "yes_bid")
    return yb is not None and 0 < yb and 1 - yb <= price + 1e-9


def settle(http: Http, rows: list[dict], now: datetime, cfg, dry_run) -> int:
    n = 0
    for r in rows:
        if r.get("status") not in OPEN:
            continue
        close = parse_iso(r.get("close"))
        placed = parse_iso(r.get("placed_at")) or now
        if now - placed < timedelta(minutes=30):
            continue
        # markets can settle before their close time (Mentions settle right after
        # the speech), so ask for the result whenever we grade
        if r["mode"] == "paper" and r["status"] == "resting":
            # paper fill: a real trade printed at our price or better since we "placed" it
            try:
                prints = trade_prices(http, r["ticker"], placed, min(close or now, now))
            except HttpError as exc:
                log.warning("paper trades %s: %s", r["ticker"], exc)
                continue
            m = kalshi.get_market(http, r["ticker"])
            if fill_flags(r["side"], _f(r["price"]), prints)[0] or crossed(r, m):
                r["filled"], r["status"] = int(_f(r["count"])), "filled"
                r["filled_at"] = now.isoformat(timespec="seconds")
        else:
            m = kalshi.get_market(http, r["ticker"])
        res = ((m or {}).get("result") or "").lower()
        if res not in ("yes", "no"):
            continue
        filled = int(_f(r.get("filled")))
        r["result"] = res
        r["settled_at"] = now.isoformat(timespec="seconds")
        if filled:
            pnl = filled * ((1.0 if res == r["side"] else 0.0) - _f(r["price"]))
            r["pnl"] = f"{pnl:.2f}"
            r["status"] = "settled"
            if r["mode"] == "live":
                _alert(http, cfg, f"Settled {'WIN' if pnl > 0 else 'LOSS'} ${pnl:+.2f}",
                       f"{r['title'] or r['ticker']} · {r['side'].upper()} x{filled}", dry_run)
        else:
            r["status"] = "done-unfilled"
        n += 1
    return n


def cancel_all(client: KalshiClient | None, rows: list[dict], mode: str) -> int:
    n = 0
    for r in rows:
        if r.get("status") != "resting":
            continue
        if mode == "live" and r.get("mode") == "live" and client and r.get("order_id"):
            try:
                client.cancel(r["order_id"])
            except HttpError as exc:
                log.warning("cancel %s: %s", r["order_id"], exc)
                continue
        if r.get("mode") == mode:
            r["status"] = "filled" if int(_f(r.get("filled"))) else "cancelled"
            n += 1
    return n


# ---------------------------------------------------------------- main

def strategy_mode(global_mode: str, sp: dict) -> str | None:
    """Each strategy runs on paper until it is explicitly cleared for live
    ([trading.<name>] live = true) AND the global mode is live."""
    if global_mode == "off" or not sp.get("enabled", False):
        return None
    return "live" if global_mode == "live" and sp.get("live") is True else "paper"


def _stops(mode: str, rows, state, t, now, midnight, today, client, cfg, http, dry_run, out) -> bool:
    """Apply the loss stops for one mode. Returns True if new orders are blocked."""
    halts = state.setdefault("halted", {})
    if cfg.raw.get("trading", {}).get("reset_halt"):
        halts.pop(mode, None)
    total = realized(rows, mode=mode)
    day = realized(rows, since=midnight, mode=mode)
    if total <= float(t["total_loss_stop"]) and mode not in halts:
        halts[mode] = {"at": now.isoformat(timespec="seconds"), "pnl": round(total, 2)}
        n = cancel_all(client, rows, mode)
        out["stops"].append(f"TOTAL STOP ({mode}): ${total:+.2f}; cancelled {n} resting orders")
        _alert(http, cfg, f"Trading HALTED ({mode}, total loss stop)",
               f"{mode} P&L ${total:+.2f} hit the ${t['total_loss_stop']:.2f} stop "
               f"({t['total_loss_pct']:.0%} of bankroll). Cancelled {n} resting orders. "
               "No new orders until you clear it.", dry_run, 5, ["warning"])
    if mode in halts:
        return True
    if day <= float(t["daily_loss_stop"]):
        key = f"{mode}|{today}"
        if state.get("day_stop") != key:
            state["day_stop"] = key
            out["stops"].append(f"DAILY STOP ({mode}): ${day:+.2f}")
            _alert(http, cfg, f"Trading paused for today ({mode}, daily loss stop)",
                   f"{mode} P&L today ${day:+.2f} hit the ${t['daily_loss_stop']:.2f} stop.",
                   dry_run, 4, ["warning"])
        return True
    return False


def run(cfg: Config, *, now: datetime, http: Http, client: KalshiClient | None = None,
        dry_run: bool = False) -> dict:
    t = settings(cfg)
    gmode = t["mode"]
    out = {"mode": gmode, "placed": [], "skipped": [], "stops": []}
    if gmode == "off":
        return out
    specs = {name: strategy_settings(cfg, name, t) for name in strategies()}
    modes = {n: m for n, sp in specs.items() if (m := strategy_mode(gmode, sp))}
    need_live = "live" in modes.values()
    if need_live and client is None:
        client = KalshiClient.from_env(http)
        if client is None:
            out["stops"].append("live mode but no API key: live strategies skipped")
            log.warning(out["stops"][-1])
            modes = {n: m for n, m in modes.items() if m != "live"}
            need_live = False

    rows = load()
    state = read_json(STATE, {}) or {}
    if not isinstance(state.get("halted", {}), dict):
        state["halted"] = {}
    tz = ZoneInfo(cfg.tz)
    today = now.astimezone(tz).date().isoformat()
    midnight = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)

    if client is not None:
        sync_live(client, rows, cfg, http, dry_run)
    if now.minute % 30 < 5 or any(r.get("mode") == "live" and r.get("status") in OPEN for r in rows):
        try:
            settle(http, rows, now, cfg, dry_run)
        except Exception as exc:
            log.warning("trade settle failed: %s", exc)

    blocked = {m: _stops(m, rows, state, t, now, midnight, today, client, cfg, http, dry_run, out)
               for m in ("paper", "live")}
    totals = {m: realized(rows, mode=m) for m in ("paper", "live")}

    planned: list[Intent] = []
    plan_mode: dict[int, str] = {}
    for name, propose in strategies().items():
        mode = modes.get(name)
        if mode is None or blocked[mode]:
            continue
        sp = specs[name]
        ctx = Context(cfg, http, now, state.setdefault(name, {}), rows, sp)
        try:
            intents = propose(ctx)
        except Exception as exc:  # a broken strategy must not stop the others
            log.warning("strategy %s failed: %s", name, exc)
            continue
        for it in intents:
            if len(planned) >= int(t["max_orders_per_run"]):
                break
            same = [r for r in rows if r.get("mode") == mode]
            why = vet(it, same, t, sp, now, [p for p in planned if plan_mode[id(p)] == mode])
            if why:
                out["skipped"].append((it.ticker, it.side, why))
                continue
            planned.append(it)
            plan_mode[id(it)] = mode
    for it in planned:
        mode = plan_mode[id(it)]
        row = {"id": f"{it.ticker}|{it.side}", "client_id": client_id(it, mode), "mode": mode,
               "strategy": it.strategy, "placed_at": now.isoformat(timespec="seconds"),
               "ticker": it.ticker, "event": it.event, "title": it.title, "side": it.side,
               "price": f"{it.price:.2f}", "count": it.count, "filled": 0,
               "status": "resting", "close": it.close, "note": it.note}
        if dry_run:
            print(f"[dry run] {mode} {it.strategy}: buy {it.side.upper()} {it.count} @ "
                  f"{it.price:.2f} {it.ticker}")
            out["placed"].append(row)
            continue
        if mode == "live":
            try:
                o = client.place_limit(it.ticker, it.side, it.price, it.count, row["client_id"])
            except (HttpError, OrderRejected) as exc:
                log.warning("order %s %s rejected: %s", it.ticker, it.side, exc)
                out["skipped"].append((it.ticker, it.side, f"rejected: {exc}"))
                continue
            row["order_id"] = o.get("order_id", "")
        rows.append(row)
        out["placed"].append(row)

    if not dry_run:
        save(rows)
        write_json(STATE, state)
    log.info("trade (%s, strategies %s): placed %d, skipped %d, stops %s, open $%.2f, "
             "P&L paper $%+.2f live $%+.2f", gmode, modes, len(out["placed"]), len(out["skipped"]),
             out["stops"], exposure(rows), totals["paper"], totals["live"])
    return out


# ---------------------------------------------------------------- report

def report_section(rows: list[dict], cfg: Config) -> list[str]:
    t = settings(cfg)
    state = read_json(STATE, {}) or {}
    lines = ["## Automatic trading", "",
             f"Mode: **{t['mode']}** · bankroll ${t['bankroll']:.2f} · caps: "
             f"{t['max_contracts_per_order']} contracts/order, ${t['max_open_dollars']:.2f} open "
             f"({t['max_open_pct']:.0%}), daily stop ${t['daily_loss_stop']:.2f} "
             f"({t['daily_loss_pct']:.0%}), total stop ${t['total_loss_stop']:.2f} ({t['total_loss_pct']:.0%})"
             + (f" · **HALTED: {', '.join(state['halted'])}**" if state.get("halted") else ""), ""]
    if not rows:
        return lines + ["No orders yet.", ""]
    lines += ["| Strategy | Mode | Orders | Open | Filled | Settled | P&L |", "|---|---|---|---|---|---|---|"]
    keys = sorted({(r["strategy"], r["mode"]) for r in rows})
    for s, m in keys:
        rs = [r for r in rows if r["strategy"] == s and r["mode"] == m]
        lines.append(f"| {s} | {m} | {len(rs)} | {sum(r['status'] in OPEN for r in rs)} "
                     f"| {sum(int(_f(r.get('filled'))) > 0 for r in rs)} "
                     f"| {sum(r['status'] == 'settled' for r in rs)} "
                     f"| ${realized(rs):+.2f} |")
    return lines + [""]
