"""Analyze the launch backtest: is making markets more profitable early in a series' life?

usage: python research_launch/analyze.py RUN_DIR

For every trade the taker bought YES at p or NO at 1-p; the resting maker took
the other side. Maker profit per contract (no maker fee) = price taker paid -
(1 if the taker's side won). Grouped by the market's age since its series'
first market: 0-14d, 14-28d, 28-56d, 56-112d.
"Fade" = only trades where the taker bought a longshot (paid < 30c), so the
maker holds the 70c+ side (the longshot-fade strategy).
"""
from __future__ import annotations

import csv
import gzip
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

RUN = Path(sys.argv[1])
BUCKETS = [(0, 14), (14, 28), (28, 56), (56, 112)]


def fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100


def bname(age):
    for lo, hi in BUCKETS:
        if lo <= age < hi:
            return f"{lo:02d}-{hi}d"
    return None


def boot(cells, n=400, seed=3):
    """cells: list of (contracts, pnl) per series. Series-clustered bootstrap CI of pnl/contract."""
    if not cells:
        return None, None, None
    tot_c = sum(c for c, _ in cells)
    est = sum(p for _, p in cells) / tot_c if tot_c else None
    rnd = random.Random(seed)
    vals = []
    for _ in range(n):
        s = [cells[rnd.randrange(len(cells))] for _ in cells]
        c = sum(x for x, _ in s)
        if c:
            vals.append(sum(p for _, p in s) / c)
    vals.sort()
    return est, vals[int(.025 * len(vals))], vals[int(.975 * len(vals)) - 1]


