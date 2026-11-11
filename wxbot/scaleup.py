"""Scale-up kit: risk limits that tighten as the bankroll grows (built Nov 10, 2026).

SWITCHED OFF ([scaleup] enabled = false) until the 500-contract milestone reviews and the
Nov 17 checkpoint pass and Alex OKs real deposits. Nothing here changes trading while it is
off, except that the 15-minute bot keeps a per-coin record of its current step so the
contract step-ups are ready the day it is switched on.

What it does when on:

1. Bankroll tiers (Alex, Nov 10: "when its real money ... we will work more to limit those
   potential losses"). Each tier lists limits for the main bot (live cap, daily stop, total
   stop) and the 15-minute bot (24h stop, total stop, window guard). The highest tier whose
   min_bankroll the bankroll reaches applies. Tiers can only TIGHTEN: every % limit becomes
   min(configured, tier), and the window guard can only be switched on, never off.
   So a looser value in config.toml never wins, and a tier never loosens anything.

2. 15-minute contract steps 1 -> 2 -> 4 (Nov 9 plan: "one contract at a time, not price
   ladders"). A coin moves up one step only after its 500-contract review count, at least
   step_min_contracts real contracts at its current step earning step_min_ct or better,
   step_min_days at the step, and the bankroll for the next step. It moves back one step
   when its last step_min_contracts at a step fall below step_down_ct. The 80-87c extra
   contract scales with the step (1+1, 2+2, 4+4). Pool, stops and the cut rule still apply.

3. Push budget for >300 orders a day: after max_trade_pushes_per_day trade pushes in an
   ET day, the main bot stops pushing routine fills/sales (one note says so); stops and
   warnings (priority 4+) always push. Everything still lands on the dashboard and reports.
"""
from __future__ import annotations

from datetime import datetime

DEFAULTS = {
    "enabled": False,
    # Lowest tier first. Keys without "m15_" are the main (Actions) bot's [trading] keys;
    # "m15_" keys are the 15-minute bot's [m15] keys.
    "tiers": [
        {"name": "test money", "min_bankroll": 0},
        {"name": "small real", "min_bankroll": 300,
         "max_open_pct": 0.50, "daily_loss_pct": 0.08, "total_loss_pct": 0.20,
         "m15_daily_loss_pct": 0.15, "m15_total_loss_pct": 0.25, "m15_stop_guard": True},
        {"name": "real", "min_bankroll": 1000,
         "max_open_pct": 0.50, "daily_loss_pct": 0.06, "total_loss_pct": 0.15,
         "m15_daily_loss_pct": 0.10, "m15_total_loss_pct": 0.15, "m15_stop_guard": True},
        {"name": "large", "min_bankroll": 5000,
         "max_open_pct": 0.50, "daily_loss_pct": 0.04, "total_loss_pct": 0.10,
         "m15_daily_loss_pct": 0.06, "m15_total_loss_pct": 0.10, "m15_stop_guard": True},
    ],
    "steps": [1, 2, 4],
    "step_min_bankroll": [0, 150, 400],
    "step_min_review": 500,
    "step_min_contracts": 300,
    "step_min_ct": 0.01,
    "step_min_days": 3,
    "step_down_ct": -0.02,
    "step_scales_extra": True,
    "max_trade_pushes_per_day": 40,
}

# limits a tier may tighten; everything else in a tier is ignored
MAIN_PCTS = ("max_open_pct", "daily_loss_pct", "total_loss_pct")
M15_PCTS = ("daily_loss_pct", "total_loss_pct", "pool_pct")


def params(cfg) -> dict:
    raw = (getattr(cfg, "raw", None) or {}).get("scaleup", {}) or {}
    p = dict(DEFAULTS)
    p.update(raw)
    p["enabled"] = p.get("enabled") is True
    return p


def tier(p: dict, bankroll: float | None) -> dict:
    """The highest tier the bankroll reaches (the first tier if bankroll is unknown)."""
    tiers = sorted(p.get("tiers") or [], key=lambda x: float(x.get("min_bankroll", 0)))
    if not tiers:
        return {"name": "none", "min_bankroll": 0}
    best = tiers[0]
    for t in tiers:
        if bankroll is not None and bankroll >= float(t.get("min_bankroll", 0)):
            best = t
    return best


def _tighten(cur, new):
    if new is None:
        return cur
    if cur is None:
        return float(new)
    return min(float(cur), float(new))


