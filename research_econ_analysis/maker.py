import csv, gzip, math, collections
from datetime import datetime
mk = {r["ticker"]: r for r in csv.DictReader(open("/tmp/claude-0/econ/markets.csv"))}
def mfee(p): return 0.0175*p*(1-p)  # per contract, ignoring per-order rounding
BANDS=[(0,.1),(.1,.3),(.3,.5),(.5,.7),(.7,.9),(.9,1)]
agg = collections.defaultdict(lambda: [0.0, 0.0, set(), collections.defaultdict(float)])
with gzip.open("/tmp/claude-0/econ3/trades.csv.gz","rt") as fh:
    for r in csv.DictReader(fh):
        m = mk.get(r["ticker"]); 
        if not m or not r["yes_price"]: continue
        yp = float(r["yes_price"]); n = float(r["count"] or 0)
        if r["taker_side"] == "yes": mp, win = 1-yp, m["result"]=="no"
        else: mp, win = yp, m["result"]=="yes"
        pnl = (win - mp - mfee(mp))*n
        hrs = (datetime.fromisoformat(m["close"].replace("Z","+00:00")) - datetime.fromisoformat(r["time"].replace("Z","+00:00"))).total_seconds()/3600
        hb = "<1h" if hrs<1 else "1-24h" if hrs<24 else "1-7d"
        b = next(b for b in BANDS if b[0]<=mp<b[1] or (mp==1 and b[1]==1))
        for key in ((m["series"], b), (m["series"], hb), ("ALL", b), ("ALL", hb), (m["series"],"all")):
            a = agg[key]; a[0]+=pnl; a[1]+=n; a[2].add(m["event"]); a[3][m["event"]]+=pnl
def show(k):
    p,n,ev,pe = agg[k]
    if n<2000: return ""
    pos=sum(1 for v in pe.values() if v>0)
    return f"{str(k[1]):11s} {p/n*100:+5.1f}c n={int(n):>8d} ev={len(ev)} ev+={pos}"
for s in sorted({k[0] for k in agg}):
    print(s, show((s,"all")))
    for b in BANDS+["1-7d","1-24h","<1h"]:
        x=show((s,b))
        if x: print("    ",x)
