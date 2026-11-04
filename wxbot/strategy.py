"""Turn model probabilities + market prices into sized trade suggestions."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import kalshi
from .model import DayModel

DEFAULTS = {
    "min_edge_lock": 0.03,
    "min_edge_model": 0.07,
    "day_ahead_extra_edge": 0.03,
    "market_blend": 0.25,
    "max_model_disagreement": 0.35,
    "min_price_model": 0.10,
    "max_price_model": 0.95,
    "max_price_lock": 0.97,
    "lock_prob": 0.995,
    "kelly_fraction": 0.25,
    "max_bet_frac_lock": 0.10,
    "max_bet_frac_model": 0.05,
    "max_event_frac": 0.12,
    "max_open_frac": 0.40,
    "min_expected_profit": 0.02,
    "min_volume": 0,
}


@dataclass
class Opportunity:
    ticker: str
    event: str
    series: str
    city: str
    metric: str
    date: str
    lo: int | None
    hi: int | None
    side: str               # "yes" or "no"
    kind: str               # "lock" or "model"
    price: float            # ask we'd pay, dollars
    model_p: float          # model probability our side wins
    fair_p: float           # after blending with the market
    market_p: float         # market's implied probability for our side
    edge: float             # fair_p - price - fee per contract
    contracts: int = 0
    cost: float = 0.0
    fee: float = 0.0
    exp_profit: float = 0.0
    depth: float | None = None
    day_ahead: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.ticker}|{self.side}"

    @property
    def label(self) -> str:
        return kalshi.bracket_label(self.lo, self.hi)


def kelly_contracts(p: float, price: float, bankroll: float, kelly_frac: float,
                    max_frac: float) -> int:
    """Fractional-Kelly size for a $1 binary bought at `price` (fee included)."""
    cost = price + kalshi.fee_per_contract(price)
    if p <= cost or cost >= 1:
        return 0
    f = kelly_frac * (p - cost) / (1.0 - cost)
    f = min(f, max_frac)
    n = int((f * bankroll) // cost)
    if n == 0 and cost <= max_frac * bankroll:
        n = 1   # smallest possible position, still within the per-trade cap
    return n


def evaluate_event(markets: list[dict], dm: DayModel, *, series: str, city: str,
                   metric: str, date: str, day_ahead: bool, params: dict | None = None,
                   locks_only: bool = False) -> tuple[list[Opportunity], list[dict]]:
    """Score every bracket in one city-day. Returns (opportunities, all_rows).

    locks_only: data is incomplete (no forecast, or no/stale readings), so only
    brackets already decided by observations may be suggested."""
    P = {**DEFAULTS, **(params or {})}
    opps, rows = [], []
    for m in markets:
        br = kalshi.parse_bracket(m)
        if br is None:
            continue
        lo, hi = br
        p_yes = dm.prob(lo, hi)
        lock = dm.lock_side(lo, hi)
        yes_ask, no_ask = kalshi.price(m, "yes_ask"), kalshi.price(m, "no_ask")
        yes_bid = kalshi.price(m, "yes_bid")
        if kalshi.tradable(yes_bid) and kalshi.tradable(yes_ask):
            mkt_yes = (yes_bid + yes_ask) / 2
        elif kalshi.tradable(yes_ask) and kalshi.tradable(no_ask):
            mkt_yes = (yes_ask + (1 - no_ask)) / 2
        else:
            last = kalshi.price(m, "last_price")
            mkt_yes = last if last is not None and 0 < last < 1 else None
        rows.append({"ticker": m.get("ticker"), "bracket": kalshi.bracket_label(lo, hi),
                     "model_yes": round(p_yes, 4), "market_yes": mkt_yes,
                     "yes_ask": yes_ask, "no_ask": no_ask, "lock": lock})
        vol = m.get("volume_fp") or m.get("volume") or 0
        try:
            vol = float(vol)
        except (TypeError, ValueError):
            vol = 0.0

        for side, ask in (("yes", yes_ask), ("no", no_ask)):
            if not kalshi.tradable(ask):
                continue
            model_p = p_yes if side == "yes" else 1.0 - p_yes
            market_p = None if mkt_yes is None else (mkt_yes if side == "yes" else 1 - mkt_yes)
            if lock is not None and lock != side:
                continue
            notes = []
            if lock == side:
                kind = "lock"
                fair = max(min(model_p, P["lock_prob"]), P["lock_prob"] - 0.01)
                if ask > P["max_price_lock"]:
                    continue
                threshold = P["min_edge_lock"]
            else:
                kind = "model"
                if locks_only:
                    continue
                if not (P["min_price_model"] <= ask <= P["max_price_model"]):
                    continue
                if vol < P["min_volume"]:
                    continue
                if market_p is not None and abs(model_p - market_p) > P["max_model_disagreement"]:
                    continue  # too far from the crowd: more likely our data is off
                fair = model_p if market_p is None else (
                    (1 - P["market_blend"]) * model_p + P["market_blend"] * market_p)
                fair = min(max(fair, 0.01), 0.97)
                threshold = P["min_edge_model"] + (P["day_ahead_extra_edge"] if day_ahead else 0.0)
            edge = fair - ask - kalshi.fee_per_contract(ask)
            if edge < threshold:
                continue
            opps.append(Opportunity(
                ticker=m.get("ticker", ""), event=m.get("event_ticker", ""), series=series,
                city=city, metric=metric, date=date, lo=lo, hi=hi, side=side, kind=kind,
                price=ask, model_p=model_p, fair_p=fair,
                market_p=market_p if market_p is not None else float("nan"),
                edge=edge, day_ahead=day_ahead, notes=notes))
    opps.sort(key=lambda o: (o.kind != "lock", -o.edge))
    return opps, rows


def size(opp: Opportunity, bankroll: float, open_total: float, open_event: float,
         depth: float | None, params: dict | None = None) -> Opportunity:
    """Fill in contracts/cost/fee, respecting per-trade, per-event and total caps."""
    P = {**DEFAULTS, **(params or {})}
    max_frac = P["max_bet_frac_lock"] if opp.kind == "lock" else P["max_bet_frac_model"]
    n = kelly_contracts(opp.fair_p, opp.price, bankroll, P["kelly_fraction"], max_frac)
    unit = opp.price
    room = min(P["max_open_frac"] * bankroll - open_total,
               P["max_event_frac"] * bankroll - open_event)
    n = min(n, int(max(room, 0) // unit)) if unit > 0 else 0
    if depth is not None:
        opp.depth = depth
        n = min(n, int(depth))
    opp.contracts = max(n, 0)
    opp.cost = round(opp.contracts * opp.price, 2)
    opp.fee = kalshi.taker_fee(opp.contracts, opp.price) if opp.contracts else 0.0
    opp.exp_profit = round(opp.contracts * opp.fair_p - opp.cost - opp.fee, 4)
    if opp.contracts and opp.exp_profit < P["min_expected_profit"]:
        opp.notes.append("expected profit below minimum")
    return opp
