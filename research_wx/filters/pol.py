import pickle,sys
from datetime import date
import numpy as np
sys.path.insert(0,"research_wx"); import sim
R=pickle.load(open(sys.argv[1],"rb"))
P1=(date(2025,10,1),date(2026,8,31)); P2=(date(2026,9,1),date(2026,12,31))
def policy(grp,cond=lambda r:True,Hs=range(12,4,-1)):
    by={}
    for r in grp:
        if r["H"] in Hs: by.setdefault(r["tk"],[]).append(r)
    out=[]
    for tk,L in by.items():
        L.sort(key=lambda r:-r["H"])
        for r in L:      # live: first eligible scan; cond can skip (then later scans may place)
            if cond(r): out.append(r); break
    return out
def first_only(grp,cond):
    base=policy(grp); return [r for r in base if cond(r)]
def st(T):
    F=[r for r in T if r["filled"]]
    if len(F)<10: return f"n={len(F):4d}            "
    m,se=sim.cl([r["pnl"] for r in F],[r["day"] for r in F])
    return f"{100*m:+5.1f}c±{100*se:3.1f} n={len(F):4d} ${sum(r['pnl'] for r in F):+7.2f}"
def row(lab,T):
    return f"{lab:38s} "+" | ".join(st([r for r in T if a<=r['day']<=b]) for a,b in (P1,P2))
