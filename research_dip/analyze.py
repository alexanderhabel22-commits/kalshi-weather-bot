"""Buy-the-panic-dip backtest on exact trades + 1-minute quotes.

usage: python research_dip/analyze.py <dir with runs/*/events.csv.gz ...>

A "dip" = our side traded at 56c+ and later its real ASK (from 1-minute candles)
fell to the trigger price or below. We buy at the ask `delay` minutes after the
trigger, as a taker (fee ceil(7% * p * (1-p))), only if the ask is still at or
under the cap, and hold to settlement. Fillable size = contracts other takers
actually bought at our price or better in the 30 minutes after entry (proven
liquidity; real depth may be larger).
"""
from __future__ import annotations

import bisect
import csv
import gzip
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

csv.field_size_limit(10 ** 9)


def fee(p):
    return math.ceil(round(7 * p * (1 - p), 6)) / 100  # per contract, 1-lot rounding


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load(d):
    ev, tr, cn = [], defaultdict(list), defaultdict(list)
    for run in sorted(Path(d).glob("runs/*")):
        for r in csv.DictReader(gzip.open(run / "events.csv.gz", "rt")):
            if r["t_dip"]:
                ev.append(r)
        for r in csv.DictReader(gzip.open(run / "trades.csv.gz", "rt")):
            tr[r["ticker"]].append((int(r["t"]), float(r["yes_price"]), float(r["count"]), r["taker_side"]))
        for r in csv.DictReader(gzip.open(run / "candles.csv.gz", "rt")):
            cn[r["ticker"]].append((int(r["end_ts"]), fnum(r["yes_bid"]), fnum(r["yes_ask"])))
    for v in cn.values():
        v.sort()
    for v in tr.values():
        v.sort()
    return ev, tr, cn


def side_quotes(cs, side):
    """-> times, asks, bids for our side (None = no quote)."""
    t, a, b = [], [], []
    for ts, yb, ya in cs:
        if side == "yes":
            ask = ya if ya and 0 < ya < 1 else None
            bid = yb if yb and yb > 0 else None
        else:
            ask = (1 - yb) if yb and 0 < yb < 1 else None
            bid = (1 - ya) if ya and ya < 1 else None
        t.append(ts), a.append(ask), b.append(bid)
    return t, a, b


def simulate(ev, tr, cn, trig, delay_min, cap=None):
    cap = trig if cap is None else cap
    out = []
    for e in ev:
        side, tk = e["side"], e["ticker"]
        cs = cn.get(tk)
        if not cs:
            continue
        t, a, b = side_quotes(cs, side)
        th = int(e["t_high"])
        i0 = bisect.bisect_left(t, th)
        ti = None
        for i in range(i0, len(t)):
            if a[i] is not None and a[i] <= trig + 1e-9:
                ti = i
                break
        if ti is None:
            continue
        te = t[ti] + delay_min * 60
        j = bisect.bisect_right(t, te) - 1
        if j < ti:
            j = ti
        last_trade = int(e["last_trade"] or 0)
        if te > last_trade:  # market finished trading before we could act
            continue
        p = a[j]
        if p is None or p > cap + 1e-9:
            continue
        p = round(p, 2)
        win = 1.0 if e["result"] == side else 0.0
        depth = sum(c for ts, y, c, s in tr.get(tk, ())
                    if te <= ts <= te + 1800 and s == side and (y if side == "yes" else 1 - y) <= p + 1e-9)
        out.append(dict(ticker=tk, event=tk.rsplit("-", 1)[0], cat=e["cat"], series=e["series"],
                        side=side, p=p, win=win, pnl=win - p - fee(p), depth=depth,
                        mins_after_high=(t[ti] - th) / 60, t=te))
    return out


def ci(rows, key="pnl", n=400, seed=1):
    by = defaultdict(list)
    for r in rows:
        by[r["event"]].append(r[key])
    groups = list(by.values())
    if not groups:
        return (0, 0)
    rnd = random.Random(seed)
    m = []
    for _ in range(n):
        s = [rnd.choice(groups) for _ in groups]
        tot, cnt = sum(sum(g) for g in s), sum(len(g) for g in s)
        m.append(tot / cnt)
    m.sort()
    return m[int(0.025 * n)], m[int(0.975 * n)]


def line(name, rows):
    if not rows:
        return f"{name}: none"
    n = len(rows)
    w = sum(r["win"] for r in rows) / n
    p = sum(r["p"] for r in rows) / n
    pnl = sum(r["pnl"] for r in rows) / n
    lo, hi = ci(rows)
    d10 = sum(r["depth"] >= 10 for r in rows) / n
    d100 = sum(r["depth"] >= 100 for r in rows) / n
    ev = len({r["event"] for r in rows})
    return (f"{name}: n={n} ({ev} events) avg ask {p*100:.1f}c, won {w*100:.1f}% -> "
            f"{pnl*100:+.2f}c/contract after fee (95% {lo*100:+.1f}..{hi*100:+.1f}); "
            f"proven fill >=10: {d10*100:.0f}%, >=100: {d100*100:.0f}%")


def main():
    ev, tr, cn = load(sys.argv[1])
    print(f"dip events {len(ev)}, tickers with quotes {len(cn)}")
    cats = sorted({e["cat"] for e in ev})
    for trig in (0.05, 0.10, 0.15):
        for delay in (1, 5, 30):
            rows = simulate(ev, tr, cn, trig, delay)
            print(line(f"\nask<={trig*100:.0f}c, buy {delay}m later, ALL", rows))
            for c in cats:
                print("  " + line(c, [r for r in rows if r["cat"] == c]))
    # price-band and timing detail for the main setting
    rows = simulate(ev, tr, cn, 0.10, 5)
    print("\nDetail: ask<=10c, 5m later")
    for lo, hi in ((0, .03), (.03, .06), (.06, .10)):
        print("  " + line(f"entry {lo*100:.0f}-{hi*100:.0f}c", [r for r in rows if lo < r["p"] <= hi + 1e-9]))
    for lo, hi in ((0, 30), (30, 360), (360, 1e9)):
        print("  " + line(f"dip {lo:.0f}-{hi:.0f} min after last 56c+ print",
                          [r for r in rows if lo <= r["mins_after_high"] < hi]))
    for c in cats:
        top = defaultdict(list)
        for r in rows:
            if r["cat"] == c:
                top[r["series"]].append(r)
        for s, rs in sorted(top.items(), key=lambda kv: -len(kv[1]))[:6]:
            print("  " + line(f"{c} {s}", rs))
    if len(sys.argv) > 2:
        with open(sys.argv[2], "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)


if __name__ == "__main__":
    main()
