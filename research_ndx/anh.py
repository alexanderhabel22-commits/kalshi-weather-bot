import json, statistics as st, collections, sys
from math import sqrt
ML=[json.loads(l) for l in open(sys.argv[1])]
def sim(L,lo="",hi="9",fee=0.01,taker_only=False):
    byd=collections.defaultdict(list)
    for m in ML:
        d=m["close"][:10]
        if not(lo<=d<hi): continue
        ys=[px for t,px,c,s in m["tr"] if (s=="yes" or not taker_only)]
        if ys and max(ys)>L+1e-9:
            byd[m["event"]].append(L-(m["result"]=="yes")-fee)
    per=[x for v in byd.values() for x in v]; dm=[sum(v) for v in byd.values()]
    if len(dm)<3: return f"L={L} n={len(per)}"
    se=100*st.pstdev(dm)/sqrt(len(dm))*len(dm)/len(per)
    return f"L={L:.2f} fills={len(per):4d} days={len(dm):3d} lose={100*sum(x<0 for x in per)/len(per):3.0f}% {100*st.mean(per):+5.1f}c ±{se:.1f} $/day {st.mean(dm):+.3f} tot ${sum(dm):+.2f}"
for L in (0.20,0.25,0.30,0.35,0.40,0.50): print("ALL",sim(L)); 
for L in (0.30,0.35): print("yes-taker only",sim(L,taker_only=True))
for L in (0.25,0.30,0.35):
    for a,b in (("2025","2025-07"),("2025-07","2026-01"),("2026-01","2026-05"),("2026-05","2026-09"),("2026-09","9")):
        print(f"  {a}..{b}",sim(L,a,b))
