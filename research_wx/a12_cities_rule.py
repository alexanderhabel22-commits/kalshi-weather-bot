"""Live late-day rule replay on a7 records: first chance 12h..5h, spread>=4c."""
import pickle,sys,math
from collections import defaultdict
from datetime import date
import numpy as np
sys.path.insert(0,"research_wx"); import sim
rec=pickle.load(open(sys.argv[1],"rb"))
OLD={"NYC","Chicago","Miami","LA","Denver","Philadelphia","Austin"}
def rule(pos,side,hours=(12,10,9,8,7,6,5),min_sp=0.04):
    seen,out=set(),[]
    for H in hours:
        for r in rec:
            if r["H"]!=H or r["pos"]!=pos or r["side"]!=side: continue
            k=(r["tk"],side)
            if k in seen or r["sp"]<min_sp-1e-9: continue
            seen.add(k); out.append(r)
    return out
def show(T,lab):
    if len(T)<10: return f"{lab:26s} n={len(T)}"
    m,se=sim.cl([r["pnl"] for r in T],[r["day"] for r in T])
    days=len({r["day"] for r in T})
    return f"{lab:26s} {100*m:+5.1f}c ±{100*se:.1f} n={len(T):4d} days={days} ${sum(r['pnl'] for r in T):+.2f} thru {np.mean([r['thru'] for r in T]):.2f}"
for pos,side in (("need","no"),("holds","yes")):
    R=rule(pos,side)
    print(f"\n### {pos} {side}")
    for per,a,b in (("Sep 1-Nov 12",date(2026,9,1),date(2026,11,30)),("Oct 15-Nov 12",date(2026,10,15),date(2026,11,30))):
        T=[r for r in R if a<=r["day"]<=b]
        print(per); print("  "+show([r for r in T if r["city"] in OLD],"7 live cities"))
        print("  "+show([r for r in T if r["city"] not in OLD],"NEW cities"))
        print("  "+show([r for r in T if r["city"] not in OLD and r["thru"]],"NEW, trade-through only"))
        if per.startswith("Sep"):
            for c in sorted({r["city"] for r in T}):
                print("    "+show([r for r in T if r["city"]==c],c+(" *" if c in OLD else "")))
            for lab,f in (("spread 4-7c",lambda r:r["sp"]<=0.071),("spread 8c+",lambda r:r["sp"]>0.071),("adjacent",lambda r:r["gap"]<=2),("far",lambda r:r["gap"]>2)):
                print("    NEW "+show([r for r in T if r["city"] not in OLD and f(r)],lab))
            for ym in sorted({(r["day"].year,r["day"].month) for r in T}):
                print("    NEW "+show([r for r in T if r["city"] not in OLD and (r["day"].year,r["day"].month)==ym],str(ym)))
