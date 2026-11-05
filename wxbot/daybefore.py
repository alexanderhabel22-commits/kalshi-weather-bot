"""Paper trading for the day-before maker strategy (started Nov 5, 2026).

About 36 hours before a market closes (midday the day before), the bot works
out each bracket's chance from four forecast models (bias-corrected per city,
weighted by recent accuracy), blends that with the market price, and places a
paper limit order 1c inside the spread on any side worth 6c+ more than the
order price. Nothing is sent to Kalshi. After close it checks Kalshi's public
trade tape: the order counts as filled if a later trade printed at our price
or better. Then it grades the order against the settlement. Makers pay no
fee on weather markets.

Parameters come from data/daybefore_params.json (tools/daybefore_params.py,
built from the backtest data). Orders are kept in data/paper_orders.csv.
"""
from __future__ import annotations

import csv
import logging
import math
from datetime import datetime, timedelta

from . import kalshi
from .backtest import bracket_prob
from .stations import cli_window
from .util import DATA_DIR, Http, HttpError, parse_iso, read_json

log = logging.getLogger("wxbot")

PARAMS = DATA_DIR / "daybefore_params.json"
ORDERS = DATA_DIR / "paper_orders.csv"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FIELDS = ["id", "placed_at", "ticker", "event", "series", "city", "metric", "date", "bracket",
          "side", "price", "bid", "ask", "model_p", "blend_p", "edge", "mu", "sd",
          "close", "status", "touched", "through", "result", "pnl", "settled_at"]
# status: open -> filled | unfilled (after close) ; pnl only for filled orders
# touched: a trade printed at our price (likely fill, if our order was first in line)
# through: a trade printed strictly better than our price (certain fill)


