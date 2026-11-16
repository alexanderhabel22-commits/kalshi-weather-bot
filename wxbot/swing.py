"""$500-by-Friday swing (Alex, Nov 16 2026: "$500 or die trying", "your main goal is $500
in the account by EOW, do what you need to do to get there").

Bold play: when the goal is far above the bankroll and no measured edge is big enough to get
there in time, the best chance of hitting it is a few big bets, each sized so that a win
gets as close to the target as the cash allows, and never a bet bigger than needed. With
fairly priced markets the chance is about bankroll / target (~10% from ~$58); fees and the
spread take a bit off each step, so fewer, bigger steps are better than many small ones.

Two tracks, each on its own Kalshi balance (exchange shard; the API key cannot move cash
between them):
  main    shard 0: S&P 500 daily close above/below (KXINXU, 4 PM ET), one bet per weekday
  crypto  shard 2: Bitcoin daily 5 PM ET above/below (KXBTCD), one bet per day

Each bet buys YES on the "above" strike whose ask is closest to `aim` (~30c, ~3.3x), with
an immediate-or-cancel order (taker: our research found resting bids on crypto ladders get
adversely filled, -3 to -10c). Stake = all of that track's cash, or less if a win would
overshoot the target. Positions are held to settlement. The swing stops at the target,
at `end`, or when a track's cash is under `min_stake`.

While the swing is active, every other main-bot strategy runs on PAPER (pause_others), so
the cash goes to the swing; positions already open are still followed and settled.
State: data/orders/swing.json (committed by the trade workflow). One phone push per bet
and per result.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import kalshi
from .kalshi_api import KalshiClient, OrderRejected, check_swing_order, fill_qty
from .util import DATA_DIR, Config, Http, parse_iso, read_json, write_json

log = logging.getLogger("wxbot")

STATE = DATA_DIR / "orders" / "swing.json"
ET = ZoneInfo("America/New_York")

DEFAULTS = {
    "enabled": False,
    "target": 500.0,
    "end": "",                  # ISO time; nothing new after this
    "pause_others": True,
    "aim": 0.30,
    "min_price": 0.20,
    "max_price": 0.42,
    "slip": 0.02,               # IOC limit = ask + slip (walks a thin top of book)
    "min_stake": 1.0,
    "max_tries": 4,             # IOC attempts per bet while the stake isn't filled
}


def params(cfg: Config) -> dict:
    raw = dict(cfg.raw.get("swing", {}) or {})
    tracks = {k: v for k, v in raw.items() if isinstance(v, dict)}
    p = {**DEFAULTS, **{k: v for k, v in raw.items() if not isinstance(v, dict)}}
    p["tracks"] = tracks
    return p


def load() -> dict:
    st = read_json(STATE, {}) or {}
    st.setdefault("bets", [])
    return st


def active(cfg: Config, now: datetime, state: dict | None = None) -> bool:
    """True while the swing runs (other strategies then trade on paper)."""
    p = params(cfg)
    if not p["enabled"]:
        return False
    end = parse_iso(p["end"]) if p["end"] else None
    if end is not None and now >= end:
        return False
    st = state if state is not None else load()
    return not st.get("done")


def pause_others(cfg: Config, now: datetime) -> bool:
    return bool(params(cfg)["pause_others"]) and active(cfg, now)


# ---------------------------------------------------------------- picking

def close_time(day, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=ET)


def pick(markets: list[dict], close: datetime, p: dict) -> dict | None:
    """The "above" strike closing at `close` whose YES ask is nearest `aim` (inside the band)."""
    best = None
    for m in markets:
        if m.get("strike_type") not in ("greater", "greater_or_equal"):
            continue
        c = parse_iso(m.get("close_time"))
        if c is None or abs((c - close).total_seconds()) > 60:
            continue
        ask = kalshi.price(m, "yes_ask")
        if ask is None or not (p["min_price"] - 1e-9 <= ask <= p["max_price"] + 1e-9):
            continue
        d = abs(ask - p["aim"])
        if best is None or d < best[0]:
            best = (d, m, ask)
    if best is None:
        return None
    return {**best[1], "_ask": best[2]}


def stake_for(cash: float, total: float, target: float, price: float) -> float:
    """Bold play: bet what a win needs to reach the target (net of the stake coming back),
    or all the cash if that's not enough."""
    need = target - total
    if need <= 0:
        return 0.0
    want = need * price / (1.0 - price)
    return round(min(cash, want), 2)


