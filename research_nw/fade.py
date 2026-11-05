"""Fade-longshots test: a resting NO order filled when a taker buys YES under 30c.

usage: python research_nw/fade.py RUN_DIR [--random]
Per category and YES-price band: maker profit per contract (NO holder, no maker
fee), number of markets and events, share of events positive, 90% bootstrap
interval clustered by event.
"""
import csv
import gzip
import random
import sys
from collections import defaultdict
from pathlib import Path

RUN = Path(sys.argv[1])
ONLY_RANDOM = "--random" in sys.argv
BANDS = [(0.0, .05), (.05, .10), (.10, .20), (.20, .30)]

mk = {r["ticker"]: r for r in csv.DictReader(gzip.open(RUN / "settled.csv.gz", "rt"))}
S = {r["ticker"]: r for r in csv.DictReader(open(RUN / "sample.csv"))}
agg = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))  # key -> event -> [contracts, pnl]
mkts = defaultdict(set)
for t in csv.DictReader(gzip.open(RUN / "trades.csv.gz", "rt")):
    s = S.get(t["ticker"])
    if ONLY_RANDOM and s and (s["in_top"] == "1" or s["truncated"] == "1"):
        continue
    m = mk.get(t["ticker"])
    if not m or m["result"] not in ("yes", "no") or t["taker_side"] != "yes":
        continue
    p, c = float(t["yes_price"]), float(t["count"])
    band = next((f"{int(lo*100)}-{int(hi*100)}c" for lo, hi in BANDS if lo <= p < hi), None)
    if not band:
        continue
    pnl = c * ((1.0 if m["result"] == "no" else 0.0) - (1 - p))
    ev = t["ticker"].rsplit("-", 1)[0]
    for key in ((m["category"], band), (m["category"], "ALL<30"), ("ALL", band), ("ALL", "ALL<30")):
        a = agg[key][ev]
        a[0] += c
        a[1] += pnl
        mkts[key].add(t["ticker"])
rnd = random.Random(3)
print(f"{'category':24} {'band':7} {'mkts':>5} {'events':>6} {'%ev+':>5} {'contracts':>10} "
      f"{'c/ctr':>6} {'lo90':>6} {'hi90':>6}")
for key in sorted(agg):
    evs = list(agg[key].values())
    n = sum(v[0] for v in evs)
    if n == 0 or len(evs) < 5:
        continue
    boots = []
    for _ in range(400):
        x = [evs[rnd.randrange(len(evs))] for _ in evs]
        xn = sum(v[0] for v in x)
        if xn:
            boots.append(sum(v[1] for v in x) / xn * 100)
    boots.sort()
    pos = sum(v[1] > 0 for v in evs) / len(evs) * 100
    print(f"{key[0][:24]:24} {key[1]:7} {len(mkts[key]):5} {len(evs):6} {pos:5.0f} {int(n):10} "
          f"{sum(v[1] for v in evs) / n * 100:6.2f} {boots[int(.05*len(boots))]:6.2f} "
          f"{boots[int(.95*len(boots))-1]:6.2f}")
