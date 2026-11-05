"""Score the Mentions maker strategies on the out-of-sample history pull.

usage: python research_nw/mentions_oos.py RUN_DIR
Strategy A: resting NO bid at 70-98c, filled when a taker sells NO / buys YES at 2-30c.
  (same fills as "fade longshots": maker holds NO, paid 1 - yes_price)
Strategy B: any maker fill on the 70-98c side (NO or YES).
Reported per month and per series family, clustered by event.
"""
import csv
import gzip
import random
import sys
from collections import defaultdict
from pathlib import Path

RUN = Path(sys.argv[1])
mk = {r["ticker"]: r for r in csv.DictReader(gzip.open(RUN / "settled.csv.gz", "rt"))}
groups = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
for t in csv.DictReader(gzip.open(RUN / "trades_agg.csv.gz", "rt")):
    m = mk.get(t["ticker"])
    if not m:
        continue
    p, c = float(t["yes_price"]), float(t["count"])
    if t["taker_side"] == "yes":
        side, paid, won = "NO", 1 - p, m["result"] == "no"
    elif t["taker_side"] == "no":
        side, paid, won = "YES", p, m["result"] == "yes"
    else:
        continue
    if not (.70 <= paid < .99):
        continue
    pnl = c * ((1.0 if won else 0.0) - paid)
    ev = t["ticker"].rsplit("-", 1)[0]
    month = m["close"][:7]
    fam = m["series"]
    keys = [("B any side", "ALL"), ("B any side", month)]
    if side == "NO" and paid <= .98:
        keys += [("A NO 70-98", "ALL"), ("A NO 70-98", month), ("A NO by series", fam)]
        band = "70-80" if paid < .80 else "80-90" if paid < .90 else "90-98"
        keys.append(("A NO by price", band))
    for k in keys:
        g = groups[k][ev]
        g[0] += c
        g[1] += pnl
rnd = random.Random(5)
print(f"{'strategy':16} {'slice':22} {'events':>6} {'%ev+':>5} {'contracts':>10} {'c/ctr':>6} "
      f"{'lo90':>6} {'hi90':>6}")
for k in sorted(groups):
    evs = list(groups[k].values())
    n = sum(v[0] for v in evs)
    if len(evs) < 8 or n == 0:
        continue
    b = []
    for _ in range(400):
        x = [evs[rnd.randrange(len(evs))] for _ in evs]
        xn = sum(v[0] for v in x)
        b.append(sum(v[1] for v in x) / xn * 100 if xn else 0)
    b.sort()
    pos = 100 * sum(v[1] > 0 for v in evs) / len(evs)
    print(f"{k[0]:16} {k[1][:22]:22} {len(evs):6} {pos:5.0f} {int(n):10} "
          f"{sum(v[1] for v in evs) / n * 100:6.2f} {b[20]:6.2f} {b[379]:6.2f}")
