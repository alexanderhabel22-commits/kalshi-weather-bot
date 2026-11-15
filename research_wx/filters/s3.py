import sys; sys.path.insert(0,sys.argv[2]); from pol import *
import random
def G(met,pos): return [r for r in R if r["metric"]==met and r["pos"]==pos]
def boot(A,B,a,b,n=2000):
    A=[r for r in A if r["filled"] and a<=r["day"]<=b]; B=[r for r in B if r["filled"] and a<=r["day"]<=b]
    days=sorted({r["day"] for r in A+B}); ia={};ib={}
    for r in A: ia.setdefault(r["day"],[]).append(r["pnl"])
    for r in B: ib.setdefault(r["day"],[]).append(r["pnl"])
    rng=random.Random(1); dc=[];dd=[]
    for _ in range(n):
        s=[rng.choice(days) for _ in days]
        pa=[x for d in s for x in ia.get(d,[])]; pb=[x for d in s for x in ib.get(d,[])]
        if not pa or not pb: continue
        dc.append(100*(sum(pb)/len(pb)-sum(pa)/len(pa))); dd.append(sum(pb)-sum(pa))
    dc.sort(); dd.sort(); q=lambda v,p:v[int(p*len(v))]
    return f"d c/ct {q(dc,.5):+.1f} [{q(dc,.025):+.1f},{q(dc,.975):+.1f}]  d$ {q(dd,.5):+.1f} [{q(dd,.025):+.1f},{q(dd,.975):+.1f}]"
C=[("lows need, spread>=8c",G("low","need"),lambda r:r["sp"]>=.08-1e-9),
   ("lows holds, spread>=8c",G("low","holds"),lambda r:r["sp"]>=.08-1e-9),
   ("lows need, spread>=6c",G("low","need"),lambda r:r["sp"]>=.06-1e-9),
   ("highs holds, wait not rising",G("high","holds"),lambda r:r["tr1"]<1),
   ("highs holds, wait tr1<1 & 14h",G("high","holds"),lambda r:r["tr1"]<1 and r["lhr"]>=14),
   ("highs need, wait not rising & gap>=3",G("high","need"),lambda r:r["tr1"]<1 and r["gap"]>=3)]
for lab,g,f in C:
    A=policy(g);B=policy(g,f)
    print(lab); 
    for nm,a,b in (("dev Oct25-Aug26",*P1),("holdout Sep-Nov26",*P2),("Oct15-Nov11",date(2026,10,15),date(2026,11,30))):
        print(f"   {nm:18s} {boot(A,B,a,b)}")
