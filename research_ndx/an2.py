import json, sys, statistics as st, collections, datetime as dt
from math import sqrt
P=lambda s: dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()
def load(s, st_="between"):
    ML=[]
    for l in open(f"c_{s}.jsonl"):
        m=json.loads(l)
        if st_ and m["st"]!=st_: continue
        m["cl"]=P(m["close"]); m["y"]=1 if m["result"]=="yes" else 0; ML.append(m)
    return ML
def sim(ML,L,hmin,hmax,lo="",hi="9",fee=0.01,side="sellyes"):
    byd=collections.defaultdict(list)
    for m in ML:
        d=m["close"][:10]
        if not(lo<=d<hi): continue
        hit=False
        for ts,ph,v,b,a in m["c"]:
            h=(m["cl"]-ts)/3600
            if hmin<=h<=hmax and ph is not None and ph>L+1e-9: hit=True;break
        if hit: byd[m["event"]].append(L-m["y"]-fee)
    per=[x for v in byd.values() for x in v]; dm=[sum(v) for v in byd.values()]
    if len(dm)<3: return None
    return dict(n=len(per),ev=len(dm),hit=sum(x<0 for x in per)/len(per),pf=100*st.mean(per),
                se=100*st.pstdev(dm)/sqrt(len(dm))*len(dm)/len(per),perev=st.mean(dm),tot=sum(dm),
                months=len({k for k in byd}) )
def fmt(r): return "n/a" if not r else f"fills={r['n']:5d} ev={r['ev']:4d} hit={100*r['hit']:3.0f}% {r['pf']:+5.1f}c ±{r['se']:.1f}  $/ev {r['perev']:+.3f} tot ${r['tot']:+.2f}"
if __name__=="__main__":
    s=sys.argv[1]; wins=json.loads(sys.argv[2]); ML=load(s)
    print(s,len(ML),"bracket mkts", len({m['event'] for m in ML}),"events", min(m['close'] for m in ML)[:10], max(m['close'] for m in ML)[:10])
    for hmin,hmax in wins:
        for L in (0.20,0.25,0.30,0.35,0.40,0.50):
            print(f" h[{hmin},{hmax}] L={L:.2f}",fmt(sim(ML,L,hmin,hmax)))
    if len(sys.argv)>3:
        hmin,hmax,L=json.loads(sys.argv[3])
        for a,b in (("2025","2025-07"),("2025-07","2026-01"),("2026-01","2026-05"),("2026-05","2026-09"),("2026-09","9")):
            print(f"  {a}..{b} h[{hmin},{hmax}] L={L}",fmt(sim(ML,L,hmin,hmax,a,b)))
