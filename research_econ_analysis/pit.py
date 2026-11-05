import csv, gzip, collections, re
from datetime import datetime
mk = [r for r in csv.DictReader(open("/tmp/claude-0/econ/markets.csv")) if r["strike_type"]=="greater" and r["floor"]]
by = {r["ticker"]: r for r in mk}
cand = collections.defaultdict(list)
with gzip.open("/tmp/claude-0/econ/candles.csv.gz","rt") as fh:
    for r in csv.DictReader(fh):
        if r["ticker"] in by and r["bid"] and r["ask"]:
            cand[r["ticker"]].append((int(r["ts"]), float(r["bid"]), float(r["ask"])))
ev = collections.defaultdict(list)
for tk, cs in cand.items():
    cs.sort()
    if max(x[2] for x in cs) <= 0.0100001: cs=[(a,b*100,c*100) for a,b,c in cs]
    m = by[tk]; t = datetime.fromisoformat(m["close"].replace("Z","+00:00")).timestamp()-24*3600
    c=[x for x in cs if x[0]<=t]
    if not c or c[-1][0]<t-6*3600: continue
    ev[(m["series"], m["event"])].append((float(m["floor"]), (c[-1][1]+c[-1][2])/2, m["result"]=="yes"))
for S in ("KXPAYROLLS","KXU3","KXCPI","KXCPIYOY","KXJOBLESSCLAIMS","KXGDP"):
    pits=[]
    for (s,e),v in ev.items():
        if s!=S or len(v)<4: continue
        v.sort()
        # PIT ~ P(X <= actual): highest strike with result yes -> its survival prob
        yes=[x for x in v if x[2]]; no=[x for x in v if not x[2]]
        if not yes or not no: continue
        lo=yes[-1]; hi=no[0]  # actual lies between lo strike and hi strike
        pit=1-(lo[1]+hi[1])/2
        pits.append((e,round(pit,2)))
    tails=sum(1 for _,p in pits if p<0.1 or p>0.9); n=len(pits)
    print(S, n, "in outer 20% of market dist:", tails, f"({tails/max(n,1):.0%}; well-calibrated≈20%)", "| recent:", [p for e,p in sorted(pits)[-10:]])
