"""Analyse research_xv/log.py output: cross-venue arbitrage, who leads, settlement agreement.

usage: python research_xv/analyze.py quotes.csv markets.jsonl
"""
import csv
import json
import math
import sys
from collections import defaultdict


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def kfee(p):  # Kalshi taker fee per contract, rounded up to the cent
    return math.ceil(0.07 * p * (1 - p) * 100 - 1e-9) / 100


def ufee(p, theta=0.095):  # Polymarket US taker fee per contract (tier 1)
    return theta * p * (1 - p)


def main(qpath, mpath):
    rows = [r for r in csv.DictReader(open(qpath)) if r["coin"] == "btc"]
    rows.sort(key=lambda r: float(r["ts"]))
    print(f"BTC snapshots: {len(rows)}, windows: {len({r['window_end'] for r in rows})}")
    both = [r for r in rows if all(f(r[k]) is not None for k in ("k_bid", "k_ask", "us_bid", "us_ask"))]
    print(f"with Kalshi + Polymarket US quotes: {len(both)}")

    # 1) arbitrage, both legs as takers at the touch
    gaps = defaultdict(list)
    for r in both:
        kb, ka, ub, ua = (f(r[k]) for k in ("k_bid", "k_ask", "us_bid", "us_ask"))
        # A: Kalshi YES at ka + PM US Down (= sell Up at ub) costs ka + (1 - ub)
        a = ub - ka - kfee(ka) - ufee(ub)
        # B: PM US Up at ua + Kalshi NO at (1 - kb)
        b = kb - ua - ufee(ua) - kfee(kb)
        best = max(a, b)
        gaps[r["window_end"]].append((float(r["ts"]), best, a, b, kb, ka, ub, ua))
    pos = [(w, g) for w, gs in gaps.items() for g in gs if g[1] > 0]
    print(f"\nARB (taker both legs, after fees): {len(pos)} of {len(both)} snapshots positive "
          f"in {len({w for w, _ in pos})} windows")
    for th in (0.0, 0.01, 0.02, 0.03, 0.05):
        n = sum(1 for _, g in pos if g[1] > th)
        print(f"  > {th * 100:.0f}c: {n} snapshots")
    big = sorted(pos, key=lambda x: -x[1][1])[:12]
    for w, g in big:
        print(f"  win {w} t={g[0]:.0f} profit {g[1] * 100:+.1f}c  k {g[4]}/{g[5]}  us {g[6]}/{g[7]}")
    # persistence: run lengths of consecutive positive snapshots
    runs = []
    for w, gs in gaps.items():
        cur = 0
        for g in gs:
            if g[1] > 0:
                cur += 1
            elif cur:
                runs.append(cur)
                cur = 0
        if cur:
            runs.append(cur)
    if runs:
        runs.sort()
        print(f"  positive streaks: {len(runs)}, median {runs[len(runs) // 2]} snapshots, max {runs[-1]}"
              f" (one snapshot ~3 s)")

    # 2) price gaps and lead/lag (mid vs mid)
    def mid(r, a, b):
        x, y = f(r[a]), f(r[b])
        return None if x is None or y is None else (x + y) / 2

    for other, (a, b) in (("PM US", ("us_bid", "us_ask")), ("PM intl", ("intl_up_bid", "intl_up_ask"))):
        diffs, pairs_k, pairs_o = [], [], []
        by = defaultdict(list)
        for r in rows:
            by[r["window_end"]].append(r)
        for w, rs in by.items():
            for r0, r1 in zip(rs, rs[1:]):
                k0, k1 = mid(r0, "k_bid", "k_ask"), mid(r1, "k_bid", "k_ask")
                o0, o1 = mid(r0, a, b), mid(r1, a, b)
                if None in (k0, k1, o0, o1) or float(r1["ts"]) - float(r0["ts"]) > 10:
                    continue
                if not (0.05 < k0 < 0.95):
                    continue
                d = o0 - k0
                diffs.append(abs(d))
                pairs_k.append((d, k1 - k0))  # does Kalshi move toward the other venue?
                pairs_o.append((-d, o1 - o0))  # does the other move toward Kalshi?

        def beta(p):
            sx = sum(x * x for x, _ in p)
            return sum(x * y for x, y in p) / sx if sx else float("nan")
        if diffs:
            diffs.sort()
            print(f"\n{other} vs Kalshi (mids 5-95c, {len(diffs)} pairs): |gap| median "
                  f"{diffs[len(diffs) // 2] * 100:.1f}c, p90 {diffs[int(len(diffs) * .9)] * 100:.1f}c")
            print(f"  next-step catch-up: Kalshi closes {beta(pairs_k):.0%} of the gap, "
                  f"{other} closes {beta(pairs_o):.0%}  (higher = that venue follows)")

    # staleness of PM US gateway quotes
    ages = []
    import datetime as dt
    for r in both:
        s = r.get("us_sample_ts")
        if s:
            try:
                t = dt.datetime.fromisoformat(s[:26].rstrip("Z")).replace(tzinfo=dt.timezone.utc).timestamp()
                ages.append(float(r["ts"]) - t)
            except ValueError:
                pass
    if ages:
        ages.sort()
        print(f"\nPM US last-price sample age: median {ages[len(ages) // 2]:.1f}s, p90 {ages[int(len(ages) * .9)]:.1f}s")

    # 3) settlement agreement
    agree = n = same_strike = 0
    for line in open(mpath):
        m = json.loads(line)
        if m["coin"] != "btc" or not m.get("kalshi") or not m.get("us"):
            continue
        k = m["kalshi"]
        apt = m["us"].get("assetPriceTerms") or {}
        ptb = f((apt.get("priceToBeat") or {}).get("value"))
        sp = f((apt.get("settlementPrice") or {}).get("value"))
        if ptb is not None and f(k.get("floor_strike")) is not None:
            same_strike += abs(ptb - f(k["floor_strike"])) < 0.01
        if k.get("result") in ("yes", "no") and sp is not None and ptb is not None:
            n += 1
            agree += (k["result"] == "yes") == (sp >= ptb)
    print(f"\nsettlement: {same_strike} windows with identical strike; outcome agreed {agree}/{n}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
