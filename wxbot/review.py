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

from zoneinfo import ZoneInfo

from .util import DATA_DIR, Config, read_json, write_json

ET = ZoneInfo("America/New_York")

REVIEW_DIR = Path(DATA_DIR).parent / "reports" / "reviews"
FIRST = [100, 300, 500, 750, 1000, 2000, 5000]   # 100 = early check-in (Alex, Nov 9)
EVERY = 2500     # after 5,000 (Alex, Nov 9: 5,000 steps were ~7 weeks apart for BTC)
NAMES = {"KXBTC15M": "BTC", "KXSILVER15M": "Silver", "KXCOPPER15M": "Copper",
         "KXPALLADIUM15M": "Palladium", "KXPLATINUM15M": "Platinum", "KXWTI15M": "WTI oil",
         "KXBRENT15M": "Brent oil", "KXGOLD15M": "Gold", "KXNATGAS15M": "Natural gas",
         "mentions": "Mentions", "favorites": "Favorites", "macro_fade": "Fed/CPI",
         "macro_wide": "Gas/claims fade", "ai_fade": "Best-AI fade", "favorites_wide": "Election favorites", "favorites_late": "Favorites, last 24h (paper)",
         "weather": "Weather", "weather_late": "Late-day weather", "locks": "LOCKs",
         "rain_evening": "Evening rain fade", "spx_range": "S&P range fade", "ndx_range": "Nasdaq range fade"}
KELLY = 0.25          # quarter Kelly
MAX_MULT = 3          # never recommend more than 3x the current size in one step
CUT_BELOW = -0.05     # under -5c per contract -> back to paper
MONTHLY_DAYS = 30     # a bot with no review for 30 days gets one anyway (Alex, Nov 9)


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
        ttc = r.get("fill_ttc_s")
        out.setdefault(r["series"], []).append(
            {"n": n, "cost": _f(r.get("cost")) + _f(r.get("fee")), "pnl": _f(r["pnl"]),
             "at": r.get("close") or "",
             "late": None if ttc in (None, "") else _f(ttc) <= 300})
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
    return {"key": key, "name": NAMES.get(key, key), "mark": mark,
            "label": f"{mark:,} contracts" if mark else f"monthly review ({n:,.0f} contracts)", "n": n, "results": len(units),
            "pnl": pnl, "per_c": per_c, "price": price, "ci": ci, "recent_c": recent_c,
            "fading": fading, "per_day": per_day, "action": action, "why": why,
            "cur": cur, "rec": rec, "kelly": k, "need_money": need_money,
            "month_now": month_now, "month_rec": month_rec, "grow": grow, "bot": ctx["bot"],
            "exp": ctx.get("exp"), "grade": grade(ci), "x": extras(units),
            "freeze_at": ctx.get("freeze_at")}


def _c(x) -> str:
    return f"{x * 100:+.1f}c"


GRADES = [(0.97, "A+", "Money printer. The edge is about as clear as this data can show."),
          (0.90, "A", "Clearly working. Odds strongly favor a real edge."),
          (0.75, "B", "Looking good, but luck can't be ruled out yet."),
          (0.55, "C", "Coin-flip territory. Slightly ahead, nothing proven."),
          (0.30, "D", "Struggling. More likely losing than winning."),
          (0.0, "F", "Losing. The numbers say this isn't working.")]


def grade(ci) -> tuple[str, str]:
    share = ci[2] if ci else 0.5
    for cut, g, line in GRADES:
        if share >= cut:
            return g, line
    return GRADES[-1][1:]


def _bar(x: float, scale: float = 0.10, width: int = 10) -> str:
    """Text bar for a per-contract number: green right of 0, red left."""
    k = min(width, round(abs(x) / scale * width))
    return ("🟩" * k if x >= 0 else "🟥" * k) or "·"


def _group(units: list[dict], key) -> list[tuple[str, int, float, float]]:
    """(label, contracts, profit, per contract) per group, groups in first-seen order."""
    acc: dict = {}
    for u in units:
        g = key(u)
        if g is None:
            continue
        a = acc.setdefault(g, [0.0, 0.0])
        a[0] += u["n"]
        a[1] += u["pnl"]
    return [(g, a[0], a[1], a[1] / a[0] if a[0] else 0.0) for g, a in acc.items()]


def _et(u):
    try:
        return datetime.fromisoformat(u["at"]).astimezone(ET)
    except (ValueError, TypeError):
        return None


