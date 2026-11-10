"""15-min fade: replay bid policies on recorded Kalshi trade tapes (research only, never trades).

usage: python research_fillq/replay.py TRADES.csv.gz MARKETS.csv.gz [LABEL] [--since UNIX]

TRADES: ticker,t,yes_price,count,taker_side   (research_fillq/fetch.py or research_spot/fetch.py)
MARKETS: ticker,...,close_ts,result,...

Book model (tape only, no order book history): the favourite's best bid is the price of the
latest trade that touched it. A longshot taker (seller of the favourite) shows the bid itself;
a favourite taker shows the ask, and the bid is taken as 1c under it. That is close for BTC and
silver (1c spreads most of the time) and lags when the price moves fast, so every number here is
a comparison between policies on the same tape, not a forecast of live profit.

Policies (1 contract per market, the bot's 71-95c band, requote up after 5 s, one side per market):
  join      bot today: rest at the best bid, back of the queue. Fill only when a longshot taker
            prints THROUGH our price (the whole level was used up).
  join_touch  same order, front-of-queue bound: fill as soon as anything prints at our price.
  improve   rest 1c above the best bid when the spread leaves room (est. ask >= bid + 2c), else
            join. We are alone at the new best price, so the next longshot taker fills us.
  improve_force  always 1c above, even into a 1c spread (would be refused live as post-only
            cross; only shows the ceiling).
Start times: bid only in the last S seconds of the window (S in START_S).

After each fill the favourite's estimated mid 10 s / 60 s / 5 min later (or the settlement past
the close) is compared with our price: the same adverse-selection numbers the live log records.
"""
from __future__ import annotations

import csv
import gzip
import math
import random
import sys
from collections import defaultdict

MIN_FAV, MAX_FAV = 0.71, 0.95
REQUOTE_S = 5
FEE = 0.0175
START_S = (600, 480, 360, 300, 240, 180, 120, 60)
POLICIES = ("join", "join_touch", "improve", "improve_force")
HORIZ = (10, 60, 300)


def fee(p):
    return math.ceil(round(FEE * p * (1 - p) * 100, 6)) / 100.0


def load(trades_path, markets_path, since=0):
    res = {}
    with gzip.open(markets_path, "rt") as f:
        for r in csv.DictReader(f):
            if r["result"] in ("yes", "no") and int(r["close_ts"]) >= since:
                res[r["ticker"]] = (int(r["close_ts"]), r["result"])
    tapes = defaultdict(list)
    with gzip.open(trades_path, "rt") as f:
        for r in csv.DictReader(f):
            if r["ticker"] in res and r["taker_side"] in ("yes", "no"):
                tapes[r["ticker"]].append((int(r["t"]), float(r["yes_price"]), float(r["count"]),
                                           r["taker_side"]))
    for v in tapes.values():
        v.sort(key=lambda x: x[0])         # stable: keeps the API's order inside a second
    return res, tapes


def book_path(tape):
    """[(t, yes_bid, yes_ask)] after each trade, from the tape alone."""
    out, yb, ya = [], None, None
    for t, yp, _n, side in tape:
        if side == "no":            # sold YES into the YES bid
            yb = yp
            if ya is None or ya <= yb:
                ya = round(yb + 0.01, 2)
        else:                       # bought YES from the YES ask
            ya = yp
            if yb is None or yb >= ya:
                yb = round(ya - 0.01, 2)
        out.append((t, yb, ya))
    return out


def fav(yb, ya):
    """(side, bid, ask) of the favourite if its bid is in the band."""
    if yb is not None and MIN_FAV - 1e-9 <= yb <= MAX_FAV + 1e-9:
        return "yes", yb, ya
    if ya is not None:
        nb, na = round(1 - ya, 2), (round(1 - yb, 2) if yb is not None else None)
        if MIN_FAV - 1e-9 <= nb <= MAX_FAV + 1e-9:
            return "no", nb, na
    return None


def mid_at(book, side, t):
    """Favourite-side mid at time t (last book state at or before t)."""
    lo, hi = 0, len(book)
    while lo < hi:
        m = (lo + hi) // 2
        if book[m][0] <= t:
            lo = m + 1
        else:
            hi = m
    if lo == 0:
        return None
    _t, yb, ya = book[lo - 1]
    if yb is None or ya is None:
        return None
    m = (yb + ya) / 2
    return m if side == "yes" else 1 - m


