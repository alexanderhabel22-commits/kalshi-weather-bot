"""Milestone performance reviews (Alex, Nov 9 2026).

Every live bot gets an automatic review when its real-money contract count first
passes 100 (an early check-in), 300, 500, 750, 1,000, 2,000 and 5,000, then every 2,500 after that. A "bot"
is one 15-minute market (BTC, silver, ...) or one main-bot strategy (Mentions,
favorites, ...). Each review is written to reports/reviews/ and pushed to ntfy.

Every recommendation comes from fixed rules below, worked out from the record itself,
so they can't lean toward whatever looks exciting this week:
  * keep / scale up / cut / back to paper, from the 90% range of profit per contract
  * contracts per order: quarter-Kelly on the LOW end of that range (a cautious edge),
    split across the markets that trade at the same time
  * bankroll: the profit pace now and at that size, and the money that size needs

Read-only apart from its own state: it never changes sizes, config or orders.
"""
from __future__ import annotations

import os
import random
from datetime import datetime
from pathlib import Path

from .util import DATA_DIR, Config, read_json, write_json

REVIEW_DIR = Path(DATA_DIR).parent / "reports" / "reviews"
FIRST = [100, 300, 500, 750, 1000, 2000, 5000]   # 100 = early check-in (Alex, Nov 9)
EVERY = 2500     # after 5,000 (Alex, Nov 9: 5,000 steps were ~7 weeks apart for BTC)
NAMES = {"KXBTC15M": "BTC", "KXSILVER15M": "Silver", "KXCOPPER15M": "Copper",
         "KXPALLADIUM15M": "Palladium", "KXPLATINUM15M": "Platinum", "KXWTI15M": "WTI oil",
         "KXBRENT15M": "Brent oil", "KXGOLD15M": "Gold", "KXNATGAS15M": "Natural gas",
         "mentions": "Mentions", "favorites": "Favorites", "macro_fade": "Fed/CPI",
         "weather": "Weather", "locks": "LOCKs"}
KELLY = 0.25          # quarter Kelly
MAX_MULT = 3          # never recommend more than 3x the current size in one step
CUT_BELOW = -0.05     # under -5c per contract -> back to paper