def extras(units: list[dict]) -> dict:
    """The colour: trend, breakdowns, streaks."""
    wins = sum(1 for u in units if u["pnl"] > 0)
    # trend in five equal stretches
    k = max(1, len(units) // 5)
    trend = []
    for i in range(0, len(units), k):
        ch = units[i:i + k]
        n = sum(u["n"] for u in ch)
        trend.append((f"#{i + 1}-{i + len(ch)}", n, sum(u["pnl"] for u in ch) / n if n else 0.0))
    trend = trend[:5] if len(trend) <= 5 else trend[:4] + [(
        f"#{4 * k + 1}-{len(units)}", sum(u["n"] for u in units[4 * k:]),
        sum(u["pnl"] for u in units[4 * k:]) / max(1e-9, sum(u["n"] for u in units[4 * k:])))]

    def band(u):
        p = u["cost"] / u["n"] if u["n"] else 0
        return ("71-74c" if p < 0.75 else "75-79c" if p < 0.80 else "80-87c" if p < 0.88 else "88-95c")

    def tod(u):
        d = _et(u)
        if not d:
            return None
        h = d.hour
        return ("Overnight (12-6 AM ET)" if h < 6 else "Morning (6 AM-12 PM)" if h < 12
                else "Afternoon (12-6 PM)" if h < 18 else "Evening (6 PM-12 AM)")

    bands = sorted(_group(units, band))
    size = _group(units, lambda u: "Double-ups (2 contracts)" if u["n"] >= 1.99 else "Singles")
    order = ["Overnight", "Morning", "Afternoon", "Evening"]
    times = sorted(_group(units, tod), key=lambda g: next(i for i, o in enumerate(order) if g[0].startswith(o)))
    timing = _group(units, lambda u: None if u.get("late") is None else
                    ("Filled in last 5 min" if u["late"] else "Filled with 5+ min left"))
    days = _group(units, lambda u: (_et(u) or datetime.min).strftime("%a %b %d") if _et(u) else None)
    # paper "skip macro release windows" rule (Alex OK'd a paper track, Nov 10): logged only
    from . import releases
    known = releases.load()
    rel = _group(units, lambda u: "Release windows (paper rule would skip)"
                 if releases.window_kind(u.get("at") or "", known) else "All other windows")
    # streaks and drawdown
    worst_streak = cur = 0
    peak = run = dd = 0.0
    for u in units:
        cur = cur + 1 if u["pnl"] < 0 else 0
        worst_streak = max(worst_streak, cur)
        run += u["pnl"]
        peak = max(peak, run)
        dd = max(dd, peak - run)
    best = max(days, key=lambda d: d[2]) if days else None
    worst = min(days, key=lambda d: d[2]) if days else None
    return {"wins": wins, "losses": len(units) - wins, "trend": trend, "bands": bands,
            "size": size, "times": times, "release": rel, "timing": timing, "best_day": best, "worst_day": worst,
            "worst_streak": worst_streak, "drawdown": dd,
            "big_win": max((u["pnl"] for u in units), default=0.0),
            "big_loss": min((u["pnl"] for u in units), default=0.0)}


def _table(title: str, rows) -> list[str]:
    rows = [r for r in rows if r[1] > 0]
    if len(rows) < 2:
        return []
    L = [f"**{title}**", "", "| | Contracts | Profit | Per contract | |", "|---|---|---|---|---|"]
    for g, n, p, c in rows:
        L.append(f"| {g} | {n:,.0f} | ${p:+.2f} | {_c(c)} | {_bar(c)} |")
    return L + [""]


def _release_lines(groups) -> list[str]:
    """Paper skip-release-windows rule: what the record would be without those windows."""
    rel = next((g for g in groups if g[0].startswith("Release")), None)
    if not rel or rel[1] <= 0:
        return []
    total = sum(g[2] for g in groups)
    rest = total - rel[2]
    return _table("Macro release windows (CPI, jobs, PCE, GDP, Fed; paper rule)", groups) + [
        f"Paper skip rule: ${rest:+.2f} without release windows vs ${total:+.2f} actual "
        f"({rel[1]:,.0f} contracts in release windows). Judged at the 500 review; no live change.", ""]


def render(r: dict, now: datetime) -> str:
    x, (g, gline) = r["x"], r["grade"]
    ci = "not enough results yet" if not r["ci"] else (
        f"{_c(r['ci'][0])} to {_c(r['ci'][1])} per contract")
    sure = f"{r['ci'][2]:.0%}" if r["ci"] else "?"
    be = r["price"]                                   # break-even win rate = average price paid
    wr = x["wins"] / max(1, r["results"])
    L = [f"# {r['name']} performance review: {r['label']}", "",
         f"_{r['bot']} · {now.astimezone(ET):%a %b %d, %I:%M %p} ET · rules fixed in advance (wxbot/review.py)_", "",
         f"## Grade: {g}", "", f"> {gline}", "",
         f"**Bottom line:** {r['action']}. {r['why'].capitalize()}.", "",
         "## Scorecard", "",
         "| | |", "|---|---|",
         f"| Contracts | {r['n']:,.0f} ({r['per_day']:.0f} a day) |",
         f"| Record | {x['wins']}-{x['losses']} ({wr:.0%} won; break-even is {be:.0%}) |",
         f"| Profit | **${r['pnl']:+.2f}** |",
         f"| Per contract | **{_c(r['per_c'])}** at an average {r['price'] * 100:.0f}c |"]
    if r.get("exp") is not None:
        L.append(f"| Backtest said | {_c(r['exp'])} per contract "
                 f"({'beating' if r['per_c'] >= r['exp'] else 'behind'} it) |")
    L += [f"| 90% range | {ci} |",
          f"| Chance the edge is real | {sure} |",
          f"| Biggest win / loss | ${x['big_win']:+.2f} / ${x['big_loss']:+.2f} |",
          f"| Worst losing streak | {x['worst_streak']} in a row |",
          f"| Deepest dip from a high | ${x['drawdown']:.2f} |"]
    if x["best_day"]:
        L.append(f"| Best / worst day | {x['best_day'][0]} ${x['best_day'][2]:+.2f} / "
                 f"{x['worst_day'][0]} ${x['worst_day'][2]:+.2f} |")
    L += ["", "## Is it holding up?", "", "Results in five equal stretches, oldest first:", "",
          "| Stretch | Contracts | Per contract | |", "|---|---|---|---|"]
    for lab, n, c in x["trend"]:
        L.append(f"| {lab} | {n:,.0f} | {_c(c)} | {_bar(c)} |")
    L += ["", ("⚠️ **Fading:** the latest third lost money while the total is up." if r["fading"]
               else f"Latest third: {_c(r['recent_c'])} per contract."), "", "## Where the money comes from", ""]
    L += _table("By entry price", x["bands"])
    L += _table("Singles vs double-ups", x["size"])
    L += _table("By bid timing", x["timing"])
    L += _table("By time of day", x["times"])
    L += _release_lines(x.get("release") or [])
    L += ["## Recommendation", "",
          f"- **Action: {r['action']}.**",
          f"- **Contracts per order: {r['rec']}** (now {r['cur']:g}). "
          + (f"Quarter-Kelly on the cautious end of the range says {r['kelly']:.1f}; raises are capped "
             f"at {MAX_MULT}x per review." if r["kelly"] > 0 else
             "No raise until the cautious end of the range is above zero."),
          f"- **Profit pace:** ${r['month_now']:+.2f}/month now, ${r['month_rec']:+.2f}/month at the "
          f"recommended size.",
          f"- **Money that size needs:** about ${r['need_money']:.0f} of bankroll."]
    if r["grow"]:
        L.append("- **Bankroll growth:** " + ", ".join(
            f"{c} contracts per order wants ~${v:,.0f}" for c, v in r["grow"].items())
            + ". Add money only if this grade holds at the next review.")
    else:
        L.append("- **Bankroll growth:** don't add money on this one's account until its range clears zero.")
    nxt = next((m for m in FIRST if m > r["n"]), (int(r["n"] // EVERY) + 1) * EVERY)
    L += ["", "## What would change my mind", "",
          "- Falls under -5c per contract, or the whole range drops below zero: back to paper.",
          "- Whole range climbs above zero (and the latest stretch isn't losing): scale up.",
          f"- Next review at {nxt:,} contracts, or in 30 days, whichever comes first."]
    if r.get("freeze_at"):
        L.append(f"- Rule freeze: {'lifted' if r['n'] >= r['freeze_at'] else 'on'} "
                 f"(rules stay fixed until {r['freeze_at']:,} contracts; Alex, Nov 9).")
    return "\n".join(L) + "\n"


def push_line(r: dict) -> str:
    return (f"[{r['grade'][0]}] " + (f"{r['name']} @ {r['mark']:,}: {r['action']}." if r["mark"] else f"{r['name']} (monthly): {r['action']}.")) + (f" {_c(r['per_c'])}/ct, ${r['pnl']:+.2f}. "
            f"Contracts {r['cur']:g} -> {r['rec']}. Pace ${r['month_rec']:+.0f}/mo.")


# ---------------------------------------------------------------- the run

def run(cfg: Config, now: datetime, http=None, dry_run: bool = False) -> list[str]:
    """Review every bot that crossed a new milestone since the last run."""
    from . import notify, trader
    from .checkpoint import EXPECTED, M15_EXPECTED, m15_rows
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
    try:                              # paper release-window rule: keep the calendar fresh (daily)
        from . import releases
        upd = (read_json(releases.PATH, {}) or {}).get("updated")
        if http is not None and (not upd or (now - datetime.fromisoformat(upd)).total_seconds() > 86400):
            releases.refresh(http, now)
    except Exception as exc:  # never let the calendar break reviews
        import logging
        logging.warning("release calendar refresh failed: %s", exc)
    bankroll = float(trader.settings(cfg)["bankroll"])
    at = state.get("reviews_at") or {}          # last review (or first seen) per bot

    def due(key: str, n: float, results: int) -> int | None:
        """Milestone to review now, 0 for the monthly backstop, None for nothing."""
        mark = milestone(n)
        if seed and mark == 100:
            done.setdefault(key, 100)
        if mark and mark > int(done.get(key, 0)):
            return mark
        if key not in at:
            at[key] = now.isoformat(timespec="seconds")   # the monthly clock starts now
            return None
        try:
            since = (now - datetime.fromisoformat(at[key])).total_seconds() / 86400
        except ValueError:
            since = 0
        return 0 if since >= MONTHLY_DAYS and results >= 10 else None

    s15 = m15_settings(cfg)
    live15 = list(s15.get("live_series") or [])
    todo: list[dict] = []
    for series, units in m15_units(m15_rows(), live15).items():
        units.sort(key=lambda u: u["at"])
        mark = due(series, sum(u["n"] for u in units), len(units))
        if mark is not None:
            first = units[0]["at"]
            try:
                days = (now - datetime.fromisoformat(first)).total_seconds() / 86400
            except ValueError:
                days = 1
            ctx = {"bot": "15-min bot", "contracts": s15.get("contracts", 1), "days": days,
                   "exp": M15_EXPECTED.get(series), "freeze_at": 500,
                   "money": bankroll * float(s15.get("pool_pct", 0.25)), "concurrent": len(live15),
                   "per_market_max": 1 + (1 if s15.get("extra_contracts") else 0),
                   "share": float(s15.get("pool_pct", 0.25))}
            todo.append(review(series, units, mark, ctx))

    tcfg = cfg.raw.get("trading", {}) or {}
    live_main = [k for k, v in tcfg.items() if isinstance(v, dict) and v.get("live")]
    rows = trader.load_all()
    for strat, units in main_units(rows, live_main).items():
        mark = due(strat, sum(u["n"] for u in units), len(units))
        if mark is not None:
            e = EXPECTED.get(strat) or {}
            ctx = {"bot": "main bot", "contracts": (tcfg.get(strat) or {}).get("contracts", 1),
                   "exp": e["per_dollar"] * e["price"] if e else None, "freeze_at": 500,
                   "days": max(1, (now - datetime.fromisoformat("2026-11-05T23:03:00+00:00")).days),
                   "money": bankroll * float((tcfg.get(strat) or {}).get("word_pct")
                                               or (tcfg.get(strat) or {}).get("max_event_pct", 0.15)),
                   "concurrent": 1, "per_market_max": 1, "share": 1.0}
            todo.append(review(strat, units, mark, ctx))

    written = []
    for r in todo:
        REVIEW_DIR.mkdir(parents=True, exist_ok=True)
        path = REVIEW_DIR / f"{now:%Y-%m-%d}-{r['key']}-{r['mark'] or 'monthly'}.md"
        path.write_text(render(r, now), encoding="utf-8")
        written.append(str(path))
        if r["mark"]:
            done[r["key"]] = r["mark"]
        at[r["key"]] = now.isoformat(timespec="seconds")
    if not dry_run:
        state = read_json(state_path, {}) or {}
        state["reviews"] = done
        state["reviews_seeded_100"] = True
        state["reviews_at"] = at
        state["reviews_checked_at"] = now.isoformat(timespec="seconds")
        write_json(state_path, state)
    if todo:
        repo = os.environ.get("GITHUB_REPOSITORY")
        click = (f"https://github.com/{repo}/tree/main/reports/reviews" if repo else None)
        notify.send(http, cfg, f"Performance review ({len(todo)})",
                    "\n".join(push_line(r) for r in todo), priority=4, tags=["bar_chart"],
                    click=click, dry_run=dry_run)
    return written
