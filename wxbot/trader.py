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
    except strategies whose table says taker = true (LOCKs): those buy at the
    current ask with immediate-or-cancel orders that never rest
    whole contracts, at most max_contracts_per_order (and the client's ceiling)
  - one order per market and side (no duplicates, ever)
  - max open dollars overall and per strategy, max per event
  - daily loss stop: no new orders for the rest of the day (ET)
  - total loss stop: HALT. Cancel every resting order, no new orders until
    Alex clears data/trader_state.json "halted" (or sets reset_halt = true)
  - ntfy alert on every fill and every stop (fills/settles bundled into one
    push when a run has more than BUNDLE_MAX of them)

Scale (Nov 7): markets are read in batches, live orders are synced from one
listing, the paper tape is read once per market from where the last check
stopped, and finished orders move to monthly archives in data/orders/ so the
hot ledger (and every 5-minute commit) stays small.
"""
from __future__ import annotations

import csv
import hashlib
import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import allocator, kalshi, notify, scaleup
from .daybefore import fill_flags, load_orders as weather_paper_orders, trade_prints
from .kalshi_api import KalshiClient, OrderRejected, check_order, order_fill_count
from .util import DATA_DIR, Config, Http, HttpError, parse_iso, read_json, write_json

log = logging.getLogger("wxbot")

ORDERS = DATA_DIR / "trade_orders.csv"
STATE = DATA_DIR / "trader_state.json"
FIELDS = ["id", "client_id", "order_id", "mode", "strategy", "placed_at", "ticker", "event",
          "title", "side", "price", "count", "filled", "status", "close", "result", "pnl",
          "settled_at", "note", "filled_at", "fee", "checked_at"]
# status: resting -> filled (all filled, waiting for result) -> settled
#         resting -> done-unfilled / cancelled ; settled/partial handled by `filled`
OPEN = ("resting", "filled")
DONE = ("settled", "done-unfilled", "cancelled")
ARCHIVE_DIR = DATA_DIR / "orders"          # monthly archives: data/orders/2026-11.csv
ARCHIVE_SUMMARY = ARCHIVE_DIR / "summary.json"
ARCHIVE_AFTER = timedelta(days=2)          # finished + market closed this long ago -> archive
BUNDLE_MAX = 5                             # more trade alerts than this in a run -> one push

# Limits are fractions of the bankroll ([account] bankroll / BANKROLL variable),
# so they scale as the bankroll grows. settings() turns them into dollars.
DEFAULTS = {"mode": "off", "max_contracts_per_order": 3, "max_orders_per_run": 15,
            "run_budget_s": 240,          # stop placing new orders this many seconds into a run
            "max_open_pct": 0.75,         # at most 75% of the bankroll at risk at once (live)
            "max_event_pct": 0.20,        # at most 20% on any one event (all strategies)
            "paper_max_open_pct": 1.00,   # paper may use the whole bankroll (Alex, Nov 5)
            "daily_loss_pct": 0.10,       # down 10% today -> no new orders until tomorrow
            "total_loss_pct": 0.25,       # down 25% overall -> HALT and cancel everything
            # capacity (scale study, Nov 5): never be more than ~10% of a market's
            # traded volume, so we don't become the price as the bankroll grows
            "max_volume_share": 0.10,
            "min_market_contracts": 50,   # always allowed up to this per market
            "max_market_contracts": 1000}  # never more than this in one market
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
    volume: float = 0.0     # contracts the market has traded so far (0 = unknown)
    taker: bool = False     # buy now at this price (immediate-or-cancel), only if the strategy allows


@dataclass
class Context:
    cfg: Config
    http: Http
    now: datetime
    state: dict
    orders: list[dict]
    params: dict = field(default_factory=dict)    # this strategy's [trading.<name>] table
    mode: str = ""                                # "live" or "paper": the mode this strategy runs in


def held_by_event(rows: list[dict], strategy: str, mode: str = "") -> dict[str, set]:
    """{event: tickers} this strategy already has open (resting or held) in `mode`,
    so per-event limits (max words / markets per event) count across scans."""
    out: dict[str, set] = {}
    for r in rows:
        if r.get("strategy") != strategy or r.get("status") not in ("resting", "filled"):
            continue
        if mode and r.get("mode") != mode:
            continue
        out.setdefault(r.get("event") or "", set()).add(r.get("ticker"))
    return out


def per_event_pick(intents: list, held: dict | None, n: int) -> list:
    """At most n markets per event, counting the ones already open there: the
    remaining slots go to new markets (in the order given)."""
    if not intents:
        return []
    have = held or {}
    taken = have.get(intents[0].event, ())
    fresh = [i for i in intents if i.ticker not in taken]
    return fresh[:max(0, n - len(taken))]


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


def _archive_rows(path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def archive_summary() -> dict:
    """data/orders/summary.json: what the archives hold, so the hot ledger can stay
    small without losing all-time P&L (loss stops) or the weekly capacity count."""
    return read_json(ARCHIVE_SUMMARY, {}) or {}


def archived_realized(mode: str | None = None, strategy: str | None = None,
                      summary: dict | None = None) -> float:
    s = archive_summary() if summary is None else summary
    tot = 0.0
    for m, by in (s.get("realized") or {}).items():
        if mode and m != mode:
            continue
        for strat, v in by.items():
            if strategy and strat != strategy:
                continue
            tot += _f(v)
    return tot


def archived_placed(mode: str, strategy: str, since: datetime, summary: dict | None = None) -> int:
    """Contracts placed since `since` (by day) in orders already archived."""
    s = archive_summary() if summary is None else summary
    day0 = since.date().isoformat()
    by = ((s.get("placed") or {}).get(mode) or {}).get(strategy) or {}
    return sum(int(_f(v)) for d, v in by.items() if d >= day0)


def load_all() -> list[dict]:
    """Archived + hot orders (reports and digests; the trader itself only needs hot)."""
    out = []
    for m in archive_summary().get("months") or []:
        out += _archive_rows(ARCHIVE_DIR / f"{m}.csv")
    seen = {(r.get("id"), r.get("mode")) for r in out}
    return out + [r for r in load() if (r.get("id"), r.get("mode")) not in seen]


def archive(rows: list[dict], now: datetime) -> list[dict]:
    """Move finished orders (settled / unfilled / cancelled) whose market closed more
    than ARCHIVE_AFTER ago to data/orders/<month>.csv, append-only. Returns the hot
    rows to keep. Safe to re-run after a crash: rows already in an archive are not
    written twice, and the summary is rebuilt from the archive files themselves."""
    keep, move = [], {}
    for r in rows:
        close = parse_iso(r.get("close"))
        done = parse_iso(r.get("settled_at")) or parse_iso(r.get("placed_at"))
        if (r.get("status") in DONE and done and now - done >= ARCHIVE_AFTER
                and (close is None or now - close >= ARCHIVE_AFTER)):
            move.setdefault(done.strftime("%Y-%m"), []).append(r)
        else:
            keep.append(r)
    if not move:
        return rows
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    for month, rs in sorted(move.items()):
        path = ARCHIVE_DIR / f"{month}.csv"
        have = {(r.get("id"), r.get("mode")) for r in _archive_rows(path)}
        new = [r for r in rs if (r.get("id"), r.get("mode")) not in have]
        if not new:
            continue
        fresh = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            if fresh:
                w.writeheader()
            w.writerows(new)
    rebuild_summary(now)
    log.info("archive: moved %d finished orders to %s", sum(len(v) for v in move.values()),
             ", ".join(sorted(move)))
    return keep


def rebuild_summary(now: datetime) -> dict:
    months = sorted(p.stem for p in ARCHIVE_DIR.glob("????-??.csv"))
    realized_: dict = {}
    placed: dict = {}
    counts: dict = {}
    cut = (now - timedelta(days=14)).date().isoformat()
    for m in months:
        rs = _archive_rows(ARCHIVE_DIR / f"{m}.csv")
        counts[m] = len(rs)
        for r in rs:
            mode, strat = r.get("mode") or "paper", r.get("strategy") or "?"
            if r.get("status") == "settled" and r.get("pnl") not in (None, ""):
                by = realized_.setdefault(mode, {})
                by[strat] = round(by.get(strat, 0.0) + _f(r["pnl"]), 2)
            day = (r.get("placed_at") or "")[:10]
            if day >= cut:
                d = placed.setdefault(mode, {}).setdefault(strat, {})
                d[day] = d.get(day, 0) + int(_f(r.get("count")))
    out = {"at": now.isoformat(timespec="seconds"), "months": months, "counts": counts,
           "realized": realized_, "placed": placed}
    write_json(ARCHIVE_SUMMARY, out)
    return out


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
    # scale-up kit (OFF until the reviews pass): tighter caps/stops as the bankroll grows
    t, tr = scaleup.main_limits(t, scaleup.params(cfg), b)
    t["scaleup_tier"] = (tr or {}).get("name")
    t["max_open_dollars"] = round(float(t["max_open_pct"]) * b, 2)
    t["paper_max_open_dollars"] = round(float(t["paper_max_open_pct"]) * b, 2)
    t["max_event_dollars"] = round(float(t["max_event_pct"]) * b, 2)
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


def valuers() -> dict:
    """Strategies that can re-score a held position: NAME -> value(http, row, market, now),
    returning our side's chance now, or None to fall back to the usual exit rule."""
    import importlib
    import pkgutil
    from . import strats
    found = {}
    for mod in pkgutil.iter_modules(strats.__path__):
        m = importlib.import_module(f"{strats.__name__}.{mod.name}")
        if hasattr(m, "NAME") and hasattr(m, "value"):
            found[m.NAME] = m.value
    return found


def cancellers() -> dict:
    """Strategies whose resting orders expire: NAME -> cancel_due(row, now) -> bool."""
    import importlib
    import pkgutil
    from . import strats
    found = {}
    for mod in pkgutil.iter_modules(strats.__path__):
        m = importlib.import_module(f"{strats.__name__}.{mod.name}")
        if hasattr(m, "NAME") and hasattr(m, "cancel_due"):
            found[m.NAME] = m.cancel_due
    return found


def expire_resting(client: KalshiClient | None, rows: list[dict], now: datetime, dry_run: bool) -> int:
    """Cancel resting orders whose strategy says they are due (e.g. S&P ranges before the
    open). Live rows stay "resting" until sync_live reads the order back, so a fill that
    lands just before the cancel is still booked; paper rows are closed here."""
    due = cancellers()
    n = 0
    for r in rows:
        f = due.get(r.get("strategy"))
        if r.get("status") != "resting" or f is None:
            continue
        try:
            if not f(r, now):
                continue
        except Exception as exc:
            log.warning("cancel_due %s: %s", r.get("strategy"), exc)
            continue
        if dry_run:
            print(f"[dry run] cancel {r.get('mode')} {r.get('strategy')} {r.get('ticker')}")
            continue
        if r.get("mode") == "live":
            if client is None or not r.get("order_id"):
                log.warning("expire %s: live order but no client/order id", r.get("ticker"))
                continue
            try:
                client.cancel(r["order_id"], r.get("ticker", ""))
            except HttpError as exc:
                log.warning("expire cancel %s: %s", r["order_id"], exc)
                continue
        else:
            r["status"] = "filled" if int(_f(r.get("filled"))) else "cancelled"
        n += 1
    if n:
        log.info("expired %d resting orders", n)
    return n


def shadows() -> dict:
    """Strategies with a log-only exit candidate: NAME -> shadow(http, row, market, now)."""
    import importlib
    import pkgutil
    from . import strats
    found = {}
    for mod in pkgutil.iter_modules(strats.__path__):
        m = importlib.import_module(f"{strats.__name__}.{mod.name}")
        if hasattr(m, "NAME") and hasattr(m, "shadow"):
            found[m.NAME] = m.shadow
    return found


def model_exit(r: dict, m: dict | None, value: float, margin: float) -> tuple[float, str] | None:
    """Sell at the bid when the bid after the taker fee beats the model's chance for our
    side by `margin` (so a 50% bracket is held at 51c, sold at 50c + margin + fee)."""
    if not m:
        return None
    yb, ya = kalshi.price(m, "yes_bid"), kalshi.price(m, "yes_ask")
    if yb is None or ya is None or not (0 < yb < ya <= 1):
        return None
    bid = yb if r.get("side") == "yes" else round(1 - ya, 2)
    if not (0.01 <= bid <= 0.99):
        return None
    if bid - _fee(bid) >= value + margin:
        return bid, f"bid {bid:.2f} beats model {value:.2f} + {margin:.2f}"
    return None


# ---------------------------------------------------------------- checks

def vet(intent: Intent, rows: list[dict], t: dict, sp: dict, now: datetime,
        planned: list[Intent], mode: str = "live") -> str | None:
    """Why this intent is NOT allowed (None = allowed)."""
    try:
        check_order(intent.side, intent.price, intent.count)
    except OrderRejected as exc:
        return str(exc)
    if intent.count > int(t["max_contracts_per_order"]) or intent.count > int(sp.get("max_contracts", 99)):
        return "too many contracts"
    if intent.taker and not sp.get("taker"):
        return "taker orders not allowed for this strategy"
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
    cap = float(t["max_open_dollars"] if mode == "live" else t["paper_max_open_dollars"])
    if exposure(rows) + pend(lambda p: True) + cost > cap + 1e-9:
        return "overall open-$ cap"
    if exposure(rows, intent.strategy) + pend(lambda p: p.strategy == intent.strategy) + cost \
            > float(sp.get("max_open_dollars", t["max_open_dollars"])) + 1e-9:
        return "strategy open-$ cap"
    ev_cap = min(float(sp.get("max_event_dollars", t["max_event_dollars"])), float(t["max_event_dollars"]))
    if exposure(rows, event=intent.event) + \
            pend(lambda p: p.event == intent.event) + cost > float(ev_cap) + 1e-9:
        return "event open-$ cap"
    return None


def capacity(intent: Intent, rows: list[dict], t: dict, sp: dict, now: datetime,
             planned: list[Intent]) -> tuple[int, str]:
    """How many contracts this intent may still add, from the capacity limits:
    our share of the market's traded volume (max_volume_share, at least
    min_market_contracts, at most max_market_contracts) and the strategy's
    weekly contract ceiling (max_week_contracts, its fair share of the flow)."""
    share = float(sp.get("max_volume_share", t["max_volume_share"]))
    floor = int(sp.get("min_market_contracts", t["min_market_contracts"]))
    ceil_ = int(sp.get("max_market_contracts", t["max_market_contracts"]))
    lim = min(ceil_, max(floor, int(share * float(intent.volume or 0))))
    held = sum(int(_f(r.get("count"))) for r in rows if r.get("ticker") == intent.ticker
               and r.get("status") in ("resting", "filled")) + \
        sum(p.count for p in planned if p.ticker == intent.ticker)
    room, why = lim - held, f"market capacity ({lim} contracts)"
    week = sp.get("max_week_contracts")
    if week is not None:
        since = now - timedelta(days=7)
        used = sum(int(_f(r.get("count"))) for r in rows if r.get("strategy") == intent.strategy
                   and (parse_iso(r.get("placed_at")) or since) > since) + \
            sum(p.count for p in planned if p.strategy == intent.strategy)
        mode = t.get("_mode_hint") or next((r.get("mode") for r in rows if r.get("mode")), None)
        if mode:
            used += archived_placed(mode, intent.strategy, since)
        if int(week) - used < room:
            room, why = int(week) - used, f"weekly capacity ({int(week)} contracts)"
    return max(0, room), why


def client_id(intent: Intent, mode: str, now: datetime | None = None) -> str:
    # taker orders may be retried next scan (nothing filled), so they get a fresh id
    salt = f"|{now:%Y%m%dT%H%M%S}" if intent.taker and now else ""
    h = hashlib.sha1(f"{mode}|{intent.strategy}|{intent.ticker}|{intent.side}{salt}".encode()).hexdigest()
    return f"wx-{intent.strategy[:8]}-{h[:20]}"


# ---------------------------------------------------------------- follow-up

_BUNDLE: list | None = None     # trade alerts collected during run(), sent at the end


def _alert(http, cfg, title, body, dry_run, priority=3, tags=None, bundle=False):
    if bundle and _BUNDLE is not None:
        _BUNDLE.append((title, body, priority, tags))
        return
    try:
        notify.send(http, cfg, title, body, priority=priority, tags=tags or ["moneybag"],
                    dry_run=dry_run)
    except Exception as exc:  # alerts must never break trading bookkeeping
        log.warning("trade alert failed: %s", exc)


def flush_alerts(http, cfg, dry_run) -> int:
    """Send the trade alerts collected this run: one by one when there are few,
    otherwise one summary push (thousands of fills a day must not mean thousands
    of pings)."""
    global _BUNDLE
    items, _BUNDLE = _BUNDLE or [], None
    if not items:
        return 0
    if len(items) <= BUNDLE_MAX:
        pushes = [(title, body, pr, tags) for title, body, pr, tags in items]
    else:
        lines = [f"- {title}" for title, *_ in items[:12]]
        if len(items) > 12:
            lines.append(f"...and {len(items) - 12} more (dashboard has all of them)")
        pushes = [(f"{len(items)} trade updates", "\n".join(lines),
                   max(pr for _, _, pr, _ in items), ["moneybag"])]
    p = scaleup.params(cfg)
    st = (read_json(STATE, {}) or {}) if p["enabled"] and not dry_run else {}
    sent = 0
    for title, body, pr, tags in pushes:
        # scale-up kit: a daily budget of routine trade pushes (>300 orders a day)
        ok, note = scaleup.push_allowed(p, st, datetime.now(ZoneInfo(cfg.tz)), pr)
        if note:
            _alert(http, cfg, "Trade pushes paused for today",
                   f"{p['max_trade_pushes_per_day']} trade pushes sent today. More fills and sales "
                   "still happen and show on the dashboard; stops and warnings still push.",
                   dry_run, 3, ["moneybag"])
        if ok:
            _alert(http, cfg, title, body, dry_run, pr, tags)
            sent += 1
    if p["enabled"] and not dry_run and st:
        write_json(STATE, st)
    return sent


def sync_live(client: KalshiClient, rows: list[dict], cfg, http, dry_run,
              now: datetime | None = None) -> None:
    mine = [r for r in rows
            if r.get("mode") == "live" and r.get("status") == "resting" and r.get("order_id")]
    if not mine:
        return
    # contracts of an order that were already sold early or netted live on their own
    # rows (same order_id); Kalshi's fill count includes them, so take them off again
    moved: dict[str, int] = {}
    for x in rows:
        oid = x.get("order_id")
        if oid and x.get("mode") == "live" and x.get("status") != "resting" and _is_child(x):
            moved[oid] = moved.get(oid, 0) + int(_f(x.get("filled")))
    listed = None
    if len(mine) > 3:
        # one paginated listing instead of one request per order; orders that left
        # the resting list (filled or cancelled) are then read one by one
        try:
            listed = client.resting_orders()
        except Exception as exc:
            log.warning("resting order listing failed, checking one by one: %s", exc)
    for r in mine:
        o = listed.get(r["order_id"]) if listed is not None else None
        if o is None:
            try:
                o = client.get_order(r["order_id"])
            except HttpError as exc:
                log.warning("order %s: %s", r["order_id"], exc)
                continue
        placed = int(_f(r["count"]))
        gone = moved.get(r["order_id"], 0)
        got = max(0, order_fill_count(o, placed + gone) - gone)
        before = int(_f(r.get("filled")))
        if got > before:
            r["filled"] = got
            if not r.get("filled_at") and now is not None:
                # first fill seen (within one 5-min run): lets the checkpoint measure
                # fill delay and price moves after the fill, as the backtests assume
                r["filled_at"] = now.isoformat(timespec="seconds")
            _alert(http, cfg, f"FILLED: {r['side'].upper()} {got - before} @ {round(_f(r['price']) * 100)}¢",
                   f"{r['title'] or r['ticker']}\n{r['strategy']} · {got}/{placed} filled",
                   dry_run, bundle=True)
        st = (o.get("status") or "").lower()
        if got >= placed:
            r["status"] = "filled"
        elif st in ("canceled", "cancelled", "executed"):
            r["status"] = "filled" if got else "cancelled"


def _is_child(r: dict) -> bool:
    """A row split off another order's row (sold early or netted), see _split."""
    return bool(re.search(r"\|(exit|net)\d*$", str(r.get("id", ""))))


def _split(rows: list[dict], r: dict, k: int, tag: str) -> dict:
    """Move k held contracts of row r onto a new row (an early sale or a netted pair)
    and return it. The parent keeps the rest, still resting if its order is: count
    and filled both drop by k, and its entry fee is shared out by contracts. The new
    row keeps the order_id so sync_live can tell those contracts are already booked."""
    filled = int(_f(r.get("filled")))
    fee = _f(r.get("fee"))
    share = round(fee * k / filled, 2) if filled else 0.0
    base = f"{r['id']}|{tag}"
    n = sum(1 for x in rows if re.fullmatch(re.escape(base) + r"\d*", str(x.get("id", ""))))
    child = dict(r, id=base + (str(n + 1) if n else ""), filled=k, count=k)
    if r.get("fee") not in (None, ""):
        child["fee"] = f"{share:.2f}"
        r["fee"] = f"{fee - share:.2f}"
    r["filled"] = filled - k
    r["count"] = int(_f(r.get("count"))) - k
    rows.append(child)
    return child


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


def net_pairs(rows: list[dict], now: datetime) -> int:
    """Kalshi nets YES against NO in the same market: holding both pays $1 per pair at
    once and the position closes. Book that like the account does (the weather
    strategy can buy both sides when the spread is wide: 27c YES + 55c NO = +18c)."""
    n = 0
    held = lambda r: (r.get("mode") == "live" and r.get("status") in ("filled", "resting")  # noqa: E731
                      and int(_f(r.get("filled"))) > 0)
    by: dict[str, dict[str, list]] = {}
    for r in rows:
        if held(r):
            by.setdefault(r["ticker"], {}).setdefault(r["side"], []).append(r)
    for tk, sides in by.items():
        ys, ns = sides.get("yes", []), sides.get("no", [])
        while ys and ns:
            y, o = ys[0], ns[0]
            k = min(int(_f(y["filled"])), int(_f(o["filled"])))
            each = k * (1.0 - _f(y["price"]) - _f(o["price"])) / 2
            for leg, other, lst in ((y, o, ys), (o, y, ns)):
                part = leg
                if int(_f(leg["filled"])) > k or leg["status"] == "resting":
                    # keep the rest of this order open on its row; book the netted part apart
                    part = _split(rows, leg, k, "net")
                    if int(_f(leg["filled"])) == 0:
                        lst.pop(0)
                else:
                    lst.pop(0)
                part.update(status="settled", result="netted", pnl=f"{each - _f(part.get('fee')):.2f}",
                            settled_at=now.isoformat(timespec="seconds"),
                            note=(part.get("note") or "") + f" | netted {k} vs {other['side'].upper()} "
                                 f"@ {_f(other['price']):.2f}: Kalshi paid $1 per pair")
            n += k
    return n


def _since(r: dict, now: datetime) -> datetime:
    """Where this paper order's tape check starts: placement, or where the last
    check stopped (a minute of overlap), so each run reads only new trades."""
    placed = parse_iso(r.get("placed_at")) or now
    last = parse_iso(r.get("checked_at"))
    return max(placed, last - timedelta(minutes=1)) if last else placed


def settle(http: Http, rows: list[dict], now: datetime, cfg, dry_run) -> int:
    n = 0
    todo = [r for r in rows if r.get("status") in OPEN
            and now - (parse_iso(r.get("placed_at")) or now) >= timedelta(minutes=30)]
    if not todo:
        return 0
    # markets can settle before their close time (Mentions settle right after the
    # speech), so ask for the result whenever we grade: one batched read for all
    markets = kalshi.get_markets(http, [r["ticker"] for r in todo])
    # paper fills: read each market's trade tape once, from the earliest point any
    # of our paper orders there still needs
    tape: dict[str, list | None] = {}
    for r in todo:
        if r["mode"] == "paper" and r["status"] == "resting":
            tape.setdefault(r["ticker"], [])
    for tk in tape:
        rs = [r for r in todo if r["ticker"] == tk and r["mode"] == "paper" and r["status"] == "resting"]
        since = min(_since(r, now) for r in rs)
        until = min(max(parse_iso(r.get("close")) or now for r in rs), now)
        try:
            tape[tk] = trade_prints(http, tk, since, until)
        except HttpError as exc:
            log.warning("paper trades %s: %s", tk, exc)
            tape[tk] = None
    for r in todo:
        m = markets.get(r["ticker"])
        if r["mode"] == "paper" and r["status"] == "resting":
            prints = tape.get(r["ticker"])
            if prints is None:
                continue
            lo = _since(r, now).timestamp()
            close = parse_iso(r.get("close"))
            hi = min(close or now, now).timestamp()
            px = [p for at, p in prints if at is None or lo - 1 <= at <= hi + 1]
            if fill_flags(r["side"], _f(r["price"]), px)[0] or crossed(r, m):
                r["filled"], r["status"] = int(_f(r["count"])), "filled"
                r["filled_at"] = now.isoformat(timespec="seconds")
            else:
                r["checked_at"] = min(close or now, now).isoformat(timespec="seconds")
        res = ((m or {}).get("result") or "").lower()
        if res not in ("yes", "no"):
            continue
        filled = int(_f(r.get("filled")))
        r["result"] = res
        r["settled_at"] = now.isoformat(timespec="seconds")
        if filled:
            pnl = filled * ((1.0 if res == r["side"] else 0.0) - _f(r["price"])) - _f(r.get("fee"))
            r["pnl"] = f"{pnl:.2f}"
            r["status"] = "settled"
            if r["mode"] == "live":
                _alert(http, cfg, f"Settled {'WIN' if pnl > 0 else 'LOSS'} ${pnl:+.2f}",
                       f"{r['title'] or r['ticker']} · {r['side'].upper()} x{filled}", dry_run,
                       bundle=True)
        else:
            r["status"] = "done-unfilled"
        n += 1
    return n


def _fee(p: float, n: int = 1) -> float:
    """Kalshi's taker fee for one order of n contracts at price p (rounded up to the cent
    on the whole order, so per contract it shrinks as n grows)."""
    return math.ceil(round(0.07 * n * p * (1 - p) * 100, 6)) / 100


def exit_decision(r: dict, m: dict | None, sp: dict, cap_blocked: bool) -> tuple[float, str] | None:
    """(sell price, reason) if selling now beats holding to settlement.

    Hold value = the market's mid for our side + this strategy's historical edge
    (`hold_edge`, what holding has earned on average beyond the price). We sell
    at the bid only when the bid after the taker fee is worth more than that.
    Since a bid sits below the mid, this fires only when the book is lopsided in
    our favour, so in practice we hold through swings ("diamond hands").
    (98c+ sells to free money for a blocked order live in free_cap_for.) Fixed stop-losses are deliberately absent: in the
    backtest they cost money (they sell after a bad swing, at a bad price). The
    daily/total loss stops remain the backstop if a strategy is getting killed."""
    if not m or str(sp.get("exit", "ev")).lower() == "hold":
        return None
    yb, ya = kalshi.price(m, "yes_bid"), kalshi.price(m, "yes_ask")
    if yb is None or ya is None or not (0 < yb < ya <= 1):
        return None
    side = r.get("side")
    bid = yb if side == "yes" else round(1 - ya, 2)
    ask = ya if side == "yes" else round(1 - yb, 2)
    if not (0.01 <= bid <= 0.99):
        return None
    net = bid - _fee(bid)
    hold = (bid + ask) / 2 + float(sp.get("hold_edge", 0.03))
    if net > min(hold, 1.0) + 0.01:
        return bid, f"bid {bid:.2f} beats hold value {hold:.2f}"
    return None


def _holds(r: dict) -> bool:
    return r.get("status") in OPEN and int(_f(r.get("filled"))) > 0


def exits(http, rows, now, specs, client, cfg, dry_run, cap_blocked: set) -> int:
    n = 0
    vals, shads = valuers(), shadows()
    # every row holding contracts, including an order that is only partly filled so far
    # (a 6-contract Mentions bid with 4 filled holds 4 we may want to sell)
    held = [r for r in rows if _holds(r) and r.get("mode") in ("paper", "live")]
    markets = kalshi.get_markets(http, [r["ticker"] for r in held]) if held else {}
    for r in held:
        sp = specs.get(r.get("strategy"), {})
        m = markets.get(r["ticker"])
        if (m or {}).get("result"):
            continue                      # settling anyway
        if m and r.get("strategy") in shads:
            try:
                shads[r["strategy"]](http, r, m, now)   # log only, never trades
            except Exception as exc:
                log.warning("exit shadow %s: %s", r["ticker"], exc)
        d = None
        vf = vals.get(r.get("strategy")) if str(sp.get("exit", "ev")).lower() == "model" else None
        v = None
        if vf and m:
            try:
                v = vf(http, r, m, now)
            except Exception as exc:      # a model hiccup must never block the other exits
                log.warning("exit value %s: %s", r["ticker"], exc)
        if v is not None:
            d = model_exit(r, m, v, float(sp.get("exit_margin", 0.08)))
        elif str(sp.get("exit", "ev")).lower() != "model":
            d = exit_decision(r, m, sp, r["mode"] in cap_blocked)
        if not d:
            continue
        if _sell(http, r, rows, d[0], d[1], client, cfg, dry_run, now):
            n += 1
    return n


def _sell(http, r, rows, price, why, client, cfg, dry_run, now) -> bool:
    """Sell a held position at `price` (immediate-or-cancel for live) and book it.

    Multi-contract positions: we ask for every contract we hold, but only at `price`
    or better, so we never dump contracts below the price the exit rule approved.
    Whatever the bid can't take is cancelled and stays held on this row; the next
    run (5 minutes later) checks it again with a fresh order id."""
    held = int(_f(r.get("filled")))
    count = held
    if r["mode"] == "live":
        if dry_run or client is None:
            return False
        # a fresh id every attempt: Kalshi refuses a reused client id, which used to
        # block selling the rest of a position after a partial (or empty) sale
        cid = f"x{now:%d%H%M%S}-{r['client_id']}"[:60]
        try:
            o = client.sell_now(r["ticker"], r["side"], price, count, cid)
        except (HttpError, OrderRejected) as exc:
            log.warning("exit %s rejected: %s", r["ticker"], exc)
            return False
        sold = min(order_fill_count(o, count), held)
        if sold <= 0:
            return False
        count = sold
    if count < held or r.get("status") == "resting":
        # partial exit, or the order is still resting for more: keep the rest on
        # this row, book the sold part on its own row
        r = _split(rows, r, count, "exit")
    pnl = count * (price - _f(r["price"])) - _fee(price, count) - _f(r.get("fee"))
    r.update(status="settled", result="exit", pnl=f"{pnl:.2f}",
             settled_at=now.isoformat(timespec="seconds"),
             note=(r.get("note") or "") + f" | sold {count} @ {price:.2f}: {why}")
    if r["mode"] == "live":
        _alert(http, cfg, f"Sold early ${pnl:+.2f}",
               f"{r['title'] or r['ticker']} · sold {count} of {held} · {why}", dry_run, bundle=True)
    return True


def free_cap_for(http, it: Intent, why: str, mode: str, rows: list[dict], planned: list[Intent],
                 t: dict, specs: dict, now, client, cfg, dry_run) -> list[str]:
    """Alex (Nov 7): sell a nearly-won position (bid 98c+) only when a specific new
    order is blocked by the open-$ cap, only as much as that order needs, and only
    if selling actually lets it through. Returns the tickers sold."""
    same = [r for r in rows if r.get("mode") == mode]
    sp = specs.get(it.strategy, {})
    cost = it.price * it.count
    pend = sum(p.price * p.count for p in planned)
    if why == "overall open-$ cap":
        cap = float(t["max_open_dollars"] if mode == "live" else t["paper_max_open_dollars"])
        need = exposure(same) + pend + cost - cap
        pool = [r for r in same if _holds(r)]
    else:   # strategy cap: only that strategy's own positions free it
        cap = float(sp.get("max_open_dollars", t["max_open_dollars"]))
        need = exposure(same, it.strategy) + sum(p.price * p.count for p in planned
                                                 if p.strategy == it.strategy) + cost - cap
        pool = [r for r in same if _holds(r) and r.get("strategy") == it.strategy]
    pool = [r for r in pool if str(specs.get(r.get("strategy"), {}).get("exit", "ev")).lower() != "hold"]
    if need <= 0 or not pool:
        return []
    mk = kalshi.get_markets(http, [r["ticker"] for r in pool])
    cands = []
    for r in pool:
        m = mk.get(r["ticker"]) or {}
        yb, ya = kalshi.price(m, "yes_bid"), kalshi.price(m, "yes_ask")
        if m.get("result") or yb is None or ya is None or not (0 < yb < ya <= 1):
            continue
        bid = yb if r.get("side") == "yes" else round(1 - ya, 2)
        if 0.98 <= bid <= 0.99:
            cands.append((bid, r))
    cands.sort(key=lambda x: -x[0])
    pick, freed = [], 0.0
    for bid, r in cands:
        if freed >= need - 1e-9:
            break
        pick.append((bid, r))
        freed += int(_f(r.get("filled"))) * _f(r.get("price"))
    if freed < need - 1e-9:
        return []                         # selling everything eligible still wouldn't fit it
    gone = {id(r) for _, r in pick}
    after = [dict(r, status="settled") if id(r) in gone else r for r in same]
    if vet(it, after, t, sp, now, planned, mode):
        # another limit (strategy or event cap) would still block it: selling frees nothing
        # useful (Nov 8 01:32 UTC: two weather wins sold at 98-99c for a Mentions word the
        # event cap then refused)
        return []
    sold = []
    for bid, r in pick:
        if _sell(http, r, rows, bid, f"freed ${freed:.2f} for {it.strategy} {it.ticker} (bid {bid:.2f})",
                 client, cfg, dry_run, now):
            sold.append(r["ticker"])
    return sold


def cancel_all(client: KalshiClient | None, rows: list[dict], mode: str) -> int:
    n = 0
    for r in rows:
        if r.get("status") != "resting":
            continue
        if mode == "live" and r.get("mode") == "live" and client and r.get("order_id"):
            try:
                client.cancel(r["order_id"], r.get("ticker", ""))
            except HttpError as exc:
                log.warning("cancel %s: %s", r["order_id"], exc)
                continue
        if r.get("mode") == mode:
            r["status"] = "filled" if int(_f(r.get("filled"))) else "cancelled"
            n += 1
    return n


# ---------------------------------------------------------------- main

def key_check(state: dict, http, now: datetime) -> None:
    """While everything is paper: if the API key secrets exist, read the account
    balance once every 6 hours (read-only, no orders) to prove the key works
    before go-live. Result in data/trader_state.json "key_check"."""
    last = parse_iso((state.get("key_check") or {}).get("at"))
    if last and now - last < timedelta(hours=6):
        return
    try:
        client = KalshiClient.from_env(http)
    except Exception as exc:          # bad PEM text, wrong key type...
        state["key_check"] = {"at": now.isoformat(timespec="seconds"), "ok": False,
                              "error": f"key could not be loaded: {type(exc).__name__}"}
        log.warning("Kalshi key check: %s", state["key_check"]["error"])
        return
    if client is None:
        return
    try:
        bal = client.balance()
        state["key_check"] = {"at": now.isoformat(timespec="seconds"), "ok": True, "balance": round(bal, 2)}
        log.info("Kalshi key check: OK, balance $%.2f", bal)
    except Exception as exc:
        state["key_check"] = {"at": now.isoformat(timespec="seconds"), "ok": False,
                              "error": str(exc)[:200]}
        log.warning("Kalshi key check failed: %s", exc)


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
    total = realized(rows, mode=mode) + archived_realized(mode)   # all-time, incl. archived orders
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


def _off_cancel(client, cfg, http, now, out) -> None:
    """Kill switch: "off" must mean nothing more can fill. Book any fills Kalshi already
    made on our resting real-money orders, then cancel what is still resting. Positions
    already held are left alone (they settle on their own). Paper orders are untouched."""
    rows = load()
    if not any(r.get("mode") == "live" and r.get("status") == "resting" and r.get("order_id")
               for r in rows):
        return
    client = client or KalshiClient.from_env(http)
    if client is None:
        log.warning("kill switch: resting live orders but no API key to cancel them")
        return
    try:
        sync_live(client, rows, cfg, http, False, now)
    except Exception as exc:
        log.warning("kill switch: fill sync failed: %s", exc)
    n = cancel_all(client, rows, "live")
    save(rows)
    if n:
        out["stops"].append(f"KILL SWITCH: cancelled {n} resting live orders")
        _alert(http, cfg, "Kill switch: resting orders cancelled",
               f"Trading is off. Cancelled {n} resting real-money orders so nothing more can "
               "fill. Positions already held stay until they settle.", False, 4, ["warning"])


def run(cfg: Config, *, now: datetime, http: Http, client: KalshiClient | None = None,
        dry_run: bool = False) -> dict:
    global _BUNDLE
    _BUNDLE = []
    try:
        return _run(cfg, now=now, http=http, client=client, dry_run=dry_run)
    finally:
        flush_alerts(http, cfg, dry_run)


def _run(cfg: Config, *, now: datetime, http: Http, client: KalshiClient | None = None,
         dry_run: bool = False) -> dict:
    t0 = time.monotonic()
    t = settings(cfg)
    gmode = t["mode"]
    out = {"mode": gmode, "placed": [], "skipped": [], "stops": []}
    if gmode == "off":
        # the always-on 15-minute bot (wxbot/m15.py) reads this to follow the kill switch
        if not dry_run:
            st = read_json(STATE, {}) or {}
            if st.get("mode") != "off":
                st["mode"] = "off"
                write_json(STATE, st)
            _off_cancel(client, cfg, http, now, out)
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
    state["mode"] = gmode           # kill switch for the 15-minute bot on the server
    if not need_live and client is None and not dry_run:
        key_check(state, http, now)
    tz = ZoneInfo(cfg.tz)
    today = now.astimezone(tz).date().isoformat()
    midnight = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        expire_resting(client, rows, now, dry_run)
    except Exception as exc:          # never block the run
        log.warning("expire_resting failed: %s", exc)
    if client is not None:
        sync_live(client, rows, cfg, http, dry_run, now)
    net_pairs(rows, now)
    if now.minute % 30 < 5 or any(r.get("mode") == "live" and r.get("status") in OPEN for r in rows):
        try:
            settle(http, rows, now, cfg, dry_run)
        except Exception as exc:
            log.warning("trade settle failed: %s", exc)

    blocked = {m: _stops(m, rows, state, t, now, midnight, today, client, cfg, http, dry_run, out)
               for m in ("paper", "live")}
    order_names = list(strategies())
    ap = allocator.params(cfg)
    if ap["enabled"]:
        # grade every strategy on its settled results; re-allocate once a week.
        # Shadow (apply = false) only records the grades; sizing is unchanged.
        try:
            store = state.setdefault("allocator", {})
            m15 = allocator.m15_names(cfg)
            # the 15-minute bot (own server) is graded too; read its uploads only on re-grade day
            m15_rows = allocator.fetch_m15(http, cfg, now) if m15 and allocator.due(cfg, store, now) else []
            alloc = allocator.update(cfg, load_all(), weather_paper_orders(), list(specs) + m15, now,
                                     store, m15_rows=m15_rows)
            specs = allocator.apply_to(specs, alloc, ap)
            order_names = allocator.order(order_names, alloc, ap)
            out["allocation"] = alloc.get("multipliers", {})
        except Exception as exc:      # the allocator must never stop trading
            log.warning("allocator failed: %s", exc)
    totals = {m: realized(rows, mode=m) + archived_realized(m) for m in ("paper", "live")}

    planned: list[Intent] = []
    plan_mode: dict[int, str] = {}
    props = strategies()
    for name in order_names:
        propose = props[name]
        mode = modes.get(name)
        if mode is None or blocked[mode]:
            continue
        sp = specs[name]
        ctx = Context(cfg, http, now, state.setdefault(name, {}), rows, sp, mode)
        try:
            intents = propose(ctx)
        except Exception as exc:  # a broken strategy must not stop the others
            log.warning("strategy %s failed: %s", name, exc)
            continue
        for it in intents:
            if len(planned) >= int(t["max_orders_per_run"]):
                break
            if time.monotonic() - t0 > float(t["run_budget_s"]):
                out["skipped"].append((it.ticker, it.side, "run time budget used up (next run)"))
                break
            same = [r for r in rows if r.get("mode") == mode]
            room, cwhy = capacity(it, same, t | {"_mode_hint": mode}, sp, now,
                                  [p for p in planned if plan_mode[id(p)] == mode])
            if room < 1:
                out["skipped"].append((it.ticker, it.side, cwhy))
                continue
            if it.count > room:
                it.count = room             # shrink to what the market can take
            others = [p for p in planned if plan_mode[id(p)] == mode]
            why = vet(it, same, t, sp, now, others, mode)
            while it.taker and why and "cap" in why and it.count > 1:
                it.count -= 1               # LOCKs shrink to fit the caps instead of skipping
                why = vet(it, same, t, sp, now, others, mode)
            if why in ("overall open-$ cap", "strategy open-$ cap") and not dry_run:
                try:
                    if free_cap_for(http, it, why, mode, rows, others, t, specs, now, client, cfg, dry_run):
                        same = [r for r in rows if r.get("mode") == mode]
                        why = vet(it, same, t, sp, now, others, mode)
                except Exception as exc:
                    log.warning("free cap for %s: %s", it.ticker, exc)
            if why:
                out["skipped"].append((it.ticker, it.side, why))
                continue
            planned.append(it)
            plan_mode[id(it)] = mode
    cash = None
    if client is not None and not dry_run and any(plan_mode[id(p)] == "live" for p in planned):
        try:
            # never spend more than Exchange 0 (the shard our markets live on) holds; the
            # 15-min bot's cash sits on Exchange 2 and isn't ours to count
            cash = client.cash_on(0) if hasattr(client, "cash_on") else client.balance()
        except Exception as exc:
            log.warning("balance check failed: %s", exc)
            cash = 0.0                   # can't see the balance: no live orders this run
            out["stops"].append(f"balance check failed: {exc}")
    rejected_live: list[str] = []
    for it in planned:
        mode = plan_mode[id(it)]
        if mode == "live" and cash is not None:
            unit = it.price + (kalshi.fee_per_contract(it.price) if it.taker else 0.0)
            fit = int((cash + 1e-9) // unit)
            if fit < 1:
                out["skipped"].append((it.ticker, it.side, f"not enough cash (${cash:.2f})"))
                continue
            it.count = min(it.count, fit)
        row = {"id": f"{it.ticker}|{it.side}", "client_id": client_id(it, mode, now), "mode": mode,
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
                if it.taker:
                    o = client.buy_now(it.ticker, it.side, it.price, it.count, row["client_id"])
                else:
                    o = client.place_limit(it.ticker, it.side, it.price, it.count, row["client_id"])
            except (HttpError, OrderRejected) as exc:
                log.warning("order %s %s rejected: %s", it.ticker, it.side, exc)
                if "post only cross" in str(exc):
                    # the price moved onto our bid since the strategy priced it; Kalshi
                    # refused it so we never pay the ask. Normal, not worth a push.
                    out["skipped"].append((it.ticker, it.side, "price moved (post-only cross)"))
                    continue
                out["skipped"].append((it.ticker, it.side, f"rejected: {exc}"))
                rejected_live.append(f"{it.strategy} {it.ticker}: {str(exc)[:120]}")
                continue
            row["order_id"] = o.get("order_id", "")
        if it.taker:
            # immediate-or-cancel: what filled is ours, the rest is already cancelled
            got = order_fill_count(o, it.count) if mode == "live" else it.count
            if got < 1:
                out["skipped"].append((it.ticker, it.side, "nothing left at the ask"))
                continue
            fee = kalshi.taker_fee(got, it.price)
            row.update(count=got, filled=got, status="filled", fee=f"{fee:.2f}",
                       filled_at=now.isoformat(timespec="seconds"))
            if mode == "live":
                cash = (cash or 0.0) - got * it.price - fee
                _alert(http, cfg, f"BOUGHT: {it.side.upper()} {got} @ {round(it.price * 100)}¢",
                       f"{it.title or it.ticker}\n{it.strategy} · real money · ${got * it.price + fee:.2f}"
                       f" incl. ${fee:.2f} fee", dry_run, 4, ["moneybag"], bundle=True)
        elif mode == "live" and cash is not None:
            cash -= it.count * it.price
        rows.append(row)
        out["placed"].append(row)

    if rejected_live:
        # Kalshi refused real orders: tell Alex (at most once every 3 hours)
        last = parse_iso(state.get("reject_alert_at"))
        if not last or now - last >= timedelta(hours=3):
            state["reject_alert_at"] = now.isoformat(timespec="seconds")
            _alert(http, cfg, f"Kalshi rejected {len(rejected_live)} live order(s)",
                   "\n".join(rejected_live[:4]) + "\nNothing was bought for these. Other orders still go out; "
                   "set TRADING_MODE=off to stop everything.", dry_run, 4, ["warning"])

    # ---- early exits (judgement: sell only when it beats holding)
    if now.minute % 30 < 5 or any(r.get("mode") == "live" and r.get("status") == "filled" for r in rows):
        try:
            out["exits"] = exits(http, rows, now, specs, client, cfg, dry_run, set())
        except Exception as exc:
            log.warning("exits failed: %s", exc)

    if not dry_run:
        if state.get("archive_day") != today:
            try:
                rows = archive(rows, now)
                state["archive_day"] = today
            except Exception as exc:      # archiving is housekeeping; never block trading
                log.warning("archive failed: %s", exc)
        save(rows)
        write_json(STATE, state)
    if out["skipped"]:
        why: dict[str, int] = {}
        for _, _, w in out["skipped"]:
            k = re.sub(r"\(\$[0-9.]+\)", "", str(w)).strip()[:60]
            why[k] = why.get(k, 0) + 1
        log.info("skipped because: %s", ", ".join(f"{k} x{n}" for k, n in
                                                 sorted(why.items(), key=lambda x: -x[1])))
    log.info("trade (%s, strategies %s): placed %d, skipped %d, stops %s, open $%.2f, "
             "P&L paper $%+.2f live $%+.2f", gmode, modes, len(out["placed"]), len(out["skipped"]),
             out["stops"], exposure([r for r in rows if r.get("mode") == gmode]),
             totals["paper"], totals["live"])
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
             + (f" · **HALTED: {', '.join(state['halted'])}**" if state.get("halted") else ""), "",
             scaleup.describe(scaleup.params(cfg), t["bankroll"]), ""]
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
    lines.append("")
    ap = allocator.params(cfg)
    if ap["enabled"]:
        try:
            store = dict(state.get("allocator", {}) or {"graded_at": "9999-01-01T00:00:00+00:00"})
            names = [n for n in strategies() if (cfg.raw.get("trading", {}).get(n) or {}).get("enabled", True)]
            now = datetime.now(ZoneInfo("UTC"))
            m15 = allocator.m15_names(cfg)
            online = os.environ.get("GITHUB_ACTIONS") == "true"   # tests and local runs stay offline
            m15_rows = allocator.fetch_m15(Http(cfg.user_agent), cfg, now) if m15 and online else []
            alloc = allocator.update(cfg, load_all(), weather_paper_orders(), names + m15, now, store,
                                     m15_rows=m15_rows)
            lines += allocator.report_section(alloc)
        except Exception as exc:
            log.warning("allocator report failed: %s", exc)
    return lines