def milestone(n: float) -> int:
    """Highest milestone at or below n contracts (0 = none yet)."""
    best = 0
    for m in FIRST:
        if n >= m:
            best = m
    if n >= FIRST[-1] + EVERY:
        best = int(n // EVERY) * EVERY
    return best


def _f(x, d=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def per_contract_range(units: list[dict], reps: int = 4000, seed: int = 9) -> tuple[float, float, float] | None:
    """90% bootstrap range of profit per contract (resampling whole results) and the
    share of resamples above zero."""
    if len(units) < 10:
        return None
    rng, k, vals = random.Random(seed), len(units), []
    for _ in range(reps):
        c = p = 0.0
        for _ in range(k):
            u = units[rng.randrange(k)]
            c += u["n"]
            p += u["pnl"]
        vals.append(p / c if c else 0.0)
    vals.sort()
    return vals[int(0.05 * reps)], vals[int(0.95 * reps)], sum(v > 0 for v in vals) / reps


def kelly_contracts(edge: float, price: float, money: float) -> float:
    """Quarter-Kelly contracts for a binary bought at `price` that wins `edge` more
    often than the price says, staking from `money`."""
    if edge <= 0 or not 0 < price < 1:
        return 0.0
    f = KELLY * edge / (1 - price)          # share of money to stake
    return f * money / price


# ---------------------------------------------------------------- the records

def m15_units(rows: list[dict], live: list[str]) -> dict[str, list[dict]]:
    """One result per filled real-money 15-minute market, per series."""
    out: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("mode") != "live" or r.get("pnl") in (None, "") or r.get("series") not in live:
            continue
        n = _f(r.get("filled"))
        if n <= 0:
            continue
        out.setdefault(r["series"], []).append(
            {"n": n, "cost": _f(r.get("cost")) + _f(r.get("fee")), "pnl": _f(r["pnl"]),
             "at": r.get("close") or ""})
    return out


def main_units(rows: list[dict], live: list[str]) -> dict[str, list[dict]]:
    """One result per settled real-money event, per main-bot strategy."""
    from . import checkpoint
    out: dict[str, list[dict]] = {}
    for s, evs in checkpoint.events(checkpoint.live_rows(rows, None)).items():
        if s in live:
            out[s] = [{"n": e["n"], "cost": e["cost"], "pnl": e["pnl"], "at": ""} for e in evs]
    return out


# ---------------------------------------------------------------- one review

def review(key: str, units: list[dict], mark: int, ctx: dict) -> dict:
    """Numbers and recommendation for one bot at one milestone."""
    n = sum(u["n"] for u in units)
    cost = sum(u["cost"] for u in units)
    pnl = sum(u["pnl"] for u in units)
    per_c = pnl / n if n else 0.0
    price = cost / n if n else 0.85
    ci = per_contract_range(units)
    # the latest stretch vs everything before it: is the edge fading?
    half = max(1, len(units) // 3)
    recent = units[-half:]
    rn = sum(u["n"] for u in recent)
    recent_c = sum(u["pnl"] for u in recent) / rn if rn else 0.0
    cur = float(ctx.get("contracts", 1))
    days = max(0.25, float(ctx.get("days", 1)))
    per_day = n / days                               # contracts per day now

    if ci and ci[1] < 0:
        action, why = "BACK TO PAPER", "the whole 90% range is below zero: it is losing"
    elif per_c < CUT_BELOW:
        action, why = "BACK TO PAPER", f"{per_c * 100:+.1f}c per contract is under the -5c floor"
    elif ci and ci[0] > 0:
        action, why = "SCALE UP", "the whole 90% range is above zero: the edge is real"
    elif per_c > 0:
        action, why = "KEEP", "making money, but the 90% range still includes zero"
    else:
        action, why = "KEEP (watch)", "slightly negative, not bad enough to cut yet"
    fading = ci is not None and recent_c < 0 < per_c
    if fading and action == "SCALE UP":
        action, why = "KEEP", why + ", but the latest third of trades lost money, so no raise yet"

    # contracts per order: quarter Kelly on the cautious (low) end of the range
    money = float(ctx["money"]) / max(1, int(ctx.get("concurrent", 1)))
    edge = max(0.0, ci[0]) if ci else 0.0
    k = kelly_contracts(edge, price, money)
    if action.startswith("BACK"):
        rec = 0
    elif action == "SCALE UP":
        rec = max(int(cur), min(int(cur * MAX_MULT), int(k)))
    else:
        rec = int(cur)
    # money that recommendation needs (for the 15-min bot: every market doubling up at once)
    need_money = rec * price * ctx.get("concurrent", 1) * ctx.get("per_market_max", 1) / max(1e-9, ctx.get("share", 1))
    month_now = per_c * per_day * 30
    month_rec = per_c * per_day * 30 * (rec / cur if cur else 0)
    # bankroll that would make quarter Kelly say 5 and 10 contracts at today's cautious edge
    grow = {}
    if edge > 0:
        for c in (5, 10):
            grow[c] = c * price / (KELLY * edge / (1 - price)) * ctx.get("concurrent", 1) / max(1e-9, ctx.get("share", 1))
    return {"key": key, "name": NAMES.get(key, key), "mark": mark, "n": n, "results": len(units),
            "pnl": pnl, "per_c": per_c, "price": price, "ci": ci, "recent_c": recent_c,
            "fading": fading, "per_day": per_day, "action": action, "why": why,
            "cur": cur, "rec": rec, "kelly": k, "need_money": need_money,
            "month_now": month_now, "month_rec": month_rec, "grow": grow, "bot": ctx["bot"]}


def _c(x) -> str:
    return f"{x * 100:+.1f}c"


def render(r: dict, now: datetime) -> str:
    ci = "not enough results" if not r["ci"] else (
        f"{_c(r['ci'][0])} to {_c(r['ci'][1])} per contract ({r['ci'][2]:.0%} of resamples above zero)")
    L = [f"# Performance review: {r['name']} ({r['bot']}) at {r['mark']:,} contracts", "",
         f"_{now:%b %d %Y %H:%M} UTC. Rules: wxbot/review.py (fixed in advance)._", "",
         f"## Recommendation: **{r['action']}**", "", f"- Why: {r['why']}.",
         f"- Contracts per order: **{r['rec']}** (now {r['cur']:g}). Quarter-Kelly on the cautious "
         f"end of the range says {r['kelly']:.1f}; a raise is capped at {MAX_MULT}x per review.",
         f"- Money that size needs: about ${r['need_money']:.0f} of bankroll.",
         f"- Profit pace: ${r['month_now']:+.2f}/month now, ${r['month_rec']:+.2f}/month at the recommended size "
         f"(at today's {_c(r['per_c'])} per contract)."]
    if r["grow"]:
        L.append("- Bankroll growth: " + ", ".join(
            f"{c} contracts per order needs ~${v:,.0f}" for c, v in r["grow"].items()) + ".")
    else:
        L.append("- Bankroll growth: don't add money for this one until its range is above zero.")
    L += ["", "## Record", "",
          f"- Real contracts: {r['n']:,.0f} over {r['results']:,} results, {r['per_day']:.0f}/day",
          f"- Profit: ${r['pnl']:+.2f}, {_c(r['per_c'])} per contract at an average {r['price'] * 100:.0f}c",
          f"- 90% range: {ci}",
          f"- Latest third of results: {_c(r['recent_c'])} per contract"
          + (" (FADING: losing lately while the total is positive)" if r["fading"] else ""), ""]
    return "\n".join(L)


def push_line(r: dict) -> str:
    return (f"{r['name']} @ {r['mark']:,}: {r['action']}. {_c(r['per_c'])}/ct, ${r['pnl']:+.2f}. "
            f"Contracts {r['cur']:g} -> {r['rec']}. Pace ${r['month_rec']:+.0f}/mo.")


# ---------------------------------------------------------------- the run

def run(cfg: Config, now: datetime, http=None, dry_run: bool = False) -> list[str]:
    """Review every bot that crossed a new milestone since the last run."""
    from . import notify, trader
    from .checkpoint import m15_rows
    from .m15 import settings as m15_settings
    state_path = trader.STATE
    state = read_json(state_path, {}) or {}
    done = state.get("reviews") or {}
    seed = not state.get("reviews_seeded_100")   # bots already past 100 on Nov 9 skip that check-in
    last = state.get("reviews_checked_at")
    try:                              # an hourly check is plenty (milestones are days apart)
        if last and (now - datetime.fromisoformat(last)).total_seconds() < 3300:
            return []
    except ValueError:
        pass
    bankroll = float(trader.settings(cfg)["bankroll"])

    s15 = m15_settings(cfg)
    live15 = list(s15.get("live_series") or [])
    todo: list[dict] = []
    for series, units in m15_units(m15_rows(), live15).items():
        units.sort(key=lambda u: u["at"])
        n = sum(u["n"] for u in units)
        mark = milestone(n)
        if seed and mark == 100:
            done.setdefault(series, 100)
        if mark and mark > int(done.get(series, 0)):
            first = units[0]["at"]
            try:
                days = (now - datetime.fromisoformat(first)).total_seconds() / 86400
            except ValueError:
                days = 1
            ctx = {"bot": "15-min bot", "contracts": s15.get("contracts", 1), "days": days,
                   "money": bankroll * float(s15.get("pool_pct", 0.25)), "concurrent": len(live15),
                   "per_market_max": 1 + (1 if s15.get("extra_contracts") else 0),
                   "share": float(s15.get("pool_pct", 0.25))}
            todo.append(review(series, units, mark, ctx))

    tcfg = cfg.raw.get("trading", {}) or {}
    live_main = [k for k, v in tcfg.items() if isinstance(v, dict) and v.get("live")]
    rows = trader.load_all()
    for strat, units in main_units(rows, live_main).items():
        n = sum(u["n"] for u in units)
        mark = milestone(n)
        if seed and mark == 100:
            done.setdefault(strat, 100)
        if mark and mark > int(done.get(strat, 0)):
            ctx = {"bot": "main bot", "contracts": (tcfg.get(strat) or {}).get("contracts", 1),
                   "days": max(1, (now - datetime.fromisoformat("2026-11-05T23:03:00+00:00")).days),
                   "money": bankroll * float((tcfg.get(strat) or {}).get("word_pct")
                                               or (tcfg.get(strat) or {}).get("max_event_pct", 0.15)),
                   "concurrent": 1, "per_market_max": 1, "share": 1.0}
            todo.append(review(strat, units, mark, ctx))

    written = []
    for r in todo:
        REVIEW_DIR.mkdir(parents=True, exist_ok=True)
        path = REVIEW_DIR / f"{now:%Y-%m-%d}-{r['key']}-{r['mark']}.md"
        path.write_text(render(r, now), encoding="utf-8")
        written.append(str(path))
        done[r["key"]] = r["mark"]
    if not dry_run:
        state = read_json(state_path, {}) or {}
        state["reviews"] = done
        state["reviews_seeded_100"] = True
        state["reviews_checked_at"] = now.isoformat(timespec="seconds")
        write_json(state_path, state)
    if todo:
        repo = os.environ.get("GITHUB_REPOSITORY")
        click = (f"https://github.com/{repo}/tree/main/reports/reviews" if repo else None)
        notify.send(http, cfg, f"Performance review ({len(todo)})",
                    "\n".join(push_line(r) for r in todo), priority=4, tags=["bar_chart"],
                    click=click, dry_run=dry_run)
    return written
