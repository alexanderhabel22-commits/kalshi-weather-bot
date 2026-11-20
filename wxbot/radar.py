"""New-listing radar + edge-fade monitor (added Nov 20, 2026). PAPER ONLY, never orders.

Why: Alex (Nov 20) wants to catch new Kalshi markets as fast as possible and test our
known mature-market rules on them before sharp bots arrive, and to know when an edge
has faded. Runs hourly in its own workflow (.github/workflows/radar.yml), ~2-3 min.

Each pass:
  1. Lists every OPEN event on Kalshi (~110 public pages) and groups them by series.
     A series that has an open event for the first time is a NEW LISTING (brand-new
     series, or an old empty one like the farm dailies KXCORND...). The first pass only
     records a baseline.
  2. For 14 days after listing (and always for the `watch` series in config), reads the
     series' open markets, logs spread / volume snapshots and runs three known rules as
     1-contract PAPER orders (one per rule per market):
       longshot_no     YES at 3-15c: rest a NO bid 1c above the best NO bid
       fav_24h         favorite side at 85-96c closing in 1-24 h: rest a bid 1c above best
       frozen_bracket  range markets while the underlying futures are closed (hours in
                       [radar.frozen]): sell the top YES ranges at 35c (NO bid 65c), take
                       YES bids already >= 35c; cancelled when the underlying reopens
     A resting paper order counts as filled only when a later trade went THROUGH our price
     (back of queue, like our replays). Unfilled orders are settled too, so we can see
     whether the fills we miss would have won (adverse selection).
  3. Edge-fade monitor for every REAL-money strategy: fill rate, c/ct all-time and last
     20 fills, what our unfilled orders would have made, spread trend of its series, and
     its pre-written kill rule ([radar.kill_rules]). A rule hit is pushed once; the radar
     never changes trading itself.
Pushes (max 4/day): a watched series lists for the first time; a paper rule on a new
listing reaches the "worth a real test" bar (says what we'd trade, cap, evidence); a
kill rule or fade flag on a real strategy. Real money always needs Alex's OK.
State: data/radar.json. Report: digest_lines() in the 9 AM daily report.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import kalshi, notify
from .util import DATA_DIR, Config, Http, HttpError, parse_iso, read_json, write_json

log = logging.getLogger("wxbot")

RADAR = DATA_DIR / "radar.json"
ET = ZoneInfo("America/New_York")
SPORTS = {"Sports"}
TEST_DAYS = 14
MAX_SERIES_PER_RUN = 40
MAX_TRADE_CHECKS = 60
MAX_RESULT_CHECKS = 60
MAX_PUSHES_PER_DAY = 4
KEEP_SNAPS = 48 * 21          # spread snapshots per series, twice an hour (3 weeks)
KEEP_PAPER = 4000
DEFAULT_WATCH = ["KXCORND", "KXSOYBEAND", "KXWHEATD", "KXLCATTLED", "KXCOCOAD", "KXCOFFEED",
                 "KXCORNW", "KXSOYBEANW", "KXWHEATW", "KXLCATTLEW", "KXCOCOAW", "KXCOFFEEW"]
# Underlying futures closed (ET, hh:mm). close -> open wraps overnight. sunday_open = the
# Sunday-evening reopen (None = reopens Monday at `open`).
DEFAULT_FROZEN = {
    "grains": {"series": ["KXCORN", "KXSOYBEAN", "KXWHEAT"], "close": "14:20", "open": "20:00",
               "sunday_open": "20:00"},
    "cattle": {"series": ["KXLCATTLE"], "close": "14:05", "open": "09:30", "sunday_open": None},
    "cocoa": {"series": ["KXCOCOA"], "close": "13:30", "open": "04:45", "sunday_open": None},
    "coffee": {"series": ["KXCOFFEE"], "close": "13:30", "open": "04:15", "sunday_open": None},
}
DEFAULT_KILL = {"min_fills": 20, "max_cpc": -5.0}
# "worth a real test" bar for a paper rule on one new series
PROMISE_FILLS, PROMISE_CPC = 10, 3.0
RULES = ("longshot_no", "fav_24h", "frozen_bracket")


def load() -> dict:
    return read_json(RADAR, {}) or {}


def save(db: dict) -> None:
    write_json(RADAR, db)


def _p(m: dict, k: str) -> float | None:
    v = kalshi.price(m, k)
    return v if v is not None and 0 <= v <= 1 else None


def maker_fee(p: float) -> float:
    return math.ceil(round(0.0175 * p * (1 - p) * 100, 6)) / 100


def series_of(ticker: str) -> str:
    return (ticker or "").split("-", 1)[0]


# ------------------------------------------------------------------ listings

def fetch_open_events(http: Http, max_pages: int = 200) -> list[dict] | None:
    out, cursor = [], None
    for _ in range(max_pages):
        params = {"status": "open", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        js = http.get_json(f"{kalshi.BASE}/events", params=params)
        out += js.get("events") or []
        cursor = js.get("cursor")
        if not cursor or not js.get("events"):
            return out
    return None        # ran out of pages: incomplete, don't diff


def diff_listings(db: dict, events: list[dict], now: datetime) -> list[dict]:
    """Series with an open event for the first time. First call = baseline only."""
    stamp = now.isoformat(timespec="seconds")
    by_series: dict[str, list[dict]] = {}
    for e in events:
        s = e.get("series_ticker") or series_of(e.get("event_ticker", ""))
        by_series.setdefault(s, []).append(e)
    seen = set(db.get("series_seen") or [])
    new = []
    if seen:
        for s, evs in sorted(by_series.items()):
            if s in seen:
                continue
            cat = evs[0].get("category") or "?"
            item = {"series": s, "category": cat, "title": evs[0].get("title"),
                    "first_seen": stamp, "events": len(evs),
                    "example_event": evs[0].get("event_ticker"),
                    "exchange_index": evs[0].get("exchange_index")}
            db.setdefault("listings", {})[s] = item
            new.append(item)
    else:
        db["baseline_at"] = stamp
    db["series_seen"] = sorted(seen | set(by_series))
    db["open_events"] = len(events)
    db["open_series"] = len(by_series)
    return new


def active_series(db: dict, cfg: Config, now: datetime) -> list[str]:
    watch = list(cfg.get("radar", "watch", DEFAULT_WATCH) or [])
    seen = set(db.get("series_seen") or [])
    out = [s for s in watch if s in seen]
    for s, it in sorted((db.get("listings") or {}).items(), key=lambda kv: kv[1]["first_seen"],
                        reverse=True):
        if it.get("category") in SPORTS or s in out:
            continue
        if now - (parse_iso(it["first_seen"]) or now) <= timedelta(days=TEST_DAYS):
            out.append(s)
    return out[:MAX_SERIES_PER_RUN]


def open_markets(http: Http, series: str) -> list[dict]:
    out, cursor = [], None
    for _ in range(3):
        params = {"series_ticker": series, "status": "open", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        js = http.get_json(f"{kalshi.BASE}/markets", params=params)
        out += js.get("markets") or []
        cursor = js.get("cursor")
        if not cursor:
            break
    return out


def snapshot(ms: list[dict], now: datetime) -> dict:
    spreads, vol, quoted = [], 0.0, 0
    for m in ms:
        yb, ya = _p(m, "yes_bid"), _p(m, "yes_ask")
        if yb is not None and ya is not None and 0 < yb < ya < 1:
            spreads.append(round(ya - yb, 4))
            quoted += 1
        try:
            vol += float(m.get("volume_fp", m.get("volume")) or 0)
        except (TypeError, ValueError):
            pass
    spreads.sort()
    return {"t": now.isoformat(timespec="seconds"), "n": len(ms), "quoted": quoted,
            "spread": spreads[len(spreads) // 2] if spreads else None, "vol": round(vol, 1)}


# ------------------------------------------------------------------ frozen hours

def _hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def frozen_window(cfg: Config, series: str) -> dict | None:
    table = cfg.raw.get("radar", {}).get("frozen") or DEFAULT_FROZEN
    for w in table.values():
        if any(series.startswith(p) for p in w.get("series") or []):
            return w
    return None


def is_frozen(w: dict, t: datetime) -> bool:
    lt = t.astimezone(ET)
    hm = (lt.hour, lt.minute)
    close, opn = _hm(w["close"]), _hm(w["open"])
    wd = lt.weekday()                      # Mon 0 .. Sun 6
    if wd == 5:
        return True
    if wd == 6:
        so = w.get("sunday_open")
        return so is None or hm < _hm(so)
    if wd == 4 and hm >= close:
        return True
    if wd == 0 and w.get("sunday_open") is None and hm < opn:
        return True
    if close < opn:                        # closed in the afternoon, reopens the same evening
        return close <= hm < opn
    return hm >= close or hm < opn         # closed overnight


def next_open(w: dict, t: datetime) -> datetime:
    x = t.replace(second=0, microsecond=0)
    for _ in range(4 * 24 * 4):
        if not is_frozen(w, x):
            return x
        x += timedelta(minutes=15)
    return x


# ------------------------------------------------------------------ paper rules

def _book(m: dict, side: str) -> tuple[float, float] | None:
    yb, ya = _p(m, "yes_bid"), _p(m, "yes_ask")
    if yb is None or ya is None or not (0 < yb < ya < 1):
        return None
    return (yb, ya) if side == "yes" else (round(1 - ya, 2), round(1 - yb, 2))


def _maker_px(bid: float, ask: float) -> float:
    px = round(bid + 0.01, 2)
    return bid if px >= ask else px


def candidates(series: str, ms: list[dict], now: datetime, cfg: Config,
               have: set[tuple[str, str]]) -> list[dict]:
    out = []
    by_event: dict[str, list[dict]] = {}
    for m in ms:
        by_event.setdefault(m.get("event_ticker") or series_of(m.get("ticker", "")), []).append(m)
    w = frozen_window(cfg, series)
    frozen_now = w is not None and is_frozen(w, now)
    reopen = next_open(w, now) if frozen_now else None
    for ev, evms in by_event.items():
        long_, fav, brk = [], [], []
        for m in evms:
            tk = m.get("ticker") or ""
            close = parse_iso(m.get("close_time"))
            if not tk or close is None or (m.get("status") or "active") not in ("active", "open"):
                continue
            hours = (close - now).total_seconds() / 3600
            yb, ya = _p(m, "yes_bid"), _p(m, "yes_ask")
            if yb is None or ya is None or not (0 < yb < ya < 1):
                continue
            base = {"ticker": tk, "event": ev, "series": series, "close": m.get("close_time"),
                    "title": f"{m.get('title') or ''} {m.get('yes_sub_title') or ''}".strip()[:100]}
            # longshot NO fade: YES priced 3-15c
            if 1 <= hours <= 24 * 14 and 0.03 <= ya <= 0.15 and ("longshot_no", tk) not in have:
                b = _book(m, "no")
                px = _maker_px(*b)
                if px <= 0.97:
                    long_.append({**base, "rule": "longshot_no", "side": "no", "price": px,
                                  "taker": False, "_k": ya})
            # favorites, last 24 hours
            if 1 <= hours <= 24 and ("fav_24h", tk) not in have:
                side = "yes" if yb >= 0.5 else "no"
                b = _book(m, side)
                px = _maker_px(*b)
                if 0.85 - 1e-9 <= px <= 0.96 + 1e-9:
                    fav.append({**base, "rule": "fav_24h", "side": side, "price": px,
                                "taker": False, "_k": -px})
            # frozen-hours bracket fade
            if (frozen_now and (m.get("strike_type") or "") == "between" and close > reopen
                    and ("frozen_bracket", tk) not in have):
                mid = (yb + ya) / 2
                if yb >= 0.35:
                    brk.append({**base, "rule": "frozen_bracket", "side": "no",
                                "price": round(1 - yb, 2), "taker": True, "_k": -mid,
                                "cancel_at": reopen.isoformat(timespec="seconds")})
                elif mid >= 0.15:
                    brk.append({**base, "rule": "frozen_bracket", "side": "no", "price": 0.65,
                                "taker": False, "_k": -mid,
                                "cancel_at": reopen.isoformat(timespec="seconds")})
        out += sorted(long_, key=lambda x: x["_k"])[:3]
        out += sorted(fav, key=lambda x: x["_k"])[:2]
        out += sorted(brk, key=lambda x: x["_k"])[:6]
    for o in out:
        o.pop("_k", None)
    return out


def place(db: dict, cands: list[dict], now: datetime) -> int:
    paper = db.setdefault("paper", [])
    stamp = now.isoformat(timespec="seconds")
    for c in cands:
        o = {**c, "placed_at": stamp, "status": "resting", "checked_at": stamp}
        if c.get("taker"):
            o.update(status="filled", filled_at=stamp)
        paper.append(o)
    return len(cands)


def through(o: dict, trades: list[dict]) -> bool:
    """A resting bid is filled only if a later trade went through its price."""
    px = float(o["price"])
    for t in trades:
        yp = _p(t, "yes_price")
        if yp is None:
            continue
        if o["side"] == "no" and yp > 1 - px + 1e-9:
            return True
        if o["side"] == "yes" and yp < px - 1e-9:
            return True
    return False


def _trades(http: Http, ticker: str, since: str) -> list[dict]:
    st = parse_iso(since)
    params = {"ticker": ticker, "limit": 500}
    if st:
        params["min_ts"] = int(st.timestamp())
    return http.get_json(f"{kalshi.BASE}/markets/trades", params=params).get("trades") or []


def _result(http: Http, ticker: str) -> str | None:
    m = http.get_json(f"{kalshi.BASE}/markets/{ticker}").get("market") or {}
    r = (m.get("result") or "").lower()
    return r if r in ("yes", "no") else None


def cents(side: str, price: float, result: str, taker: bool = False) -> float:
    fee = kalshi.taker_fee(1, price) if taker else maker_fee(price)
    win = 1.0 if side == result else 0.0
    return round(100 * (win - price - fee), 1)


def update_paper(http: Http, db: dict, now: datetime) -> None:
    stamp = now.isoformat(timespec="seconds")
    checks = results = 0
    for o in sorted(db.get("paper") or [], key=lambda x: x.get("checked_at") or ""):
        close = parse_iso(o.get("close"))
        if o["status"] == "resting":
            cancel = parse_iso(o.get("cancel_at"))
            end = min(x for x in (close, cancel) if x is not None) if (close or cancel) else None
            if checks < MAX_TRADE_CHECKS:
                checks += 1
                try:
                    tr = _trades(http, o["ticker"], o.get("checked_at") or o["placed_at"])
                    if end:
                        tr = [t for t in tr if (parse_iso(t.get("created_time")) or now) <= end]
                    if through(o, tr):
                        o.update(status="filled", filled_at=stamp)
                    o["checked_at"] = stamp
                except HttpError as exc:
                    log.info("radar trades %s: %s", o["ticker"], exc)
            if o["status"] == "resting" and end and now >= end and o["checked_at"] >= end.isoformat():
                o["status"] = "unfilled"
        if o["status"] in ("filled", "unfilled") and close and now > close + timedelta(minutes=30) \
                and results < MAX_RESULT_CHECKS:
            results += 1
            try:
                r = _result(http, o["ticker"])
            except HttpError as exc:
                log.info("radar result %s: %s", o["ticker"], exc)
                continue
            if r:
                o["result"] = r
                o["cents"] = cents(o["side"], float(o["price"]), r, bool(o.get("taker")))
                o["status"] = "settled" if o["status"] == "filled" else "settled-unfilled"
    db["paper"] = (db.get("paper") or [])[-KEEP_PAPER:]


def rule_stats(rows: list[dict]) -> dict:
    fills = [r for r in rows if r["status"] == "settled"]
    miss = [r for r in rows if r["status"] == "settled-unfilled"]
    done = len(fills) + len(miss) + sum(r["status"] == "unfilled" for r in rows)
    c = [r["cents"] for r in fills]
    mean = sum(c) / len(c) if c else None
    se = (math.sqrt(sum((x - mean) ** 2 for x in c) / (len(c) - 1)) / math.sqrt(len(c))
          if len(c) > 1 else None)
    return {"orders": len(rows), "open": sum(r["status"] in ("resting", "filled") for r in rows),
            "fills": len(fills), "fill_rate": (len(fills) + sum(r["status"] == "filled" for r in rows)) / done
            if done else None,
            "cpc": mean, "se": se,
            "missed_cpc": sum(r["cents"] for r in miss) / len(miss) if miss else None,
            "missed": len(miss)}


# ------------------------------------------------------------------ fade monitor

def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def live_fade(http: Http | None, db: dict, cfg: Config, orders: list[dict], now: datetime) -> dict:
    """Per real-money strategy: fills, fill rate, c/ct, last-20 c/ct, unfilled-would-have
    c/ct (results looked up and cached), spread trend, kill rule. Read-only."""
    cache = db.setdefault("unfilled_results", {})
    lookups = 0
    by: dict[str, list[dict]] = {}
    for r in orders:
        if r.get("mode") == "live" and r.get("strategy"):
            by.setdefault(r["strategy"], []).append(r)
    kills = cfg.raw.get("radar", {}).get("kill_rules") or {}
    trading = cfg.raw.get("trading") or {}
    out = {}
    for strat, rows in sorted(by.items()):
        t = trading.get(strat) if isinstance(trading.get(strat), dict) else {}
        if not (t.get("enabled") and t.get("live")):
            continue                       # only strategies on real money right now
        fills = sorted((r for r in rows if r.get("status") == "settled" and _f(r.get("filled")) > 0),
                       key=lambda r: r.get("settled_at") or "")
        cpc = [100 * _f(r.get("pnl")) / _f(r.get("filled"), 1) for r in fills]
        unf = [r for r in rows if r.get("status") in ("cancelled", "done-unfilled", "unfilled")
               and _f(r.get("filled")) == 0]
        missed = []
        for r in unf:
            tk, close = r.get("ticker") or "", parse_iso(r.get("close"))
            if tk not in cache and http is not None and close and now > close + timedelta(hours=1) \
                    and lookups < MAX_RESULT_CHECKS:
                lookups += 1
                try:
                    cache[tk] = _result(http, tk) or ""
                except HttpError:
                    pass
            if cache.get(tk):
                missed.append(cents(r.get("side") or "", _f(r.get("price")), cache[tk]))
        finished = len(fills) + len(unf) + sum(r.get("status") == "filled" for r in rows)
        rule = {**DEFAULT_KILL, **(kills.get(strat) or {})}
        tot = sum(cpc) / len(cpc) if cpc else None
        last = cpc[-20:]
        s = {"fills": len(fills), "contracts": int(sum(_f(r.get("filled")) for r in fills)),
             "fill_rate": (finished - len(unf)) / finished if finished else None,
             "cpc": tot, "cpc_last20": sum(last) / len(last) if last else None,
             "missed": len(missed), "missed_cpc": sum(missed) / len(missed) if missed else None,
             "pnl": round(sum(_f(r.get("pnl")) for r in fills), 2), "rule": rule, "flags": [],
             "series": sorted({series_of(r.get("ticker") or "") for r in rows
                               if (r.get("placed_at") or "") >= (now - timedelta(days=7)).isoformat()})[:5]}
        if len(fills) >= int(rule["min_fills"]) and tot is not None and tot < float(rule["max_cpc"]):
            s["flags"].append(f"KILL RULE: {tot:+.1f}c/ct after {len(fills)} fills "
                              f"(rule: below {float(rule['max_cpc']):+.0f}c after {int(rule['min_fills'])})")
        if "max_loss_usd" in rule and s["pnl"] <= -float(rule["max_loss_usd"]):
            s["flags"].append(f"KILL RULE: dollar cap hit ({s['pnl']:+.2f} vs -${float(rule['max_loss_usd']):.2f})")
        if len(cpc) >= 40 and s["cpc_last20"] is not None and s["cpc_last20"] < 0 \
                and sum(cpc[:-20]) / len(cpc[:-20]) > 2:
            s["flags"].append(f"FADING: last 20 fills {s['cpc_last20']:+.1f}c vs {sum(cpc[:-20]) / len(cpc[:-20]):+.1f}c before")
        if len(missed) >= 10 and s["missed_cpc"] is not None and tot is not None \
                and s["missed_cpc"] - tot > 10:
            s["flags"].append(f"ADVERSE SELECTION: orders we missed would have made {s['missed_cpc']:+.1f}c, "
                              f"fills made {tot:+.1f}c")
        out[strat] = s
    if len(cache) > 3000:
        db["unfilled_results"] = dict(list(cache.items())[-3000:])
    return out


def spread_trend(snaps: list[dict]) -> tuple[float | None, float | None]:
    """Median spread in the first 3 days of snapshots vs the last 24 hours."""
    sp = [(s["t"], s["spread"]) for s in snaps if s.get("spread") is not None]
    if len(sp) < 6:
        return None, None
    t0 = parse_iso(sp[0][0])
    t1 = parse_iso(sp[-1][0])
    first = sorted(v for t, v in sp if parse_iso(t) <= t0 + timedelta(days=3))
    last = sorted(v for t, v in sp if parse_iso(t) >= t1 - timedelta(hours=24))
    return first[len(first) // 2], last[len(last) // 2]


def live_series(orders: list[dict], now: datetime) -> list[str]:
    cut = (now - timedelta(days=7)).isoformat()
    seen = {}
    for r in orders:
        if r.get("mode") == "live" and (r.get("placed_at") or "") >= cut:
            seen[series_of(r.get("ticker") or "")] = 1
    return [s for s in seen if s][:15]


# ------------------------------------------------------------------ pushes

def _push(http: Http, cfg: Config, db: dict, key: str, title: str, body: str, now: datetime,
          click: str | None = None, dry_run: bool = False) -> bool:
    sent = db.setdefault("pushed", {})
    if key in sent:
        return False
    day = now.astimezone(ET).date().isoformat()
    per = db.setdefault("pushes", {})
    if per.get(day, 0) >= MAX_PUSHES_PER_DAY:
        return False
    if str(cfg.get("radar", "push", "on")) != "on":
        sent[key] = now.isoformat(timespec="seconds") + " (shadow)"
        return False
    if notify.send(http, cfg, title, body, priority=4, tags=["satellite"],
                   click=click or "https://kalshi.com/", dry_run=dry_run):
        sent[key] = now.isoformat(timespec="seconds")
        per[day] = per.get(day, 0) + 1
        db["pushes"] = {k: per[k] for k in sorted(per)[-7:]}
        return True
    return False


def promising(db: dict) -> list[tuple[str, str, dict]]:
    by: dict[tuple[str, str], list[dict]] = {}
    for o in db.get("paper") or []:
        by.setdefault((o["series"], o["rule"]), []).append(o)
    out = []
    for (s, rule), rows in sorted(by.items()):
        st = rule_stats(rows)
        if st["fills"] >= PROMISE_FILLS and st["cpc"] is not None and st["cpc"] >= PROMISE_CPC:
            out.append((s, rule, st))
    return out


RULE_TEXT = {"longshot_no": "NO bids on YES 3-15c long shots",
             "fav_24h": "bids on the 85-96c favorite in the last 24 h",
             "frozen_bracket": "selling top range YES at 35c+ while the futures are closed"}


# ------------------------------------------------------------------ one pass

def run(http: Http, cfg: Config, now: datetime, orders: list[dict] | None = None,
        dry_run: bool = False) -> dict:
    db = load()
    try:
        events = fetch_open_events(http)
    except HttpError as exc:
        log.warning("radar: events list failed: %s", exc)
        events = None
    new = diff_listings(db, events, now) if events else []
    watch = set(cfg.get("radar", "watch", DEFAULT_WATCH) or [])
    for it in new:
        if it["series"] in watch:
            it["watched"] = True
    have = {(o["rule"], o["ticker"]) for o in db.get("paper") or []}
    snaps = db.setdefault("snaps", {})
    placed = 0
    series_list = active_series(db, cfg, now)
    extra = [s for s in live_series(orders or [], now) if s not in series_list]
    for s in series_list + extra:
        try:
            ms = open_markets(http, s)
        except HttpError as exc:
            log.info("radar markets %s: %s", s, exc)
            continue
        snaps.setdefault(s, []).append(snapshot(ms, now))
        snaps[s] = snaps[s][-KEEP_SNAPS:]
        if s in series_list:
            it = (db.get("listings") or {}).get(s)
            if it is not None and ms and not it.get("first_markets"):
                it["first_markets"] = now.isoformat(timespec="seconds")
                it["n_markets"] = len(ms)
                it["example"] = ms[0].get("ticker")
            placed += place(db, candidates(s, ms, now, cfg, have), now)
    keep = set(series_list) | set(extra)
    db["snaps"] = {k: v for k, v in snaps.items() if k in keep or (v and parse_iso(v[-1]["t"])
                   and now - parse_iso(v[-1]["t"]) < timedelta(days=21))}
    update_paper(http, db, now)
    fade = live_fade(http, db, cfg, orders or [], now)
    db["fade"] = fade
    db["last_run"] = now.isoformat(timespec="seconds")
    db["last_new"] = [i["series"] for i in new]
    db.setdefault("log", []).append({"t": db["last_run"], "new": len(new), "placed": placed,
                                     "events": len(events) if events else None})
    db["log"] = db["log"][-24 * 14:]

    # pushes
    for it in new:
        if it.get("watched") or (it.get("category") not in SPORTS
                                 and it["category"] in set(cfg.get("radar", "push_categories",
                                                                   ["Commodities"]) or [])):
            _push(http, cfg, db, f"listed:{it['series']}",
                  f"New on Kalshi: {it.get('title') or it['series']}",
                  f"{it['series']} ({it['category']}) has its first open event "
                  f"({it.get('example_event')}). The radar is paper-testing longshot NO, favorites "
                  "24h and frozen-hours brackets on it now. Nothing to do yet; you'll get one more push "
                  "only if a rule clears the real-test bar."
                  + (f" Note: it sits on Kalshi exchange index {it['exchange_index']} (not the main "
                     "balance), so real trading there would need money moved at "
                     "kalshi.com/account/exchange-indexes." if it.get("exchange_index") else ""),
                  now, kalshi.market_url(it.get("example_event") or it["series"]), dry_run)
    for s, rule, st in promising(db):
        _push(http, cfg, db, f"promise:{s}:{rule}", f"Radar: {s} looks worth a real test",
              f"Paper {RULE_TEXT.get(rule, rule)} on {s}: {st['cpc']:+.1f}c/ct "
              f"(±{st['se'] or 0:.1f}) over {st['fills']} fills, fill rate "
              f"{(st['fill_rate'] or 0) * 100:.0f}%. Proposal: 1 contract real, hard $3 cap incl. open "
              "bets, kill rule back to paper after 20 fills if below -5c. Reply in the project to "
              "approve; nothing goes real without your OK.", now, None, dry_run)
    for strat, f in fade.items():
        for flag in f["flags"]:
            _push(http, cfg, db, f"fade:{strat}:{flag.split(':')[0]}",
                  f"Radar: {strat} {flag.split(':')[0].lower()}",
                  f"{strat}: {flag}. Fills {f['fills']}, {f['cpc'] if f['cpc'] is not None else 0:+.1f}c/ct "
                  "all time. Claude will review it in the project; the bot's own stops still apply.",
                  now, None, dry_run)
    if not dry_run:
        save(db)
    return db


# ------------------------------------------------------------------ report

def _c(x, d="-"):
    return d if x is None else f"{x:+.1f}c"


def digest_lines(db: dict | None = None, now: datetime | None = None) -> list[str]:
    db = load() if db is None else db
    if not db or not db.get("baseline_at"):
        return []
    now = now or parse_iso(db.get("last_run")) or datetime.now(ET)
    day_ago = (now - timedelta(hours=24)).isoformat()
    listings = db.get("listings") or {}
    new24 = [i for i in listings.values() if i["first_seen"] >= day_ago]
    lines = ["## New listing radar", "",
             f"Last run {(parse_iso(db.get('last_run')) or now).astimezone(ET):%b %d %I:%M %p} ET · "
             f"{db.get('open_events', 0):,} open events in {db.get('open_series', 0):,} series · "
             f"{len(new24)} new listings in the last 24 h ({sum(i.get('category') in SPORTS for i in new24)} sports, "
             "not tested). Paper only, no money.", ""]
    for i in sorted(new24, key=lambda x: x["first_seen"])[-12:]:
        tag = " **WATCHED**" if i.get("watched") else ""
        lines.append(f"- {i['series']}{tag} ({i['category']}): {i.get('title') or ''} · "
                     f"{i.get('events')} events")
    # paper rules by series
    by: dict[tuple[str, str], list[dict]] = {}
    for o in db.get("paper") or []:
        by.setdefault((o["series"], o["rule"]), []).append(o)
    if by:
        lines += ["", "| Series | Rule | Orders | Open | Fills | Fill rate | c/ct | Missed would've | Spread then → now |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for (s, rule), rows in sorted(by.items(), key=lambda kv: -len(kv[1]))[:20]:
            st = rule_stats(rows)
            a, b = spread_trend((db.get("snaps") or {}).get(s, []))
            fr = "-" if st["fill_rate"] is None else f"{st['fill_rate'] * 100:.0f}%"
            sp = "-" if a is None else f"{a * 100:.0f}c → {b * 100:.0f}c"
            se = "" if st["se"] is None else f" ±{st['se']:.1f}"
            lines.append(f"| {s} | {rule} | {st['orders']} | {st['open']} | {st['fills']} | {fr} | "
                         f"{_c(st['cpc'])}{se} | "
                         f"{_c(st['missed_cpc'])} ({st['missed']}) | {sp} |")
    fade = db.get("fade") or {}
    if fade:
        lines += ["", "**Edge-fade monitor (real money):**", "",
                  "| Strategy | Fills | Fill rate | c/ct all | last 20 | Missed would've | Spread then → now | Kill rule | Flags |",
                  "|---|---|---|---|---|---|---|---|---|"]
        snaps = db.get("snaps") or {}
        for strat, f in fade.items():
            fr = "-" if f["fill_rate"] is None else f"{f['fill_rate'] * 100:.0f}%"
            r = f["rule"]
            kr = f"paper if < {float(r['max_cpc']):+.0f}c after {int(r['min_fills'])}" + \
                (f" or -${float(r['max_loss_usd']):.0f}" if "max_loss_usd" in r else "")
            sp = "-"
            for ser in f.get("series") or []:
                a, b = spread_trend(snaps.get(ser, []))
                if a is not None:
                    sp = f"{ser} {a * 100:.0f}c → {b * 100:.0f}c"
                    break
            lines.append(f"| {strat} | {f['fills']} | {fr} | {_c(f['cpc'])} | {_c(f['cpc_last20'])} | "
                         f"{_c(f['missed_cpc'])} ({f['missed']}) | {sp} | {kr} | {'; '.join(f['flags']) or 'ok'} |")
    lines.append("")
    return lines
