"""Daily risk-adjusted ranking of liquidity-reward markets (SHADOW, Nov 13, 2026).

Alex asked (Nov 13): is it worth re-picking the rewards markets once a day so we always sit in the
ones that best balance reward against risk? The live bot (wxbot/lip.py select()) already re-picks
every 6 h, but it ranks on estimated reward per dollar only, with a hard "24 h volume <= 2,000"
filter as its whole fill-risk model. This module scores each market on

    net $/day = expected reward $/day x news factor  -  expected fill loss $/day

and the bot logs what it WOULD hold next to what it holds now. Plain arithmetic, no AI calls.
It moves no orders: the switch-on is a separate decision after the Nov 15 payout shows whether
the reward estimates are real.

Pieces (all per market, for the bid plan_market() would place):
  reward      share x pool/2/days, as the live bot computes it ("capped" model: others count only
              up to the target size), and a "conservative" model where everyone resting at or above
              the reference price shares pro-rata with us. Saturday's payout tells us which is right.
  fill flow   contracts per day that SOLD our side at or below our price over the last 7 days
              (public trade tape; a trade there is exactly what would have reached our bid).
  fill chance our expected filled contracts/day = n x (1 - exp(-flow / (queue at our price + n))).
  fill loss   filled x price x `adverse` (assumption 0.6: a longshot that someone dumps on us is
              worth ~40% of what we paid; calibrate once real fills exist).
  news factor x0.5 if the last price moved >= move_guard in a day, or our news guard tripped there
              in the last 3 days (we'd keep getting pulled, so we'd earn less of the day).
"""
from __future__ import annotations

import math

ADVERSE = 0.6          # share of the price lost on a filled bid (assumption, see above)
FLOW_DAYS = 7.0
MAX_PER_EVENT = 2      # correlated news: at most 2 picks in one event
SWAP_EDGE = 1.25       # a newcomer must beat the market it replaces by 25% ...
SWAP_MIN = 0.05        # ... and by at least 5c/day, so we don't churn on noise


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def taker_long(t: dict) -> str | None:
    ts = (t.get("taker_side") or "").lower()
    if ts not in ("yes", "no"):
        return None
    return ts if (t.get("taker_action") or "buy") == "buy" else ("no" if ts == "yes" else "yes")


def yes_price(t: dict) -> float | None:
    p = _f(t.get("yes_price_dollars"))
    if p is None:
        p = _f(t.get("yes_price"))
        if p is not None and p > 1.5:
            p /= 100.0
    return p


def fill_flow(trades: list, side: str, px: float, since: float, ts_of) -> float:
    """Contracts per day that sold `side` at or below `px` since `since` (the flow that would have
    reached a resting bid at px). `ts_of(t)` -> unix seconds or None."""
    tot = 0.0
    for t in trades:
        ts = ts_of(t)
        if ts is None or ts < since:
            continue
        tl, yp = taker_long(t), yes_price(t)
        if tl is None or yp is None or tl == side:
            continue                     # the taker bought our side: not a hit on our bid
        mine = yp if side == "yes" else 1 - yp
        if mine <= px + 1e-9:
            tot += _f(t.get("count_fp"), _f(t.get("count"), 0.0)) or 0.0
    return tot / FLOW_DAYS


def queue_at(levels: list, px: float) -> float:
    return sum(q for p, q in levels if abs(p - px) < 1e-9)


def at_or_above(levels: list, px: float) -> float:
    return sum(q for p, q in levels if p >= px - 1e-9)


def score(plan: dict, levels: list, flow_day: float, last_move: float | None, tripped: bool,
          move_guard: float = 0.03, adverse: float = ADVERSE) -> dict:
    """Risk-adjusted value of one plan_market() bid. `levels` = our side of the book WITHOUT our
    own order."""
    n, px = plan["n"], plan["px"]
    reward = plan["est_day"]
    pool_side = plan["pool"] / 2 / max(1.0, plan["days"])
    rest = at_or_above(levels, px)
    share_cons = n / (rest + n) if n else 0.0
    reward_cons = share_cons * pool_side if reward > 0 else 0.0
    q = queue_at(levels, px)
    fills = n * (1 - math.exp(-flow_day / (q + n))) if flow_day > 0 and n else 0.0
    loss = fills * px * adverse
    jumpy = (last_move is not None and abs(last_move) >= move_guard - 1e-9) or tripped
    nf = 0.5 if jumpy else 1.0
    return {"reward_day": round(reward, 4), "reward_cons_day": round(reward_cons, 4),
            "share_cons": round(share_cons, 4), "flow_day": round(flow_day, 1),
            "fill_ct_day": round(fills, 2), "fill_loss_day": round(loss, 4), "news_factor": nf,
            "net_day": round(reward * nf - loss, 4), "net_cons_day": round(reward_cons * nf - loss, 4)}


def pick(rows: list, k: int, current: set, key: str = "net_day") -> list:
    """Top `k` by `key`, at most MAX_PER_EVENT per event, keeping a current market unless a
    newcomer beats it by SWAP_EDGE and SWAP_MIN."""
    rows = sorted((r for r in rows if r.get(key, 0) > 0), key=lambda r: -r[key])
    out, per_ev = [], {}

    def ok(r):
        return per_ev.get(r.get("event"), 0) < MAX_PER_EVENT

    for r in rows:
        if len(out) >= k:
            break
        if ok(r):
            out.append(r)
            per_ev[r.get("event")] = per_ev.get(r.get("event"), 0) + 1
    # hysteresis: a held market that just missed the cut stays if the weakest newcomer isn't clearly better
    for r in rows:
        if r["ticker"] not in current or r in out:
            continue
        new = [x for x in out if x["ticker"] not in current]
        if not new:
            break
        weakest = min(new, key=lambda x: x[key])
        if weakest[key] < r[key] * SWAP_EDGE or weakest[key] - r[key] < SWAP_MIN:
            out[out.index(weakest)] = r
    return sorted(out, key=lambda r: -r[key])