def main():
    launches = {r["series"]: r for r in csv.DictReader(open(RUN / "launches.csv"))}
    mk = {}
    with gzip.open(RUN / "markets.csv.gz", "rt") as fh:
        for r in csv.DictReader(fh):
            mk[r["ticker"]] = r
    # (series, bucket, kind) -> [contracts, maker_pnl, taker_net_pnl]
    agg = defaultdict(lambda: [0.0, 0.0, 0.0])
    mkts = defaultdict(set)
    with gzip.open(RUN / "trades_agg.csv.gz", "rt") as fh:
        for t in csv.DictReader(fh):
            m = mk.get(t["ticker"])
            if not m or m["result"] not in ("yes", "no") or m["age_days"] == "":
                continue
            b = bname(float(m["age_days"]))
            if not b:
                continue
            p, c = float(t["yes_price"]), float(t["count"])
            if t["taker_side"] == "yes":
                paid, won = p, m["result"] == "yes"
            elif t["taker_side"] == "no":
                paid, won = 1 - p, m["result"] == "no"
            else:
                continue
            if not 0 < paid < 1:
                continue
            maker = paid - (1.0 if won else 0.0)
            taker = -maker - fee(paid)
            kinds = ["all"] + (["fade"] if paid < 0.30 else []) + (["fav"] if paid > 0.70 else [])
            for k in kinds:
                a = agg[(m["series"], b, k)]
                a[0] += c
                a[1] += maker * c
                a[2] += taker * c
            mkts[(m["series"], b)].add(t["ticker"])

    out = []
    pr = out.append
    series = sorted({s for s, _, _ in agg})
    pr(f"Series with trade data: {len(series)}; markets sampled: {sum(len(v) for v in mkts.values())}")
    cats = defaultdict(int)
    for s in series:
        cats[launches.get(s, {}).get("category", "?")] += 1
    pr("By category: " + ", ".join(f"{k} {v}" for k, v in sorted(cats.items(), key=lambda x: -x[1])))
    pr("")
    for kind in ("all", "fade", "fav"):
        pr(f"== maker profit, {kind} trades (cents/contract, series-clustered 95% CI) ==")
        for lo, hi in BUCKETS:
            b = f"{lo:02d}-{hi}d"
            cells = [(agg[(s, b, kind)][0], agg[(s, b, kind)][1]) for s in series
                     if agg.get((s, b, kind), [0])[0] > 0]
            est, l, h = boot(cells)
            if est is None:
                continue
            tk = sum(agg[(s, b, kind)][2] for s in series if (s, b, kind) in agg) / \
                sum(c for c, _ in cells)
            pr(f"  {b}: maker {est * 100:+.2f}c [{l * 100:+.2f}, {h * 100:+.2f}]  taker-after-fee "
               f"{tk * 100:+.2f}c  series {len(cells)}  contracts {sum(c for c, _ in cells):,.0f}")
        # paired: same series, early (0-28) vs late (56-112), equal weight per series
        diffs = []
        for s in series:
            e = [agg.get((s, b, kind)) for b in ("00-14d", "14-28d")]
            e = [x for x in e if x]
            late = agg.get((s, "56-112d", kind))
            ec = sum(x[0] for x in e)
            if ec >= 50 and late and late[0] >= 50:
                diffs.append(sum(x[1] for x in e) / ec - late[1] / late[0])
        if diffs:
            mu = sum(diffs) / len(diffs)
            sd = (sum((d - mu) ** 2 for d in diffs) / max(1, len(diffs) - 1)) ** .5
            pos = sum(d > 0 for d in diffs)
            pr(f"  paired early(0-28d) minus late(56-112d): {mu * 100:+.2f}c/contract per series "
               f"(SE {sd / len(diffs) ** .5 * 100:.2f}c), {pos}/{len(diffs)} series better early")
        pr("")

    # per category, all trades, early vs late
    pr("== by category: maker c/contract early (0-28d) vs late (56-112d), all trades ==")
    bycat = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, set()])
    for (s, b, k), a in agg.items():
        if k != "all":
            continue
        cat = launches.get(s, {}).get("category", "?")
        x = bycat[cat]
        if b in ("00-14d", "14-28d"):
            x[0] += a[0]
            x[1] += a[1]
        elif b == "56-112d":
            x[2] += a[0]
            x[3] += a[1]
        x[4].add(s)
    for cat, x in sorted(bycat.items(), key=lambda kv: -len(kv[1][4])):
        e = f"{x[1] / x[0] * 100:+.2f}c ({x[0]:,.0f})" if x[0] else "-"
        lt = f"{x[3] / x[2] * 100:+.2f}c ({x[2]:,.0f})" if x[2] else "-"
        pr(f"  {cat:24s} series {len(x[4]):4d}  early {e:22s} late {lt}")
    pr("")

    # the 2026 launch calendar
    pr("== 2026 recurring-series launches by month (first market opened) ==")
    cal = defaultdict(lambda: defaultdict(int))
    for r in launches.values():
        fo = r["first_open"]
        if fo.startswith("2026") and r["capped"] == "0":
            cal[fo[:7]][r["category"]] += 1
    for mth in sorted(cal):
        tot = sum(cal[mth].values())
        top = ", ".join(f"{k} {v}" for k, v in sorted(cal[mth].items(), key=lambda kv: -kv[1])[:4])
        pr(f"  {mth}: {tot:4d}  ({top})")

    # top individual series by early maker profit (for leads)
    pr("")
    pr("== series with the most early maker profit ($ per 1 contract on every trade) ==")
    rows = []
    for s in series:
        e = [agg.get((s, b, "all")) for b in ("00-14d", "14-28d")]
        e = [x for x in e if x]
        if not e:
            continue
        c, p = sum(x[0] for x in e), sum(x[1] for x in e)
        late = agg.get((s, "56-112d", "all"))
        rows.append((p, c, s, late))
    rows.sort(reverse=True)
    for item in rows[:12]:
        p, c, s, late = item
        lt = f"late {late[1] / late[0] * 100:+.1f}c" if late and late[0] else "late -"
        L = launches.get(s, {})
        pr(f"  {s:22s} {L.get('category', '?'):18s} {L.get('frequency', ''):10s} launched "
           f"{L.get('first_open', '')[:10]} early {p / c * 100:+.1f}c on {c:,.0f}  {lt}")
    text = "\n".join(out)
    print(text)
    (RUN / "analysis.txt").write_text(text)


if __name__ == "__main__":
    main()
