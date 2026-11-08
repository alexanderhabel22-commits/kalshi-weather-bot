"""Strategy allocator: grade each strategy on its settled results and move
bankroll toward what works, without ever switching a strategy off.

Alex (Nov 7): "after like 100 completed trades... grades itself and allocates
funds as such... money allocated to what works, without eliminating the
others", re-graded about once a week.

How a strategy is graded
  * Profit per dollar risked (ROI), not hit rate. Ten 8c weather bets cost the
    same as one 80c favorite; what matters is what each dollar earns.
  * Trades on the same event are added together first (one speech, one
    city-day, one election), so ten correlated bets count as one result.
  * Noise is measured from the prices paid: a 9c longshot that pays 11x is far
    noisier per dollar than a 90c favorite, so the same number of trades says
    less. The grade is shrunk toward a common hurdle (`hurdle`, default +3c per
    dollar) in proportion to that noise (normal-normal Bayes):
        grade = (hurdle/prior_sd^2 + roi/se^2) / (1/prior_sd^2 + 1/se^2)
    With prior_sd = 3.5%, about 100 settled favorite-style events move a grade
    halfway to its own record; a pure-longshot strategy needs many more.
  * Real money only (Alex, Nov 7: paper is a sandbox for new ideas, not graded).
    `paper_weight` > 0 would let paper results count at that weight.

How the grade becomes money
  * target multiplier = grade / hurdle, kept between `floor` (0.25: never
    switched off, keeps building a record) and `ceiling` (2.0).
  * Once a week (`regrade_days`) the multiplier moves toward the target by at
    most `max_step` (0.25), so a lucky or unlucky week can't swing sizing.
  * When applied (`apply = true`, needs Alex's OK), the multiplier scales the
    strategy's own open-$ cap and per-order size, and the best-graded
    strategies get first claim on the overall cap. The overall 75% cap, the
    20% per-event cap and the loss stops never change.

15-minute bot (own server, Nov 8): graded per coin (m15_btc, m15_silver, ...)
from the real fills it uploads to the m15-data branch, one market = one result.
Its multiplier is report-only even when applied: the server sizes itself, and
its cut/raise rules (100 / 300 real fills, analysis/checkpoint/CHECKPOINT.md)
stay in charge of it.

Shadow mode (`apply = false`): grades and multipliers are worked out and
reported (the "allocator" entry of data/trader_state.json, REPORT.md) but
sizing is unchanged.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta

import csv
import io

from .util import Config, HttpError, parse_iso

# The 15-minute bot runs on its own server and uploads one CSV per day to the
# m15-data branch. It is graded per coin, one market = one result, real fills only.
M15_RAW = "https://raw.githubusercontent.com/{repo}/m15-data/m15/markets-{day}.csv"
M15_LIVE_FROM = "2026-11-08"        # first real-money market (server live Nov 8, ~03:30 UTC)

DEFAULTS = {"enabled": True, "apply": False,
            "hurdle": 0.03,          # the profit per dollar a strategy is assumed to make until shown otherwise
            "prior_sd": 0.035,       # how sure we are of that (smaller = needs more trades to move)
            "floor": 0.25, "ceiling": 2.0,
            "max_step": 0.25,        # largest change per re-grade
            "regrade_days": 7,       # Alex: "every week or something"
            "paper_weight": 0.0}     # real money only (Alex, Nov 7); >0 lets paper results count too


def params(cfg: Config) -> dict:
    return {**DEFAULTS, **(cfg.raw.get("allocator", {}) or {})}


def _f(x, d=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def m15_name(series: str) -> str:
    """KXBTC15M -> m15_btc"""
    s = (series or "").upper()
    s = s[2:] if s.startswith("KX") else s
    s = s[:-3] if s.endswith("15M") else s
    return "m15_" + s.lower()


def m15_names(cfg: Config) -> list[str]:
    m = cfg.raw.get("m15", {}) or {}
    if str(m.get("mode", "off")).lower() != "live":
        return []
    return [m15_name(s) for s in m.get("live_series", [])]


def fetch_m15(http, cfg: Config, now: datetime) -> list[dict]:
    """Every graded market the 15-minute bot has written since it went live."""
    import os
    repo = os.environ.get("GITHUB_REPOSITORY") or "alexanderhabel22-commits/kalshi-weather-bot"
    start = datetime.fromisoformat(str(params(cfg).get("m15_live_from", M15_LIVE_FROM))).date()
    rows: list[dict] = []
    day = start - timedelta(days=1)            # files are named by the close day in ET
    while day <= now.date():
        try:
            text = http.get_text(M15_RAW.format(repo=repo, day=day.isoformat()))
            rows += list(csv.DictReader(io.StringIO(text)))
        except HttpError:
            pass                               # no file that day (or not uploaded yet)
        day += timedelta(days=1)
    return rows


def results(trade_rows: list[dict], weather_paper: list[dict] = (),
            m15_rows: list[dict] = ()) -> dict[str, list[dict]]:
    """Settled results per strategy, one entry per (mode, event):
    cost, pnl, var (variance of pnl if prices were fair) and weight."""
    acc: dict = defaultdict(lambda: {"cost": 0.0, "pnl": 0.0, "sd": 0.0})
    start = M15_LIVE_FROM
    for r in m15_rows:                            # 15-minute bot: real fills only, graded markets
        n = int(_f(r.get("filled")))
        if r.get("mode") != "live" or n < 1 or r.get("won") in (None, "") or (r.get("close") or "") < start:
            continue
        cost = _f(r.get("cost"))
        p = cost / n if n else 0.0
        if not 0 < p < 1:
            continue
        a = acc[(m15_name(r.get("series")), "live", r.get("ticker"))]
        a["cost"] += cost + _f(r.get("fee"))
        a["pnl"] += _f(r.get("pnl"))
        a["sd"] += n * math.sqrt(p * (1 - p))
        a.setdefault("at", r.get("close") or "")
    for r in trade_rows:
        if r.get("status") != "settled" or r.get("pnl") in (None, ""):
            continue
        n, p = int(_f(r.get("filled"))), _f(r.get("price"))
        if n < 1 or not 0 < p < 1:
            continue
        a = acc[(r.get("strategy") or "?", r.get("mode") or "paper",
                 r.get("event") or r.get("ticker"))]
        a["cost"] += n * p + _f(r.get("fee"))
        a["pnl"] += _f(r["pnl"])
        a["sd"] += n * math.sqrt(p * (1 - p))     # markets of one event: assume they move together
        a.setdefault("at", r.get("settled_at") or "")
    for r in weather_paper:                       # the day-before research paper log = weather on paper
        if r.get("pnl") in (None, "") or r.get("status") not in ("filled", "settled"):
            continue
        p = _f(r.get("price"))
        if not 0 < p < 1:
            continue
        a = acc[("weather", "paper", r.get("event") or r.get("ticker"))]
        a["cost"] += p
        a["pnl"] += _f(r["pnl"])
        a["sd"] += math.sqrt(p * (1 - p))
        a.setdefault("at", r.get("settled_at") or "")
    out: dict[str, list[dict]] = defaultdict(list)
    for (s, mode, ev), a in acc.items():
        out[s].append({"event": ev, "mode": mode, "cost": a["cost"], "pnl": a["pnl"],
                       "var": a["sd"] ** 2, "live": mode == "live", "at": a.get("at", "")})
    return dict(out)


def grade(units: list[dict], p: dict) -> dict:
    """Shrunk profit-per-dollar for one strategy's settled events."""
    h, tau = float(p["hurdle"]), float(p["prior_sd"])
    wsum = cost = pnl = var = 0.0
    n_live = n_paper = 0
    for u in units:
        w = 1.0 if u["live"] else float(p["paper_weight"])
        if w <= 0 or u["cost"] <= 0:
            continue
        cost += w * u["cost"]
        pnl += w * u["pnl"]
        var += w * u["var"]                       # var/w per unit, times w^2 in the weighted mean
        wsum += w
        n_live += u["live"]
        n_paper += not u["live"]
    out = {"events_live": n_live, "events_paper": n_paper, "risked": round(cost, 2),
           "profit": round(pnl, 2), "roi": None, "se": None, "trust": 0.0, "grade": h}
    if cost <= 0:
        return out
    roi, se2 = pnl / cost, var / cost ** 2
    trust = tau ** 2 / (tau ** 2 + se2) if se2 > 0 else 1.0
    out.update(roi=round(roi, 4), se=round(math.sqrt(se2), 4), trust=round(trust, 3),
               grade=round(h + trust * (roi - h), 4))
    return out