def main_limits(t: dict, p: dict, bankroll: float | None) -> tuple[dict, dict | None]:
    """[trading] settings with the tier applied (only when the kit is on).
    Returns (settings, tier or None)."""
    if not p["enabled"]:
        return t, None
    tr = tier(p, bankroll)
    out = dict(t)
    for k in MAIN_PCTS:
        if k in tr:
            out[k] = _tighten(out.get(k), tr[k])
    return out, tr


def m15_limits(s: dict, p: dict, bankroll: float | None) -> tuple[dict, dict | None]:
    """[m15] settings with the tier applied (only when the kit is on)."""
    if not p["enabled"]:
        return s, None
    tr = tier(p, bankroll)
    out = dict(s)
    for k in M15_PCTS:
        if "m15_" + k in tr:
            out[k] = _tighten(out.get(k), tr["m15_" + k])
    if tr.get("m15_stop_guard"):
        out["stop_guard"] = True
    return out, tr


# ---------------------------------------------------------------- 15-minute contract steps

def max_step_for_bankroll(p: dict, bankroll: float | None) -> int:
    need = list(p.get("step_min_bankroll") or [0])
    top = 0
    for i in range(len(p["steps"])):
        if bankroll is not None and bankroll >= float(need[i] if i < len(need) else need[-1]):
            top = i
    return top


def step_contracts(p: dict, st: dict | None, bankroll: float | None, base: int) -> int:
    """Base contracts per market for one coin. Off -> the configured base."""
    if not p["enabled"]:
        return int(base)
    i = int((st or {}).get("step", 0))
    i = max(0, min(i, max_step_for_bankroll(p, bankroll), len(p["steps"]) - 1))
    return int(p["steps"][i])


def record_step(p: dict, st: dict, contracts: float, pnl: float, now: float,
                bankroll: float | None, cut: bool) -> str | None:
    """Add one graded live market to the coin's record at its current step, then move up or
    down one step if the rules say so. Records even while the kit is off (steps only move
    when it is on). Returns "up"/"down" when the step changed."""
    st.setdefault("step", 0)
    st.setdefault("step_since", now)
    at = st.setdefault("at_step", [0.0, 0.0])
    at[0] = round(at[0] + contracts, 2)
    at[1] = round(at[1] + pnl, 4)
    rec = st.setdefault("step_recent", [])
    rec.append([contracts, round(pnl, 4)])
    win = int(p["step_min_contracts"])
    while len(rec) > 1 and sum(r[0] for r in rec) - rec[0][0] >= win:
        rec.pop(0)
    if not p["enabled"]:
        return None
    i = int(st["step"])
    rn = sum(r[0] for r in rec)
    rper = sum(r[1] for r in rec) / rn if rn else 0.0
    if i > 0 and rn >= win and rper < float(p["step_down_ct"]):
        _move(st, i - 1, now)
        return "down"
    if cut or i + 1 >= len(p["steps"]):
        return None
    per = at[1] / at[0] if at[0] else 0.0
    ok = (float(st.get("fills", 0)) >= float(p["step_min_review"])
          and at[0] >= win and per >= float(p["step_min_ct"])
          and now - float(st["step_since"]) >= float(p["step_min_days"]) * 86400
          and max_step_for_bankroll(p, bankroll) >= i + 1)
    if ok:
        _move(st, i + 1, now)
        return "up"
    return None


def _move(st: dict, i: int, now: float) -> None:
    st["step"] = i
    st["step_since"] = now
    st["at_step"] = [0.0, 0.0]
    st["step_recent"] = []


# ---------------------------------------------------------------- push budget

def push_allowed(p: dict, state: dict, now: datetime, priority: int) -> tuple[bool, bool]:
    """(send it?, send the one 'budget reached' note?). Counts trade pushes per ET day in
    state["pushes"]. Off, or priority 4+, always sends."""
    if not p["enabled"] or priority >= 4:
        return True, False
    day = now.date().isoformat()
    c = state.get("pushes") or {}
    if c.get("day") != day:
        c = {"day": day, "n": 0, "held": 0}
    cap = int(p["max_trade_pushes_per_day"])
    state["pushes"] = c
    if c["n"] < cap:
        c["n"] += 1
        return True, False
    c["held"] = int(c.get("held", 0)) + 1
    return False, c["held"] == 1


def describe(p: dict, bankroll: float | None) -> str:
    """One line for REPORT.md."""
    tr = tier(p, bankroll)
    if not p["enabled"]:
        return (f"Scale-up kit: **OFF** (switches on after the 500-contract reviews + Nov 17 checkpoint). "
                f"At this bankroll it would use the '{tr.get('name', '?')}' tier.")
    return f"Scale-up kit: **ON**, tier '{tr.get('name', '?')}' (from ${float(tr.get('min_bankroll', 0)):,.0f})."