def contracts_for(stake: float, price: float) -> int:
    per = price + kalshi.fee_per_contract(price) + 0.0005
    return max(0, int(math.floor(stake / per)))


# ---------------------------------------------------------------- run

def _push(http, cfg, title, body, dry_run):
    try:
        from . import notify
        notify.send(http, cfg, title, body, priority=4, tags=["dart"], dry_run=dry_run)
    except Exception as exc:     # a push must never break the swing
        log.warning("swing push failed: %s", exc)


def _account_total(client: KalshiClient) -> float:
    from . import balance
    return float(balance.fetch(client).get("total") or 0.0)


def settle(http: Http, cfg: Config, st: dict, now: datetime, dry_run: bool) -> list[str]:
    out = []
    for b in st["bets"]:
        if b.get("status") != "open":
            continue
        c = parse_iso(b.get("close"))
        if c is None or now < c:
            continue
        m = kalshi.get_market(http, b["ticker"])
        res = (m or {}).get("result")
        if res not in ("yes", "no"):
            continue
        won = res == b["side"]
        pay = float(b["count"]) if won else 0.0
        b["pnl"] = round(pay - float(b["cost"]) - float(b.get("fee", 0)), 2)
        b["status"] = "won" if won else "lost"
        b["settled_at"] = now.isoformat()
        line = (f"{b['track']}: {'WON' if won else 'LOST'} {b['count']:g} x {b['label']} "
                f"-> {'+' if b['pnl'] >= 0 else ''}${b['pnl']:.2f}")
        out.append(line)
        _push(http, cfg, f"Swing {'WIN' if won else 'loss'}: {b['track']}", line, dry_run)
    return out


def _bet_for(st: dict, track: str, event: str) -> dict | None:
    return next((b for b in st["bets"] if b["track"] == track and b["event"] == event), None)


def run(cfg: Config, *, now: datetime, http: Http, client: KalshiClient | None = None,
        dry_run: bool = False) -> list[str]:
    p = params(cfg)
    if not p["enabled"]:
        return []
    st = load()
    lines = settle(http, cfg, st, now, dry_run)
    if not active(cfg, now, st):
        if lines and not dry_run:
            write_json(STATE, st)
        return lines
    if client is None:
        client = KalshiClient.from_env(http)
    if client is None:
        lines.append("no API key: swing skipped")
        return lines
    import os
    if (os.environ.get("TRADING_MODE") or "").strip().lower() == "off":
        lines.append("kill switch off: swing skipped")
        return lines
    total = _account_total(client)
    st["last_total"] = round(total, 2)
    st["last_at"] = now.isoformat()
    if total >= float(p["target"]):
        st["done"] = f"target reached ${total:.2f} at {now.isoformat()}"
        _push(http, cfg, "Swing: $500 TARGET HIT", f"Account ${total:.2f}. Swing stopped; "
              "other strategies go back to normal.", dry_run)
        lines.append(st["done"])
        if not dry_run:
            write_json(STATE, st)
        return lines
    local = now.astimezone(ET)
    for name, tr in p["tracks"].items():
        if not tr.get("enabled", True) or local.weekday() not in tr.get("days", [0, 1, 2, 3, 4]):
            continue
        close = close_time(local.date(), int(tr["close_hour_et"]))
        h0, m0 = (int(x) for x in str(tr.get("from_et", "10:00")).split(":"))
        start = local.replace(hour=h0, minute=m0, second=0, microsecond=0)
        last = close - timedelta(minutes=int(tr.get("last_entry_min", 30)))
        if not (start <= local <= last):
            continue
        try:
            lines += _track(name, tr, p, st, close, total, now, http, client, cfg, dry_run)
        except Exception as exc:   # one track must not stop the other
            log.warning("swing %s failed: %s", name, exc)
            lines.append(f"{name}: error {exc}")
    if not dry_run:
        write_json(STATE, st)
    return lines


