"""Checkpoint scorecard: `python bot.py checkpoint`.

One command that turns the real-money record into a decision per live strategy,
using rules written down BEFORE the data arrived (CHECKPOINT.md, Nov 8 2026),
so the Nov 17 call can't be bent to fit the results.

Per strategy (REAL money only, from Nov 5 7 PM ET launch):
  * settled events (bets on one event added together, as the allocator does),
    money risked, profit, profit per $1, fees, hit rate
  * a 90% bootstrap range for profit per $1 (resampling whole events) and the
    share of resamples above zero
  * fills vs the backtest: fill rate of contracts offered, filled events per week
  * how many settled events the result needs before it means anything, and the
    date it gets there at the live pace
  * the allocator's would-be multiplier (shadow), and the verdict from the rules

Read-only: it never changes sizes, config or orders.
"""
from __future__ import annotations

import copy
import csv
import io
import math
import random
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta

from . import allocator
from .util import Config, parse_iso

# Backtest expectations (profit per $1 risked, filled events per week at live size).
# Sources: research_alloc/RESULTS.md (streams, Feb 2025-Nov 2026), research_mn/RESULTS.md
# (Mentions recent +4.3c/contract at ~85c), weather day-before ~+1c/contract this fall
# at ~45c (unproven; the taker proxy was -1.4c). event_sd = spread of one event's
# profit per $1 when the edge is real; None = work it out from the prices paid.
EXPECTED = {
    "mentions":   {"per_dollar": 0.04,  "price": 0.85, "event_sd": 0.29, "events_week": 10,
                   "source": "research_mn: +4-6c/contract, last 6 months +4.3c; per event sd 29%"},
    "favorites":  {"per_dollar": 0.038, "price": 0.94, "event_sd": None, "events_week": 5,
                   "source": "research_alloc streams: 659 fills, 97% hit, +3.8c per $1"},
    "macro_fade": {"per_dollar": 0.063, "price": 0.87, "event_sd": None, "events_week": 1.5,
                   "source": "research_alloc streams: 256 fills, 93% hit, +6.3c per $1"},
    "weather":    {"per_dollar": 0.02,  "price": 0.45, "event_sd": None, "events_week": 20,
                   "source": "day-before maker ~+1c/contract this fall (unproven)"},
    "weather_late": {"per_dollar": 0.05, "price": 0.74, "event_sd": None, "events_week": 45,
                     "source": "research_wx: late-day lows +3.7c/contract Sep-Nov 2026 at ~74c (backtest)"},
}

DEFAULTS = {
    "live_since": "2026-11-05T23:03:00+00:00",   # first real order (PR #26)
    "checkpoint": "2026-11-17",
    "ci": 0.90,
    "boot_reps": 4000,
    "seed": 17,
    "min_events": 20,          # below this no strategy gets a verdict on its edge
    "min_events_range": 10,    # below this the 90% range is not even shown (4 events can't make one)
    "power_z": 2.487,          # 1.645 (90% one-sided) + 0.842 (80% power)
    "loss_cut_pct": 0.15,      # a strategy down 15% of the bankroll in real money -> cut
    "fill_floor": 0.25,        # under 25% of contracts filled ...
    "fill_min_orders": 30,     # ... after 30+ orders -> pricing review
    "fee_flag": 0.5,           # fees eating half the expected edge -> flag
    "raise_x": 1.5, "cut_x": 0.5,
}


def params(cfg: Config) -> dict:
    raw = cfg.raw.get("checkpoint", {}) or {}
    p = {**DEFAULTS, **{k: v for k, v in raw.items() if k != "expected"}}
    p["expected"] = {s: {**e, **((raw.get("expected") or {}).get(s) or {})} for s, e in EXPECTED.items()}
    return p