def simulate(tape, book, close, result, start_s, policy):
    """One market. Returns None (no fill) or a dict about the fill."""
    t_from = close - start_s
    order = None              # (side, q, t0)
    for i, (t, yp, n, tside) in enumerate(tape):
        if t >= close:
            break
        # 1. does this trade fill the resting order? (before the book update it causes)
        if order is not None and t > order[2]:
            side, q, _t0, improved = order
            long_side = "no" if side == "yes" else "yes"
            if tside == long_side:
                lp = yp if long_side == "yes" else 1 - yp        # what the longshot taker paid
                through = lp > 1 - q + 0.005
                touch = lp > 1 - q - 0.005
                front = policy == "join_touch" or improved       # alone at a new best price
                if through or (touch and front):
                    return _fill(side, q, t, close, result, book)
        if t < t_from:
            continue
        # 2. requote from the book after this trade
        _t, yb, ya = book[i]
        f = fav(yb, ya)
        if f is None:
            continue
        side, bid, ask = f
        q, improved = bid, False
        if policy == "improve_force" or (policy == "improve" and ask is not None and ask >= bid + 0.0199):
            if bid + 0.01 <= MAX_FAV + 1e-9:
                q, improved = round(bid + 0.01, 2), True
        if order is None:
            order = (side, q, t, improved)
        elif side != order[0]:
            order = (side, q, t, improved)                  # favourite flipped: requote
        elif q > order[1] + 1e-9 and t - order[2] >= REQUOTE_S:
            order = (side, q, t, improved)
    return None


def _fill(side, q, t, close, result, book):
    won = result == side
    out = {"q": q, "t": t, "ttc": close - t, "won": won,
           "pnl": (1.0 if won else 0.0) - q - fee(q)}
    for h in HORIZ:
        if t + h >= close:
            out[h] = (1.0 if won else 0.0) - q
        else:
            m = mid_at(book, side, t + h)
            out[h] = None if m is None else m - q
    return out


def ci90(per_market, n_boot=400, seed=1):
    vals = [v for v in per_market if v is not None]
    if len(vals) < 5:
        return None, None
    rnd = random.Random(seed)
    k = len(vals)
    bs = sorted(sum(vals[rnd.randrange(k)] for _ in range(k)) / k for _ in range(n_boot))
    return bs[int(0.05 * n_boot)], bs[int(0.95 * n_boot)]


def main(argv):
    since = 0
    if "--since" in argv:
        i = argv.index("--since")
        since = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    trades, markets = argv[1], argv[2]
    label = argv[3] if len(argv) > 3 else ""
    res, tapes = load(trades, markets, since)
    tks = sorted(t for t in res if tapes.get(t))
    books = {tk: book_path(tapes[tk]) for tk in tks}
    print(f"# {label}: {len(tks)} markets with tapes")
    print("policy         start  fills  fill%   avg_q  c/ct    90%range       win%   "
          "mv10s  mv60s  mv300s  $/100mkts")
    out = []
    for policy in POLICIES:
        for S in START_S:
            fills = []
            for tk in tks:
                close, r = res[tk]
                f = simulate(tapes[tk], books[tk], close, r, S, policy)
                if f:
                    fills.append(f)
            n = len(fills)
            if not n:
                continue
            pnl = [f["pnl"] for f in fills]
            lo, hi = ci90(pnl)
            mv = {h: [f[h] for f in fills if f[h] is not None] for h in HORIZ}
            row = {"policy": policy, "start_s": S, "markets": len(tks), "fills": n,
                   "avg_q": sum(f["q"] for f in fills) / n, "c_per_ct": 100 * sum(pnl) / n,
                   "lo": 100 * lo if lo is not None else None, "hi": 100 * hi if hi is not None else None,
                   "win": 100 * sum(f["won"] for f in fills) / n,
                   **{f"mv{h}": 100 * sum(mv[h]) / len(mv[h]) if mv[h] else None for h in HORIZ},
                   "usd_per_100": 100 * sum(pnl) / len(tks)}
            out.append(row)
            rng = f"{row['lo']:+5.1f}..{row['hi']:+5.1f}" if row["lo"] is not None else "      n/a    "
            print(f"{policy:<14} {S:5d} {n:6d} {100 * n / len(tks):5.1f}% {row['avg_q']:6.3f} "
                  f"{row['c_per_ct']:+5.1f}  {rng}  {row['win']:5.1f}  "
                  + "  ".join(f"{row[f'mv{h}']:+5.1f}" if row[f"mv{h}"] is not None else "  n/a" for h in HORIZ)
                  + f"   {row['usd_per_100']:+6.2f}")
    return out


if __name__ == "__main__":
    main(sys.argv)