def load_orders() -> list[dict]:
    if not ORDERS.exists():
        return []
    with open(ORDERS, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_orders(rows: list[dict]) -> None:
    ORDERS.parent.mkdir(parents=True, exist_ok=True)
    tmp = ORDERS.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(ORDERS)


def _sig(z: float) -> float:
    return 1 / (1 + math.exp(-max(min(z, 30), -30)))


def _logit(p: float) -> float:
    p = min(max(p, 0.005), 0.995)
    return math.log(p / (1 - p))


# ---------------------------------------------------------------- forecast

def fetch_models(http: Http, st, models: list[str]) -> dict[str, list] | None:
    """Hourly °F per model from Open-Meteo's latest runs: {"time": [...], model: [...]}."""
    js = http.get_json(FORECAST_URL, params={
        "latitude": f"{st.lat:.4f}", "longitude": f"{st.lon:.4f}", "models": ",".join(models),
        "hourly": "temperature_2m", "temperature_unit": "fahrenheit", "timezone": "GMT",
        "forecast_days": 3})
    h = js.get("hourly") or {}
    out = {"time": h.get("time") or []}
    for m in models:
        vals = h.get(f"temperature_2m_{m}")
        if vals is None and len(models) == 1:
            vals = h.get("temperature_2m")
        if vals:
            out[m] = vals
    return out


def day_extreme(hourly: dict, model: str, day, tz: str, metric: str) -> float | None:
    start, _ = cli_window(day, tz)
    want = {(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(24)}
    vals = [v for t, v in zip(hourly.get("time") or [], hourly.get(model) or [])
            if t in want and v is not None]
    if len(vals) < 20:
        return None
    return max(vals) if metric == "high" else min(vals)


def forecast(params: dict, hourly: dict, day, tz: str, metric: str) -> tuple[float, float] | None:
    w, bias = params["weights"], params["bias"]
    fc = {m: day_extreme(hourly, m, day, tz, metric) for m in w}
    if any(v is None for v in fc.values()):
        return None
    mu = sum(w[m] * (fc[m] + bias[m]) for m in w) / sum(w.values())
    return mu, float(params["sd"])


# ---------------------------------------------------------------- orders

def plan_orders(markets: list[dict], mu: float, sd: float, blend: list[float],
                threshold: float) -> list[dict]:
    """Paper limit orders for one event: 1c inside the spread, blend edge >= threshold."""
    out = []
    for m in markets:
        br = kalshi.parse_bracket(m)
        bid, ask = kalshi.price(m, "yes_bid"), kalshi.price(m, "yes_ask")
        if br is None or bid is None or ask is None or not (0 < bid < ask < 1):
            continue
        p_model = bracket_prob(mu, sd, br[0], br[1])
        mid = (bid + ask) / 2
        p = _sig(blend[0] + blend[1] * _logit(mid) + blend[2] * _logit(p_model))
        yes_px = round(bid + 0.01, 2)                 # buy YES at bid + 1c
        no_px = round(1 - (ask - 0.01), 2)            # buy NO = sell YES at ask - 1c
        base = {"ticker": m.get("ticker", ""), "bracket": kalshi.bracket_label(*br),
                "bid": bid, "ask": ask, "model_p": p_model}
        if yes_px < ask and 0.03 <= yes_px <= 0.97 and p - yes_px >= threshold:
            out.append(dict(base, side="yes", price=yes_px, blend_p=p, edge=p - yes_px))
        if ask - 0.01 > bid and 0.03 <= no_px <= 0.97 and (1 - p) - no_px >= threshold:
            out.append(dict(base, side="no", price=no_px, blend_p=1 - p, edge=(1 - p) - no_px))
    return out


def place(http: Http, orders: list[dict], tomorrow: dict, now: datetime,
          state: dict, params: dict | None = None) -> int:
    """tomorrow: event -> (SeriesInfo, markets). Places paper orders once per event
    when the event is about `hours_before_close` from closing."""
    params = params or read_json(PARAMS)
    if not params:
        return 0
    H = float(params.get("hours_before_close", 36))
    done = state.setdefault("daybefore_done", {})
    cache: dict[str, dict | None] = {}
    placed = 0
    for ev, (si, ms) in tomorrow.items():
        if ev in done or si.ticker not in params.get("series", {}):
            continue
        closes = [c for c in (parse_iso(m.get("close_time")) for m in ms) if c]
        if not closes:
            continue
        hours = (min(closes) - now).total_seconds() / 3600
        if not (H - 1.5 <= hours <= H + 1.5):
            continue
        sp = params["series"][si.ticker]
        st = si.station
        if st.icao not in cache:
            try:
                cache[st.icao] = fetch_models(http, st, sorted(sp["weights"]))
            except HttpError as exc:
                log.warning("paper forecast %s: %s", st.icao, exc)
                cache[st.icao] = None
        if cache[st.icao] is None:
            continue
        day = kalshi.event_date(ev)
        f = forecast(sp, cache[st.icao], day, st.tz, si.metric)
        done[ev] = now.isoformat(timespec="seconds")
        if f is None:
            log.info("paper %s: missing model forecast", ev)
            continue
        mu, sd = f
        for o in plan_orders(ms, mu, sd, params["blend"], float(params.get("threshold", 0.06))):
            orders.append({
                "id": f"{o['ticker']}|{o['side']}", "placed_at": now.isoformat(timespec="seconds"),
                "ticker": o["ticker"], "event": ev, "series": si.ticker, "city": si.city,
                "metric": si.metric, "date": day.isoformat(), "bracket": o["bracket"],
                "side": o["side"], "price": f"{o['price']:.2f}", "bid": f"{o['bid']:.2f}",
                "ask": f"{o['ask']:.2f}", "model_p": f"{o['model_p']:.4f}",
                "blend_p": f"{o['blend_p']:.4f}", "edge": f"{o['edge']:.4f}",
                "mu": f"{mu:.1f}", "sd": f"{sd:.2f}",
                "close": min(closes).isoformat(timespec="seconds"), "status": "open"})
            placed += 1
        log.info("paper %s: forecast %.1f±%.1f, %d orders", ev, mu, sd, placed)
    # forget events more than 3 days old
    cutoff = (now - timedelta(days=3)).isoformat()
    state["daybefore_done"] = {k: v for k, v in done.items() if v >= cutoff}
    return placed


# ---------------------------------------------------------------- fills and grading

def trade_prices(http: Http, ticker: str, since: datetime, until: datetime) -> list[float]:
    """YES prices (dollars) of every trade on `ticker` in [since, until]."""
    out, cursor = [], None
    for _ in range(20):
        params = {"ticker": ticker, "limit": 1000, "min_ts": int(since.timestamp()),
                  "max_ts": int(until.timestamp())}
        if cursor:
            params["cursor"] = cursor
        js = http.get_json(f"{kalshi.BASE}/markets/trades", params=params)
        for t in js.get("trades") or []:
            p = kalshi.price(t, "yes_price")
            if p is not None:
                out.append(p)
        cursor = js.get("cursor")
        if not cursor:
            break
    return out


def fill_flags(side: str, price: float, prints: list[float]) -> tuple[bool, bool]:
    """(touched, through) for a resting buy at `price` on `side`."""
    if side == "yes":                        # we bid `price` for YES
        return any(p <= price + 1e-9 for p in prints), any(p < price - 1e-9 for p in prints)
    level = 1 - price                        # we offer YES at 1 - price
    return any(p >= level - 1e-9 for p in prints), any(p > level + 1e-9 for p in prints)


def settle(http: Http, orders: list[dict], now: datetime) -> int:
    n = 0
    markets: dict[str, dict | None] = {}
    for o in orders:
        if o.get("status") != "open":
            continue
        tk = o["ticker"]
        close = parse_iso(o.get("close"))
        if close and now < close:
            continue
        if tk not in markets:
            markets[tk] = kalshi.get_market(http, tk)
        m = markets[tk]
        if not m:
            continue
        close = parse_iso(m.get("close_time"))
        res = (m.get("result") or "").lower()
        if close is None or now < close or res not in ("yes", "no"):
            continue
        try:
            prints = trade_prices(http, tk, parse_iso(o["placed_at"]), close)
        except HttpError as exc:
            log.warning("paper trades %s: %s", tk, exc)
            continue
        price = float(o["price"])
        touched, through = fill_flags(o["side"], price, prints)
        won = res == o["side"]
        o.update(status="filled" if touched else "unfilled", touched=int(touched),
                 through=int(through), result=res, settled_at=now.isoformat(timespec="seconds"),
                 pnl=f"{(1.0 if won else 0.0) - price:.2f}" if touched else "")
        n += 1
    return n


# ---------------------------------------------------------------- report

def report_section(orders: list[dict]) -> list[str]:
    lines = ["## Paper trading: day-before limit orders", "",
             "Orders the bot would rest 1¢ inside the spread about 36 hours before close "
             "(no money). Filled = a real trade printed at our price or better.", ""]
    if not orders:
        return lines + ["No paper orders yet.", ""]
    done = [o for o in orders if o.get("status") in ("filled", "unfilled")]
    filled = [o for o in done if o["status"] == "filled"]
    sure = [o for o in filled if o.get("through") == "1"]
    opn = [o for o in orders if o.get("status") == "open"]

    def line(label, rows):
        if not rows:
            return f"- {label}: none yet"
        pnl = sum(float(o["pnl"]) for o in rows)
        cost = sum(float(o["price"]) for o in rows)
        wins = sum(float(o["pnl"]) > 0 for o in rows)
        return (f"- {label}: {len(rows)} contracts, {wins} won, P&L ${pnl:+.2f} on ${cost:.2f} "
                f"({pnl / cost * 100:+.1f}%)")

    lines += [f"Placed {len(orders)} · open {len(opn)} · graded {len(done)} "
              f"· filled {len(filled)} ({len(filled) * 100 // max(len(done), 1)}%)", "",
              line("All fills (1 contract each)", filled),
              line("Certain fills only (traded through our price)", sure), ""]
    by_day: dict[str, list] = {}
    for o in filled:
        by_day.setdefault(o["date"], []).append(o)
    if by_day:
        lines += ["| Day | Fills | Won | P&L |", "|---|---|---|---|"]
        for d in sorted(by_day)[-10:]:
            rs = by_day[d]
            lines.append(f"| {d} | {len(rs)} | {sum(float(o['pnl']) > 0 for o in rs)} "
                         f"| ${sum(float(o['pnl']) for o in rs):+.2f} |")
        lines.append("")
    return lines
