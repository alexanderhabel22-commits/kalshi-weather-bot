import csv, gzip, collections, sys
from datetime import datetime
S, LO, HI = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]); H=int(sys.argv[4])
mk = {r["ticker"]: r for r in csv.DictReader(open("/tmp/claude-0/econ/markets.csv")) if r["series"] == S}
cand = collections.defaultdict(list)
with gzip.open("/tmp/claude-0/econ/candles.csv.gz", "rt") as fh:
    for r in csv.DictReader(fh):
        if r["ticker"] in mk and r["bid"] and r["ask"]: cand[r["ticker"]].append((int(r["ts"]), float(r["bid"]), float(r["ask"])))
w=[];p=[]
for tk, cs in cand.items():
    cs.sort()
    if max(x[2] for x in cs) <= 0.0100001: cs=[(a,b*100,c*100) for a,b,c in cs]
    m=mk[tk]; out=m["result"]=="yes"; t=datetime.fromisoformat(m["close"].replace("Z","+00:00")).timestamp()-H*3600
    c=[x for x in cs if x[0]<=t]
    if not c or c[-1][0]<t-6*3600 or c[-1][2]-c[-1][1]>0.1: continue
    for price,win in ((c[-1][2],out),(1-c[-1][1],not out)):
        if LO<=price<HI: w.append(win); p.append(price)
print(S, len(w), "avg price", round(sum(p)/len(p),3), "win rate", round(sum(w)/len(w),3))
