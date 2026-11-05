import csv, gzip, math, collections, sys
from datetime import datetime
D = sys.argv[1]
mk = {r["ticker"]: r for r in csv.DictReader(open(f"{D}/markets.csv"))}
cand = collections.defaultdict(list)
with gzip.open(f"{D}/candles.csv.gz", "rt") as fh:
    for r in csv.DictReader(fh):
        if r["bid"] and r["ask"]:
            cand[r["ticker"]].append((int(r["ts"]), float(r["bid"]), float(r["ask"])))
def fee(p, m=0.07): return math.ceil(m * p * (1 - p) * 100 - 1e-9) / 100
BANDS = [(0.02,.1),(.1,.3),(.3,.7),(.7,.9),(.9,.98)]
res = collections.defaultdict(list)  # (series, h, band) -> pnl of buying the side priced in band at ask
for tk, cs in cand.items():
    cs.sort()
    if max(x[2] for x in cs) <= 0.0100001: cs = [(a, b*100, c*100) for a, b, c in cs]
    m = mk[tk]; out = 1 if m["result"] == "yes" else 0
    close = datetime.fromisoformat(m["close"].replace("Z", "+00:00")).timestamp()
    for h in (72, 24, 6):
        t = close - h*3600
        c = [x for x in cs if x[0] <= t]
        if not c or c[-1][0] < t - 6*3600: continue
        _, bid, ask = c[-1]
        if ask - bid > 0.1: continue
        for price, win in ((ask, out), (1 - bid, 1 - out)):
            for lo, hi in BANDS:
                if lo <= price < hi:
                    res[(m["series"], h, (lo, hi))].append((win - price - fee(price), m["event"]))
                    res[("ALL", h, (lo, hi))].append((win - price - fee(price), m["event"]))
ser = sorted({k[0] for k in res})
print("Taker buying at ask (after fee), cents/contract. n=contract-sides, ev=events")
for s in ser:
    line = f"{s:15s}"
    for h in (24,):
        for b in BANDS:
            v = res.get((s, h, b), [])
            if len(v) >= 15:
                line += f" {b[0]:.2f}-{b[1]:.2f}: {sum(x for x,_ in v)/len(v)*100:+5.1f}c (n={len(v)},ev={len({e for _,e in v})})"
    print(line)
for h in (72, 24, 6):
    print("ALL h=",h, " ".join(f"{b}: {sum(x for x,_ in res[('ALL',h,b)])/max(1,len(res[('ALL',h,b)]))*100:+.1f}c n={len(res[('ALL',h,b)])}" for b in BANDS))