def _track(name, tr, p, st, close, total, now, http, client, cfg, dry_run) -> list[str]:
    series = tr["series"]
    markets = kalshi.list_open_markets(http, series)
    event_ids = {m.get("event_ticker") for m in markets
                 if (c := parse_iso(m.get("close_time"))) and abs((c - close).total_seconds()) <= 60}
    if not event_ids:
        return [f"{name}: no {series} event closing {close:%m-%d %H:%M} ET"]
    event = sorted(event_ids)[0]
    bet = _bet_for(st, name, event)
    if bet and (bet.get("tries", 0) >= int(p["max_tries"]) or bet.get("filled_up")):
        return []
    cash = float(client.cash_on(int(tr.get("shard", 0))))
    if cash < float(p["min_stake"]):
        return [f"{name}: cash ${cash:.2f} under min stake"]
    if bet:   # top up the same market only
        m = next((x for x in markets if x.get("ticker") == bet["ticker"]), None)
        if m is None:
            return []
        ask = kalshi.price(m, "yes_ask")
        if ask is None or not (p["min_price"] <= ask <= p["max_price"]):
            return []
        m = {**m, "_ask": ask}
        stake = min(cash, max(0.0, float(bet["stake"]) - float(bet["cost"]) - float(bet.get("fee", 0))))
    else:
        m = pick(markets, close, p)
        if m is None:
            return [f"{name}: no strike priced {p['min_price']:.2f}-{p['max_price']:.2f}"]
        stake = stake_for(cash, total, float(p["target"]), m["_ask"])
    ask = m["_ask"]
    n = contracts_for(stake, min(0.99, ask + float(p["slip"])))
    if n < 1 or stake < float(p["min_stake"]):
        if bet:
            bet["filled_up"] = True
        return []
    limit = round(min(0.99, ask + float(p["slip"])), 2)
    label = m.get("yes_sub_title") or m.get("subtitle") or m["ticker"]
    if bet is None:
        bet = {"track": name, "event": event, "ticker": m["ticker"], "side": "yes", "label": label,
               "close": close.isoformat(), "stake": round(stake, 2), "count": 0.0, "cost": 0.0,
               "fee": 0.0, "tries": 0, "placed_at": now.isoformat(), "status": "open",
               "total_before": round(total, 2), "ask_seen": ask}
        st["bets"].append(bet)
    bet["tries"] = int(bet.get("tries", 0)) + 1
    if dry_run:
        return [f"{name}: DRY would buy {n} YES {m['ticker']} at <= {limit:.2f} (stake ${stake:.2f})"]
    cid = f"swing-{name}-{event}-{bet['tries']}"[:60]
    try:
        check_swing_order("yes", limit, n)
        o = client.buy_now(m["ticker"], "yes", limit, n, cid, swing=True)
    except OrderRejected as exc:
        return [f"{name}: rejected {exc}"]
    except Exception as exc:
        return [f"{name}: order failed {exc}"]
    got = fill_qty(o, n)
    if got <= 0:
        if bet["count"] <= 0 and bet["tries"] >= int(p["max_tries"]):
            bet["status"], bet["filled_up"] = "nofill", True     # give up on this event
        return [f"{name}: no fill at {limit:.2f} (try {bet['tries']})"]
    cost = _num(o.get("taker_fill_cost_dollars")) or _num(o.get("fill_cost_dollars"))
    if cost is None:
        cost = round(got * ask, 2)
    fee = _num(o.get("taker_fees_dollars")) or _num(o.get("fees_dollars"))
    if fee is None:
        fee = kalshi.taker_fee(int(math.ceil(got)), ask)
    bet["count"] = round(float(bet["count"]) + got, 2)
    bet["cost"] = round(float(bet["cost"]) + cost, 2)
    bet["fee"] = round(float(bet["fee"]) + fee, 2)
    if got >= n - 1e-6:
        bet["filled_up"] = True
    avg = bet["cost"] / bet["count"] if bet["count"] else ask
    win = bet["count"] - bet["cost"] - bet["fee"]
    line = (f"{name}: bought {got:g} YES '{label}' at ~{avg * 100:.0f}c (${cost:.2f} + ${fee:.2f} fee). "
            f"Win pays ${bet['count']:.2f} (+${win:.2f}); account now ~${total:.2f}, "
            f"~${total + win:.2f} if it wins. Settles {close:%a %-I %p} ET.")
    _push(http, cfg, f"Swing bet placed ({name})", line, dry_run)
    return [line]


def _num(x):
    try:
        return round(float(x), 4)
    except (TypeError, ValueError):
        return None


def ledger_lines(st: dict | None = None) -> list[str]:
    st = st if st is not None else load()
    out = []
    for b in st.get("bets", []):
        pnl = "" if b.get("pnl") is None else f"{b['pnl']:+.2f}"
        out.append(f"{b['placed_at'][:16]} {b['track']} {b['count']:g} x {b['label']} "
                   f"cost ${b['cost'] + b.get('fee', 0):.2f} {b['status']} {pnl}")
    return out
