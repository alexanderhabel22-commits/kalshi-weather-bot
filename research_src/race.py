"""How much of each repricing is left for a bot that sees the source N seconds late?

usage: python research_src/race.py RUN_DIR
Jump: pre-level (median of trades in the 10 min before) -> post-level (median of trades
10-40 min after the first trade 15c+ away from pre). |post - pre| >= 25c.
For latency buckets after that first trade (t0): contracts traded on the stale side, and
what a taker buying in the jump direction at those prices would have made at settlement
(after the 0.07*p*(1-p) taker fee). Upper bound: assumes we could take those exact fills.
"""
import bisect, csv, gzip, sys, collections, statistics as st
from datetime import datetime
from zoneinfo import ZoneInfo
ET = ZoneInfo("America/New_York")
D = sys.argv[1]
mk = {r["ticker"]: r for r in csv.DictReader(gzip.open(f"{D}/markets.csv.gz", "rt"))}
tr = collections.defaultdict(list)
for r in csv.DictReader(gzip.open(f"{D}/trades.csv.gz", "rt")):
    tr[r["ticker"]].append((int(r["ts"]), float(r["yes_price"]), float(r["count"])))
B = [(0, 3), (3, 10), (10, 30), (30, 60), (60, 300), (300, 900)]
rows = []
for tk, t in tr.items():
    m = mk.get(tk)
    if not m:
        continue
    t.sort()
    res = 1.0 if m["result"] == "yes" else 0.0
    n, j, last = len(t), 0, 0
    T = [x[0] for x in t]
    def rng(a, b):
        return t[bisect.bisect_left(T, a):bisect.bisect_left(T, b)]
    for j in range(n):
        t0 = t[j][0]
        if t0 < last:
            continue
        pre = [x[1] for x in rng(t0 - 600, t0)]
        if len(pre) < 3:
            continue
        p_pre = st.median(pre)
        if abs(t[j][1] - p_pre) < 0.15:
            continue
        post = [x[1] for x in rng(t0 + 600, t0 + 2401)]
        if len(post) < 3:
            continue
        p_post = st.median(post)
        if abs(p_post - p_pre) < 0.25 or (t[j][1] - p_pre) * (p_post - p_pre) < 0:
            continue
        up = p_post > p_pre
        out = []
        for a, b in B:
            ct = pnl = 0.0
            for x in rng(t0 + a, t0 + b):
                if True:
                    p = x[1]
                    stale = p <= p_post - 0.10 if up else p >= p_post + 0.10
                    if stale:
                        ct += x[2]
                        cost = p if up else 1 - p
                        win = res if up else 1 - res
                        pnl += x[2] * (win - cost - 0.07 * p * (1 - p))
            out += [round(ct), round(pnl, 2)]
        dt = datetime.fromtimestamp(t0, ET)
        rows.append([m["series"], tk, dt.strftime("%Y-%m-%d %H:%M:%S"), dt.strftime("%a"),
                     round(p_pre, 2), round(p_post, 2), m["result"],
                     round((int(m["close_ts"]) - t0) / 3600, 2), *out])
        last = t0 + 2400
hdr = ["series", "ticker", "t0_et", "dow", "pre", "post", "result", "h_to_close"]
for a, b in B:
    hdr += [f"ct_{a}_{b}s", f"pnl_{a}_{b}s"]
with open(f"{D}/race.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(hdr)
    w.writerows(rows)
# summary per series
agg = collections.defaultdict(lambda: [0] + [0.0] * (2 * len(B)))
for r in rows:
    a = agg[r[0]]
    a[0] += 1
    for i, v in enumerate(r[8:]):
        a[1 + i] += v
print("series jumps | per bucket: contracts / taker P&L $ (0-3s, 3-10s, 10-30s, 30-60s, 1-5m, 5-15m)")
for s, a in sorted(agg.items(), key=lambda kv: -sum(kv[1][2 + 2 * i] for i in range(1, 4))):
    print(s, a[0], " | ".join(f"{a[1+2*i]:.0f}/{a[2+2*i]:+.0f}" for i in range(len(B))))