def _f(x, d=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def live_rows(rows: list[dict], since: datetime | None) -> list[dict]:
    out = []
    for r in rows:
        if r.get("mode") != "live":
            continue
        at = parse_iso(r.get("placed_at"))
        if since and at and at < since:
            continue
        out.append(r)
    return out


def events(rows: list[dict]) -> dict[str, list[dict]]:
    """Settled real-money results per strategy, one entry per event."""
    acc: dict = defaultdict(lambda: {"cost": 0.0, "pnl": 0.0, "fee": 0.0, "n": 0, "sd": 0.0, "won": 0})
    for r in rows:
        if r.get("status") != "settled" or r.get("pnl") in (None, ""):
            continue
        n, p = int(_f(r.get("filled"))), _f(r.get("price"))
        if n < 1 or not 0 < p < 1:
            continue
        a = acc[(r.get("strategy") or "?", r.get("event") or r.get("ticker"))]
        a["cost"] += n * p + _f(r.get("fee"))
        a["pnl"] += _f(r["pnl"])
        a["fee"] += _f(r.get("fee"))
        a["n"] += n
        a["sd"] += n * math.sqrt(p * (1 - p))
        a["won"] += n * (_f(r["pnl"]) > 0)
    out: dict[str, list[dict]] = defaultdict(list)
    for (s, ev), a in acc.items():
        out[s].append({"event": ev, **a})
    return dict(out)


def bootstrap(units: list[dict], reps: int, ci: float, seed: int) -> tuple[float, float, float] | None:
    """(low, high, share above 0) for profit per $1, resampling whole events."""
    if len(units) < 2:
        return None
    rng = random.Random(seed)
    k, vals = len(units), []
    for _ in range(reps):
        c = pnl = 0.0
        for _ in range(k):
            u = units[rng.randrange(k)]
            c += u["cost"]
            pnl += u["pnl"]
        vals.append(pnl / c if c > 0 else 0.0)
    vals.sort()
    lo = vals[int((1 - ci) / 2 * reps)]
    hi = vals[min(reps - 1, int((1 + ci) / 2 * reps))]
    return lo, hi, sum(v > 0 for v in vals) / reps


def event_sd(units: list[dict], exp: dict) -> tuple[float, str]:
    """Spread of one event's profit per $1: measured once there are 20+ events,
    else the backtest figure, else worked out from the price (a p-cent contract
    pays (1-p)/p or loses 1, so sd = sqrt(p(1-p))/p)."""
    if len(units) >= 20:
        rois = [u["pnl"] / u["cost"] for u in units if u["cost"] > 0]
        m = sum(rois) / len(rois)
        return math.sqrt(sum((x - m) ** 2 for x in rois) / (len(rois) - 1)), "measured"
    if exp.get("event_sd"):
        return float(exp["event_sd"]), "backtest"
    p = float(exp["price"])
    return math.sqrt(p * (1 - p)) / p, "from price"


def needed(sd: float, edge: float, z: float) -> int:
    """Settled events at which a strategy that really earns `edge` shows a 90% range
    above zero 4 times out of 5."""
    if edge <= 0:
        return 10 ** 6
    return max(1, math.ceil((z * sd / edge) ** 2))


def fills(rows: list[dict]) -> dict:
    """Fill record of every real order placed. Orders still resting count as not
    filled yet, so the rate is a floor that rises as they fill."""
    offered = filled = orders = resting = 0
    prices = []
    for r in rows:
        if r.get("status") == "cancelled" and "reject" in (r.get("note") or "").lower():
            continue                              # never reached the book
        f = int(_f(r.get("filled")))
        resting += r.get("status") == "resting"
        orders += 1
        offered += int(_f(r.get("count")))
        filled += f
        if f:
            prices.append((_f(r.get("price")), f))
    avg = sum(p * n for p, n in prices) / sum(n for _, n in prices) if prices else None
    return {"orders": orders, "resting": resting, "offered": offered, "filled": filled,
            "rate": filled / offered if offered else None, "avg_price": avg}


def verdict(s: dict, p: dict) -> tuple[str, list[str]]:
    """The pre-registered rules (CHECKPOINT.md). First match wins."""
    why, n, exp = [], s["n"], s["exp"]["per_dollar"]
    ci = s["ci"]
    if s["profit"] < -p["loss_cut_pct"] * s["bankroll"]:
        return "CUT", [f"real loss ${-s['profit']:.2f} is over {p['loss_cut_pct']:.0%} of the bankroll"]
    if n >= p["min_events"] and ci and ci[1] < 0:
        return "KILL (back to paper)", ["clearly losing: the whole 90% range is below zero"]
    if n >= p["min_events"] and ci and ci[1] < exp and s["roi"] < 0:
        return "CUT", [f"losing, and the 90% range is below the backtest's {exp * 100:+.1f}c"]
    if n >= p["min_events"] and ci and ci[0] > 0:
        why.append("edge confirmed: the whole 90% range is above zero")
        return "RAISE", why
    if s["fills"]["orders"] >= p["fill_min_orders"] and (s["fills"]["rate"] or 0) < p["fill_floor"]:
        why.append(f"only {s['fills']['rate']:.0%} of contracts fill: review prices")
    if s["risked"] > 0 and s["fees"] > p["fee_flag"] * exp * s["risked"]:
        why.append("fees eat over half the expected edge")
    if n < p["min_events"]:
        why.insert(0, f"too early: {n} of {p['min_events']} settled events for any verdict")
    else:
        why.insert(0, "not proven either way: the 90% range includes zero")
    return "KEEP", why


def build(cfg: Config, all_rows: list[dict], now: datetime, alloc_store: dict | None,
          bankroll: float, names=None) -> dict:
    p = params(cfg)
    since = parse_iso(p["live_since"])
    rows = live_rows(all_rows, since)
    ev = events(rows)
    names = list(names or [n for n in p["expected"]
                           if ((cfg.raw.get("trading", {}) or {}).get(n) or {}).get("live")])
    days = max(0.5, (now - since).total_seconds() / 86400) if since else 1.0
    cp = parse_iso(p["checkpoint"] + "T00:00:00+00:00")

    # allocator's would-be move if it were re-graded now (on a copy: nothing is saved)
    store = copy.deepcopy(alloc_store or {})
    alloc_now = allocator.update(cfg, all_rows, [], names, now, store, force=True)
    prev_mult = (alloc_store or {}).get("multipliers", {}) or {}

    out = {"at": now, "days_live": days, "bankroll": bankroll, "params": p, "strategies": {}}
    for s in names:
        exp = p["expected"].get(s) or {"per_dollar": allocator.params(cfg)["hurdle"], "price": 0.5,
                                       "event_sd": None, "events_week": 1, "source": "no backtest"}
        units = ev.get(s, [])
        mine = [r for r in rows if r.get("strategy") == s]
        risked = sum(u["cost"] for u in units)
        profit = sum(u["pnl"] for u in units)
        fees = sum(u["fee"] for u in units)
        contracts = sum(u["n"] for u in units)
        won = sum(u["won"] for u in units)
        fl = fills(mine)
        filled_events = {r.get("event") or r.get("ticker") for r in mine if int(_f(r.get("filled"))) > 0}
        pending = len(filled_events - {u["event"] for u in units})
        pace = len(filled_events) / days * 7          # filled events per week
        sd, sd_src = event_sd(units, exp)
        need = needed(sd, float(exp["per_dollar"]), float(p["power_z"]))
        left = max(0, need - len(units))
        eta = None if pace <= 0 else now + timedelta(days=left / pace * 7)
        by_cp = len(units) + pending + (pace / 7 * max(0.0, (cp - now).total_seconds() / 86400) if cp else 0)
        g = alloc_now["grades"].get(s, {})
        row = {"name": s, "exp": exp, "n": len(units), "contracts": contracts, "risked": risked,
               "profit": profit, "fees": fees, "roi": profit / risked if risked else 0.0,
               "hit": won / contracts if contracts else None,
               "ci": (bootstrap(units, int(p["boot_reps"]), float(p["ci"]), int(p["seed"]))
                      if len(units) >= int(p["min_events_range"]) else None),
               "fills": fl, "pending": pending, "pace_week": pace, "sd": sd, "sd_src": sd_src,
               "need": need, "eta": eta, "by_checkpoint": by_cp, "bankroll": bankroll,
               "alloc_mult": float(prev_mult.get(s, 1.0)), "alloc_next": float(alloc_now["multipliers"].get(s, 1.0)),
               "alloc_target": g.get("target", 1.0), "alloc_trust": g.get("trust", 0.0),
               "alloc_roi": g.get("roi")}
        row["verdict"], row["why"] = verdict(row, p)
        out["strategies"][s] = row
    out["allocator"] = allocator_rule(out, alloc_store, p)
    return out


def allocator_rule(out: dict, store: dict | None, p: dict) -> tuple[str, list[str]]:
    """Switch the allocator from shadow to live sizing at the checkpoint if it is
    doing its sums right and nothing is broken (its moves are small and slow by
    design, so this mainly starts the clock)."""
    why = []
    ok = True
    for s, r in out["strategies"].items():
        if r["alloc_roi"] is not None and r["n"] and abs(r["alloc_roi"] - r["roi"]) > 0.01:
            ok = False
            why.append(f"{s}: allocator's {r['alloc_roi'] * 100:+.1f}c differs from the scorecard's "
                       f"{r['roi'] * 100:+.1f}c")
        if r["verdict"].startswith(("KILL", "CUT")):
            ok = False
            why.append(f"{s} is {r['verdict']}: settle that first")
    if store is None:
        ok = False
        why.append("allocator has not graded yet")
    if out.get("halted"):
        ok = False
        why.append("the bot is halted")
    if ok:
        why.append("grades match the scorecard's recount and no strategy is broken")
    return ("SWITCH ON (apply = true)" if ok else "STAY SHADOW"), why


def _pct(x) -> str:
    return "-" if x is None else f"{x * 100:+.1f}c"


def render(out: dict) -> str:
    p = out["params"]
    at = out["at"]
    L = [f"# Checkpoint scorecard ({at:%b %d %H:%M} UTC)", "",
         f"Real money only, since {p['live_since'][:16].replace('T', ' ')} UTC "
         f"({out['days_live']:.1f} days). Bankroll ${out['bankroll']:.2f}. "
         f"Decision day: {p['checkpoint']}. Rules: CHECKPOINT.md (written Nov 8, before the data).", "",
         f"Settled real money so far: ${sum(r['profit'] for r in out['strategies'].values()):+.2f} on "
         f"${sum(r['risked'] for r in out['strategies'].values()):.2f} risked.", "",
         "## Verdicts", "",
         "| Strategy | Verdict | Why |", "|---|---|---|"]
    for s, r in out["strategies"].items():
        L.append(f"| {s} | **{r['verdict']}** | {'; '.join(r['why'])} |")
    a, aw = out["allocator"]
    L += [f"| allocator | **{a}** | {'; '.join(aw)} |", "",
          "## Results (settled real-money events)", "",
          "| Strategy | Events | Contracts | Risked | Profit | Per $1 | 90% range | Chance > 0 | Hit rate | Fees | Backtest per $1 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s, r in out["strategies"].items():
        ci = r["ci"]
        rng = f"needs {p['min_events_range']}+" if not ci else f"{ci[0] * 100:+.0f}c to {ci[1] * 100:+.0f}c"
        ch = "-" if not ci else f"{ci[2]:.0%}"
        hit = "-" if r["hit"] is None else f"{r['hit']:.0%}"
        L.append(f"| {s} | {r['n']} | {r['contracts']} | ${r['risked']:.2f} | ${r['profit']:+.2f} | "
                 f"{_pct(r['roi'] if r['risked'] else None)} | {rng} | {ch} | {hit} | ${r['fees']:.2f} | "
                 f"{_pct(r['exp']['per_dollar'])} |")
    L += ["", "## Fills vs the backtest", "",
          "| Strategy | Orders | Still resting | Contracts offered | Filled | Fill rate so far | Avg price paid | Filled events/week | Backtest pace | Awaiting settlement |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for s, r in out["strategies"].items():
        f = r["fills"]
        rate = "-" if f["rate"] is None else f"{f['rate']:.0%}"
        ap = "-" if f["avg_price"] is None else f"{f['avg_price'] * 100:.0f}c"
        L.append(f"| {s} | {f['orders']} | {f['resting']} | {f['offered']} | {f['filled']} | {rate} | {ap} | "
                 f"{r['pace_week']:.1f} | {r['exp']['events_week']} | {r['pending']} |")
    L += ["", "## When does each result mean something?", "",
          "Events needed = settled events at which a strategy really earning its backtest edge "
          "would show a 90% range above zero 4 times out of 5.", "",
          "| Strategy | Spread per event | Events needed | Have | Expected by checkpoint | Reaches it (live pace) |",
          "|---|---|---|---|---|---|"]
    for s, r in out["strategies"].items():
        eta = "never at this pace" if r["eta"] is None else (
            "already" if r["n"] >= r["need"] else
            (f"{r['eta']:%b %d %Y}" if r["eta"].year < at.year + 5 else "5+ years"))
        L.append(f"| {s} | {r['sd'] * 100:.0f}% ({r['sd_src']}) | {r['need']:,} | {r['n']} | "
                 f"~{r['by_checkpoint']:.0f} | {eta} |")
    L += ["", "## Allocator (shadow) would-be sizes", "",
          "| Strategy | Now | If re-graded today | Target | Trust |", "|---|---|---|---|---|"]
    for s, r in out["strategies"].items():
        L.append(f"| {s} | {r['alloc_mult']:.2f}x | {r['alloc_next']:.2f}x | {r['alloc_target']:.2f}x | "
                 f"{r['alloc_trust']:.1%} |")
    L += ["", "Backtest sources:"]
    for s, r in out["strategies"].items():
        L.append(f"- {s}: {r['exp']['source']}")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- 15-minute bot (server)

# Research (Nov 8, PR #53): resting maker bid on the 71-95c favourite, back of the queue.
M15_EXPECTED = {"KXBTC15M": 0.073, "KXSILVER15M": 0.052, "KXCOPPER15M": 0.052}
M15_RULES = {"min_fills": 100,      # no verdict before 100 filled markets per series
             "cut_below": -0.05,    # after 100 fills, under -5c per contract -> that series back to paper
             "raise_fills": 300,    # 300+ fills and the 90% range above zero -> 2 contracts per market
             "price": 0.85}


def m15_rows(path=None) -> list[dict]:
    """The server bot's market log: local data/m15/markets-*.csv if this machine has it
    (the server itself), else the m15-data branch the server uploads every 15 min."""
    from .m15 import M15_DIR
    from pathlib import Path
    d = Path(path) if path else M15_DIR
    files = sorted(d.glob("markets-*.csv")) if d.exists() else []
    if files:
        out = []
        for f in files:
            with open(f, newline="", encoding="utf-8") as fh:
                out += list(csv.DictReader(fh))
        return out
    try:
        subprocess.run(["git", "fetch", "-q", "origin", "m15-data"], timeout=60,
                       capture_output=True, check=False)
        names = subprocess.run(["git", "ls-tree", "--name-only", "origin/m15-data", "m15/"],
                               capture_output=True, text=True, timeout=30).stdout.split()
        out = []
        for n in sorted(x for x in names if x.startswith("m15/markets-")):
            txt = subprocess.run(["git", "show", f"origin/m15-data:{n}"], capture_output=True,
                                 text=True, timeout=30).stdout
            out += list(csv.DictReader(io.StringIO(txt)))
        return out
    except (OSError, subprocess.SubprocessError):
        return []


def m15_build(rows: list[dict], now: datetime, p: dict) -> dict:
    """Per live series: one market = one result (one contract each)."""
    out = {}
    live = [r for r in rows if r.get("mode") == "live"]
    for series in sorted(set(M15_EXPECTED) | {r.get("series") for r in live if r.get("series")}):
        rs = [r for r in live if r.get("series") == series]
        quoted = len(rs)
        filled = [r for r in rs if int(_f(r.get("filled"))) > 0 and r.get("pnl") not in (None, "")]
        units = [{"cost": _f(r.get("cost")) + _f(r.get("fee")), "pnl": _f(r["pnl"])} for r in filled]
        n = len(units)
        contracts = sum(int(_f(r.get("filled"))) for r in filled)
        cost = sum(u["cost"] for u in units)
        pnl = sum(u["pnl"] for u in units)
        exp = M15_EXPECTED.get(series, 0.03)
        closes = [parse_iso(r.get("close")) for r in rs if parse_iso(r.get("close"))]
        span = max(0.25, (now - min(closes)).total_seconds() / 86400) if closes else None
        pace = n / span if span else 0.0               # filled markets per day
        sd = math.sqrt(M15_RULES["price"] * (1 - M15_RULES["price"]))   # per contract
        need = needed(sd, exp, float(p["power_z"]))
        ci = bootstrap(units, int(p["boot_reps"]), float(p["ci"]), int(p["seed"])) if n >= 10 else None
        per_c = pnl / contracts if contracts else None
        if n >= M15_RULES["min_fills"] and ci and ci[1] < 0:
            v, why = "KILL (back to paper)", "clearly losing: the whole 90% range is below zero"
        elif n >= M15_RULES["min_fills"] and per_c is not None and per_c < M15_RULES["cut_below"]:
            v, why = "CUT (back to paper)", f"{per_c * 100:+.1f}c per contract is under -5c after {n} fills"
        elif n >= M15_RULES["raise_fills"] and ci and ci[0] > 0:
            v, why = "RAISE (2 contracts)", "edge confirmed: the whole 90% range is above zero"
        else:
            v, why = "KEEP", (f"too early: {n} of {M15_RULES['min_fills']} fills for a verdict"
                              if n < M15_RULES["min_fills"] else "not proven either way")
        eta = None if pace <= 0 else now + timedelta(days=max(0, need - n) / pace)
        out[series] = {"quoted": quoted, "n": n, "contracts": contracts, "cost": cost, "pnl": pnl,
                       "per_c": per_c, "roi": pnl / cost if cost else None, "ci": ci, "exp": exp,
                       "rate": n / quoted if quoted else None, "pace": pace, "need": need,
                       "eta": eta, "verdict": v, "why": why}
    return out


def m15_render(m: dict, at: datetime) -> str:
    if not m:
        return ""
    L = ["", "## 15-minute bot (server, real money)", "",
         "One market = one result. Rules: CHECKPOINT.md, section 15-minute bot.", "",
         "| Series | Verdict | Quoted | Filled | Fill rate | Profit | Per contract | 90% range (per $1) | Backtest per contract | Fills needed | Reaches it |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s, r in m.items():
        rate = "-" if r["rate"] is None else f"{r['rate']:.0%}"
        ci = "needs 10+" if not r["ci"] else f"{r['ci'][0] * 100:+.0f}c to {r['ci'][1] * 100:+.0f}c"
        eta = ("already" if r["n"] >= r["need"] else "no fills yet" if r["eta"] is None
               else f"{r['eta']:%b %d}")
        L.append(f"| {s} | **{r['verdict']}** ({r['why']}) | {r['quoted']} | {r['n']} | {rate} | "
                 f"${r['pnl']:+.2f} | {_pct(r['per_c'])} | {ci} | {_pct(r['exp'])} | {r['need']} | {eta} |")
    return "\n".join(L) + "\n"


def run(cfg: Config, now: datetime) -> str:
    from . import trader
    from .util import read_json
    state = read_json(trader.STATE, {}) or {}
    t = trader.settings(cfg)
    out = build(cfg, trader.load_all(), now, state.get("allocator"), float(t["bankroll"]))
    out["halted"] = state.get("halted")
    if out["halted"]:
        out["allocator"] = allocator_rule(out, state.get("allocator"), out["params"])
    text = render(out)
    try:
        text += m15_render(m15_build(m15_rows(), now, out["params"]), now)
    except Exception as exc:  # the server bot's data must never break the scorecard
        text += f"\n15-minute bot: could not read its data ({exc}).\n"
    return text
