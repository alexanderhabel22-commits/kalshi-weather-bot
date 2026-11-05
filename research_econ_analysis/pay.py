import csv, gzip, math, collections, sys
from datetime import datetime
D = sys.argv[1]; S = sys.argv[2]; LO, HI = float(sys.argv[3]), float(sys.argv[4])
mk = {r["ticker"]: r for r in csv.DictReader(open(f"{D}/markets.csv")) if r["series"] == S}
cand = collections.defaultdict(list)
with gzip.open(f"{D}/candles.csv.gz", "rt") as fh:
    for r in csv.DictReader(fh):
        if r["ticker"] in mk and r["bid"] and r["ask"]:
            cand[r["ticker"]].append((int(r["ts"]), float(r["bid"]), float(r["ask"])))
def fee(p): return math.ceil(0.07*p*(1-p)*100-1e-9)/100
for h in (72, 24, 6, 1):
    ev = collections.defaultdict(list)
    for tk, cs in cand.items():
        cs.sort()
        if max(x[2] for x in cs) <= 0.0100001: cs = [(a, b*100, c*100) for a, b, c in cs]
        m = mk[tk]; out = 1 if m["result"] == "yes" else 0
        t = datetime.fromisoformat(m["close"].replace("Z", "+00:00")).timestamp() - h*3600
        c = [x for x in cs if x[0] <= t]
        if not c or c[-1][0] < t-6*3600: continue
        _, bid, ask = c[-1]
        if ask-bid > 0.1: continue
        for price, win, side in ((ask, out, "Y"), (1-bid, 1-out, "N")):
            if LO <= price < HI: ev[m["event"]].append((win-price-fee(price), side, m["floor"], price))
    tot = [x[0] for v in ev.values() for x in v]
    per = sorted((k, sum(x[0] for x in v)) for k, v in ev.items())
    pos = sum(1 for _, s in per if s > 0)
    print(f"h={h} sides={len(tot)} events={len(per)} avg={sum(tot)/max(1,len(tot))*100:+.1f}c events+={pos}")
    yr = collections.defaultdict(list)
    for k, v in ev.items(): yr[k.split("-")[1][:2]] += [x[0] for x in v]
    print("   by year:", {y: (len(v), round(sum(v)/len(v)*100,1)) for y, v in sorted(yr.items())})
    if h == 99:
        for k, s in per: print("  ", k, f"{s:+.2f}", [(x[1], x[2], round(x[3],2)) for x in ev[k]])
