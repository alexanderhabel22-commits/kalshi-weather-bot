import json, statistics as st, collections
from math import sqrt
seen=set(); ML=[]
for fn in ("trades.jsonl","trades2.jsonl","trades3.jsonl"):
    for l in open(fn):
        m=json.loads(l)
        if m["ticker"] in seen: continue
        seen.add(m["ticker"]); ML.append(m)
ML=[m for m in ML if m["result"] in ("yes","no") and m["strike_type"]=="between"]
def sim(L, lo="", hi="9"):
    byd=collections.defaultdict(list)
    for m in ML:
        if not (lo<=m["day"]<hi): continue
        ys=[float(px) for t,px,c,s in m["trades"] if s=="yes"]
        if ys and max(ys)>L+1e-9:
            y=1 if m["result"]=="yes" else 0
            byd[m["day"]].append(L - y - 0.01)  # sell 1 YES at L, 1c maker fee worst case
    dm=[sum(v) for v in byd.values()]  # $ per day (1 ct per bracket)
    n=sum(len(v) for v in byd.values()); hits=sum(1 for v in byd.values() for x in v if x<0)
    per=[x for v in byd.values() for x in v]
    if len(dm)<2: return f"L={L:.2f} n={n}"
    return f"L={L:.2f} fills={n} days={len(dm)} hit%={100*hits/n:.0f} per-fill {100*st.mean(per):+.1f}c  per-day ${st.mean(dm):+.3f}±{st.pstdev(dm)/sqrt(len(dm)):.3f}  total ${sum(dm):+.2f}"
for L in (0.15,0.20,0.25,0.30,0.35,0.40,0.50):
    print("ALL ", sim(L))
for L in (0.25,0.30,0.35):
    for a,b in (("2025","2026-01"),("2026-01","2026-05"),("2026-05","2026-09"),("2026-09","9")):
        print(a,b, sim(L,a,b))
