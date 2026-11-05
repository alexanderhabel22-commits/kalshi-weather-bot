"""Analyze probe output: who wins at each price level, per category.

usage: python research_nw/analyze.py RUN_DIR
For every trade in sampled settled markets, the taker bought YES at p or NO
at 1-p. We score the contract the taker bought (price paid, did it win) and
report average profit per contract for takers (after the standard taker fee)
and for the maker on the other side (no fee assumed), clustered by market.
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
ONLY_RANDOM = "--random" in sys.argv  # drop top-volume and truncated markets
BUCKETS = [(0, .10), (.10, .30), (.30, .50), (.50, .70), (.70, .90), (.90, 1.0)]


def fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100  # per single contract, rounded up


def bucket(p):
    for lo, hi in BUCKETS:
        if lo <= p < hi:
            return f"{int(lo*100):02d}-{int(hi*100)}c"
    return None


def main():
    series = {r["series"]: r for r in csv.DictReader(open(RUN / "series.csv"))}
    mk = {}
    with gzip.open(RUN / "settled.csv.gz", "rt") as fh:
        for r in csv.DictReader(fh):
            mk[r["ticker"]] = r
    sample = {r["ticker"]: r for r in csv.DictReader(open(RUN / "sample.csv"))}
    # per (category, bucket, ticker): [contracts, taker_pnl, maker_pnl]
    agg = defaultdict(lambda: [0.0, 0.0, 0.0])
    vol_by_cat = defaultdict(float)
    for r in mk.values():
        vol_by_cat[r["category"]] += float(r["volume"] or 0)
    with gzip.open(RUN / "trades.csv.gz", "rt") as fh:
        for t in csv.DictReader(fh):
            if ONLY_RANDOM and (sample[t["ticker"]]["in_top"] == "1"
                                or sample[t["ticker"]]["truncated"] == "1"):
                continue
            m = mk.get(t["ticker"])
            if not m or m["result"] not in ("yes", "no"):
                continue
            try:
                p, c = float(t["yes_price"]), float(t["count"])
            except (TypeError, ValueError):
                continue
            won_yes = m["result"] == "yes"
            if t["taker_side"] == "yes":
                paid, won = p, won_yes
            elif t["taker_side"] == "no":
                paid, won = 1 - p, not won_yes
            else:
                continue
            gross = (1.0 if won else 0.0) - paid
            mult = series.get(m["series"], {}).get("fee_multiplier")
            try:
                fm = float(mult) if mult not in (None, "", "None") else 1.0
            except ValueError:
                fm = 1.0
            net = gross - fee(paid) * fm
            for key in ((m["category"], "ALL"), (m["category"], bucket(paid)), ("ALL", bucket(paid)),
                        ("ALL", "ALL")):
                a = agg[(key, t["ticker"])]
                a[0] += c
                a[1] += net * c
                a[2] += -gross * c
    # roll up with market-clustered bootstrap CI
    groups = defaultdict(list)
    for (key, tk), v in agg.items():
        groups[key].append(v)
    rnd = random.Random(1)
    out = []
    for key, lst in sorted(groups.items()):
        n = sum(v[0] for v in lst)
        if n == 0:
            continue
        tk = sum(v[1] for v in lst) / n
        mm = sum(v[2] for v in lst) / n
        boots = []
        for _ in range(300):
            s = [lst[rnd.randrange(len(lst))] for _ in lst]
            sn = sum(v[0] for v in s)
            if sn:
                boots.append(sum(v[2] for v in s) / sn)
        boots.sort()
        lo, hi = boots[int(.05 * len(boots))], boots[int(.95 * len(boots)) - 1]
        out.append([key[0], key[1], len(lst), int(n), round(tk * 100, 2), round(mm * 100, 2),
                    round(lo * 100, 2), round(hi * 100, 2), int(vol_by_cat.get(key[0], 0))])
    hdr = ["category", "bucket", "markets", "contracts", "taker_net_c", "maker_c", "maker_lo90",
           "maker_hi90", "cat_settled_volume_45d"]
    with open(RUN / ("summary_random.csv" if ONLY_RANDOM else "summary.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(hdr)
        w.writerows(out)
    for row in out:
        print(*row, sep="\t")
    print("sampled markets", len(sample))


if __name__ == "__main__":
    main()
