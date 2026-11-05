"""New-market watcher (added Nov 5, 2026).

Idea being tested: brand-new Kalshi series may be mispriced in their first
weeks, before market makers arrive. Every 30 minutes this pulls Kalshi's full
series list (one public call), remembers every series ticker and category it
has seen, and notices new ones. A new recurring series is watched until its
first markets open; then it is logged with its prices, spreads and a
recommended test.

The first run only records a baseline (no alerts). After that, a launch is
"alert-worthy" when it is a brand-new category, a weather/climate series, or a
fast-repeating series (15-min, hourly, daily, weekly). `launch_alerts` in
config.toml decides what happens to those: "shadow" = log only (default),
"on" = also push one combined phone note (max a few a day, not in quiet
hours). Everything lives in data/launches.json and REPORT.md.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from . import kalshi, notify
from .util import DATA_DIR, Config, Http, HttpError, parse_iso, read_json, write_json

log = logging.getLogger("wxbot")

LAUNCHES = DATA_DIR / "launches.json"
FAST = {"fifteen_min", "hourly", "daily", "weekly"}
WEATHER_CATS = {"Climate and Weather", "Climate", "Weather"}
MAX_CHECKS_PER_RUN = 25     # market lookups for pending series per run
PENDING_DAYS = 21           # stop waiting for a series' first market after this
KEEP_FOUND = 400
MAX_PUSHES_PER_DAY = 4


def load() -> dict:
    return read_json(LAUNCHES, {}) or {}


def save(db: dict) -> None:
    write_json(LAUNCHES, db)


def is_alert_worthy(item: dict) -> bool:
    return bool(item.get("new_category") or item.get("category") in WEATHER_CATS
                or item.get("frequency") in FAST)


def recommend(item: dict) -> str:
    """Plain-words test to run on a fresh launch (refined by the launch backtest)."""
    freq = item.get("frequency") or ""
    spread = item.get("median_spread")
    wide = spread is not None and spread >= 0.05
    if item.get("category") in WEATHER_CATS:
        base = ("Weather: check what it settles on and whether our forecasts/obs cover it; "
                "if yes, paper-test it like the day-before maker orders.")
    elif freq in ("fifteen_min", "hourly"):
        base = ("Fast series: paper-quote resting limit orders a few cents inside the spread for "
                "a week before any money.")
    else:
        base = ("Paper-test resting NO orders on 10-30c longshots (the longshot fade) for 2-4 "
                "weeks, 1 contract each.")
    if wide:
        base += f" Spreads are wide ({spread * 100:.0f}c), so makers haven't arrived yet."
    elif spread is not None:
        base += f" Spreads already tight ({spread * 100:.0f}c): probably already efficient."
    return base


def _fetch_series(http: Http) -> list[dict]:
    js = http.get_json(f"{kalshi.BASE}/series")
    return [s for s in (js.get("series") or []) if s.get("ticker")]


def _first_markets(http: Http, ticker: str) -> list[dict]:
    js = http.get_json(f"{kalshi.BASE}/markets",
                       params={"series_ticker": ticker, "status": "open", "limit": 100})
    return js.get("markets") or []


def _market_stats(ms: list[dict]) -> dict:
    spreads, vol = [], 0.0
    for m in ms:
        bid, ask = kalshi.price(m, "yes_bid"), kalshi.price(m, "yes_ask")
        if bid is not None and ask is not None and 0 < ask <= 1 and ask >= bid:
            spreads.append(round(ask - bid, 4))
        try:
            vol += float(m.get("volume_fp", m.get("volume")) or 0)
        except (TypeError, ValueError):
            pass
    spreads.sort()
    return {"n_open": len(ms), "volume": round(vol, 2),
            "median_spread": spreads[len(spreads) // 2] if spreads else None,
            "example": (ms[0].get("ticker") if ms else None)}


def watch(http: Http, cfg: Config, db: dict, now: datetime, *, quiet: bool = False,
          dry_run: bool = False) -> list[dict]:
    """One watcher pass. Returns the launches that became ready this pass."""
    series = _fetch_series(http)
    known = set(db.get("known") or [])
    if len(series) < 100 or (known and len(series) < 0.5 * len(known)):
        log.info("launch watch: series list looks incomplete (%d), skipping", len(series))
        return []
    stamp = now.isoformat(timespec="seconds")
    cats = set(db.get("categories") or [])
    if not known:                                   # first run: baseline only
        db["known"] = sorted(s["ticker"] for s in series)
        db["categories"] = sorted({s.get("category") or "?" for s in series})
        db["baseline_at"] = stamp
        db["last_check"] = stamp
        log.info("launch watch: baseline of %d series", len(series))
        return []

    pending = db.setdefault("pending", {})
    found = db.setdefault("found", [])
    for s in series:
        t = s["ticker"]
        if t in known:
            continue
        known.add(t)
        cat = s.get("category") or "?"
        item = {"series": t, "title": s.get("title"), "category": cat,
                "frequency": s.get("frequency"), "first_seen": stamp,
                "fee_multiplier": s.get("fee_multiplier"),
                "new_category": cat not in cats}
        if cat not in cats:
            cats.add(cat)
            db.setdefault("new_categories", []).append({"category": cat, "first_seen": stamp,
                                                        "series": t})
        pending[t] = item

    ready = []
    checks = 0
    order = sorted(pending, key=lambda k: (not is_alert_worthy(pending[k]),
                                           pending[k].get("checked", ""), pending[k]["first_seen"]))
    for t in order:
        item = pending[t]
        if now - (parse_iso(item["first_seen"]) or now) > timedelta(days=PENDING_DAYS):
            item["status"] = "no markets"
            found.append(pending.pop(t))
            continue
        if checks >= MAX_CHECKS_PER_RUN:
            break
        checks += 1
        item["checked"] = stamp
        try:
            ms = _first_markets(http, t)
        except HttpError as exc:
            log.info("launch watch %s: %s", t, exc)
            continue
        if not ms:
            continue
        item.update(_market_stats(ms))
        item["markets_seen"] = stamp
        item["alert_worthy"] = is_alert_worthy(item)
        item["recommendation"] = recommend(item)
        item["status"] = "live"
        found.append(pending.pop(t))
        ready.append(item)

    db["known"] = sorted(known)
    db["categories"] = sorted(cats)
    db["found"] = found[-KEEP_FOUND:]
    db["last_check"] = stamp
    _maybe_push(http, cfg, db, ready, now, quiet=quiet, dry_run=dry_run)
    return ready


def _maybe_push(http: Http, cfg: Config, db: dict, ready: list[dict], now: datetime, *,
                quiet: bool, dry_run: bool) -> None:
    mode = str(cfg.get("alerts", "launch_alerts", "shadow"))
    queue = db.setdefault("queue", [])
    queue += [i["series"] for i in ready if i.get("alert_worthy")]
    if not queue:
        return
    if mode != "on":
        db["queue"] = []          # shadow: the report shows what would have been sent
        return
    day = now.date().isoformat()
    pushes = db.setdefault("pushes", {})
    if quiet or pushes.get(day, 0) >= MAX_PUSHES_PER_DAY:
        db["queue"] = queue[-20:]
        return
    by_t = {i["series"]: i for i in db.get("found", [])}
    items = [by_t[t] for t in dict.fromkeys(queue) if t in by_t]
    if not items:
        db["queue"] = []
        return
    lines = []
    for i in items[:5]:
        tag = " (NEW CATEGORY)" if i.get("new_category") else ""
        lines.append(f"- {i.get('title') or i['series']} [{i['category']}, {i.get('frequency')}]{tag}: "
                     f"{i.get('n_open', 0)} markets open. {i.get('recommendation', '')}")
    if len(items) > 5:
        lines.append(f"+{len(items) - 5} more in the report.")
    title = f"New on Kalshi: {items[0].get('title') or items[0]['series']}" if len(items) == 1 \
        else f"New on Kalshi: {len(items)} series"
    click = f"https://kalshi.com/markets/{items[0]['series'].lower()}"
    if notify.send(http, cfg, title, "\n".join(lines) + "\nInfo only, nothing to trade yet.",
                   priority=2, tags=["sparkles"], click=click, dry_run=dry_run):
        pushes[day] = pushes.get(day, 0) + 1
        db["pushes"] = {k: pushes[k] for k in sorted(pushes)[-7:]}
        db["queue"] = []


def report_section(db: dict) -> list[str]:
    if not db or not db.get("baseline_at"):
        return []
    found = db.get("found") or []
    live = [i for i in found if i.get("status") == "live"]
    lines = ["## New Kalshi markets (launch watcher)", "",
             f"Watching {len(db.get('known') or [])} series since {db['baseline_at'][:10]}; "
             f"{len(live)} new series have opened markets, {len(db.get('pending') or {})} waiting "
             "for their first market.", ""]
    for c in (db.get("new_categories") or [])[-5:]:
        lines.append(f"- New category **{c['category']}** first seen {c['first_seen'][:16]} ({c['series']})")
    worthy = [i for i in live if i.get("alert_worthy")][-15:]
    if worthy:
        lines += ["", "| Opened | Series | Category | Freq | Markets | Spread | Test |",
                  "|---|---|---|---|---|---|---|"]
        for i in reversed(worthy):
            sp = i.get("median_spread")
            lines.append(f"| {i['markets_seen'][:16]} | {i.get('title') or i['series']} ({i['series']}) | "
                         f"{i['category']} | {i.get('frequency')} | {i.get('n_open')} | "
                         f"{'' if sp is None else f'{sp * 100:.0f}c'} | {i.get('recommendation', '')} |")
    lines.append("")
    return lines