def target(g: float, p: dict) -> float:
    return min(float(p["ceiling"]), max(float(p["floor"]), g / float(p["hurdle"])))


def step(prev: float, tgt: float, p: dict) -> float:
    d = max(-float(p["max_step"]), min(float(p["max_step"]), tgt - prev))
    return round(prev + d, 3)


def due(cfg: Config, store: dict, now: datetime) -> bool:
    last = parse_iso(store.get("graded_at"))
    return last is None or now - last >= timedelta(days=float(params(cfg)["regrade_days"]))


def update(cfg: Config, trade_rows: list[dict], weather_paper: list[dict], names,
           now: datetime, store: dict, force: bool = False, m15_rows: list[dict] = ()) -> dict:
    """Fresh grades every call; multipliers move only once every `regrade_days`
    (or when forced). `store` is kept in data/trader_state.json ("allocator")."""
    p = params(cfg)
    last = parse_iso(store.get("graded_at"))
    grades = {s: grade(u, p) for s, u in results(trade_rows, weather_paper, m15_rows).items()}
    prev = store.get("multipliers", {}) or {}
    due = force or last is None or now - last >= timedelta(days=float(p["regrade_days"]))
    mult = {}
    for s in names:
        g = grades.get(s) or grade([], p)
        grades[s] = g
        g["target"] = round(target(g["grade"], p), 3)
        m = float(prev.get(s, 1.0))
        mult[s] = step(m, g["target"], p) if due else m
    if due:
        stamp = now.isoformat(timespec="seconds")
        store["graded_at"] = stamp
        store["multipliers"] = mult
        store["grades"] = {s: grades[s] for s in sorted(mult)}
        store["history"] = (store.get("history", []) + [{"at": stamp, "multipliers": mult}])[-26:]
    store["apply"] = bool(p["apply"])
    store["hurdle"] = p["hurdle"]
    return {**store, "multipliers": mult, "grades": {s: grades[s] for s in sorted(mult)}}


SIZE_KEYS = ("contracts", "max_contracts", "word_pct")


def apply_to(specs: dict, alloc: dict, p: dict) -> dict:
    """Scale each strategy's own cap and order size by its multiplier (live sizing).
    Returns new specs; per-event caps and the overall cap are left alone."""
    if not p.get("apply"):
        return specs
    mult = alloc.get("multipliers", {}) or {}
    out = {}
    for name, sp in specs.items():
        m = float(mult.get(name, 1.0))
        sp = dict(sp)
        if m != 1.0:
            if "max_open_dollars" in sp:
                sp["max_open_dollars"] = round(float(sp["max_open_dollars"]) * m, 2)
            for k in SIZE_KEYS:
                if k in sp:
                    v = float(sp[k]) * m
                    sp[k] = v if k == "word_pct" else max(1, int(round(v)))
            sp["_alloc_mult"] = m
        out[name] = sp
    return out


def order(names, alloc: dict, p: dict) -> list[str]:
    """Strategy run order: best multiplier first when applied (first claim on the overall cap)."""
    names = list(names)
    if not p.get("apply"):
        return names
    mult = alloc.get("multipliers", {}) or {}
    return sorted(names, key=lambda n: -float(mult.get(n, 1.0)))


def report_section(alloc: dict | None) -> list[str]:
    if not alloc:
        return []
    when = str(alloc.get("graded_at") or "")[:10]
    when = "not yet" if not when or when.startswith("9999") else when
    mode = "APPLIED to live sizing" if alloc.get("apply") else "shadow (reported only, sizing unchanged)"
    lines = ["## Strategy grades (allocator)", "",
             f"Mode: **{mode}** · last re-graded {when} · "
             f"hurdle {alloc.get('hurdle', 0.03) * 100:.0f}c profit per $1 risked", "",
             "| Strategy | Settled events (real/paper) | Risked | Profit | Per $1 | Trust | Grade | Size x |",
             "|---|---|---|---|---|---|---|---|"]
    for s, g in (alloc.get("grades") or {}).items():
        roi = "" if g.get("roi") is None else f"{g['roi'] * 100:+.1f}c"
        lines.append(f"| {s} | {g['events_live']}/{g['events_paper']} | ${g['risked']:.2f} | "
                     f"${g['profit']:+.2f} | {roi} | {g['trust']:.0%} | {g['grade'] * 100:+.1f}c | "
                     f"{alloc['multipliers'].get(s, 1.0):.2f} (target {g.get('target', 1.0):.2f}) |")
    lines += ["", "Trust = how much the grade follows the strategy's own record instead of the hurdle. "
              "It grows with settled events and grows slower for longshots (noisier per dollar).", ""]
    return lines
