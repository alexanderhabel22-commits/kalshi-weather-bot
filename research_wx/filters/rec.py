"""All chances H=12..4 for late-day need/holds orders, with trend features."""
import pickle,sys
from datetime import date,datetime,timezone
import numpy as np
sys.path.insert(0,"research_wx"); import lib
D=lib.load(sys.argv[1]); out=[]
for d in D["days"]:
    if d["day"]<date(2025,10,1): continue
    o=D["obs"].get(d["icao"])
    if o is None: continue
    MET=d["metric"]; SG=1 if MET=="high" else -1
    for H in range(12,3,-1):
        T=lib.day_T(d,H)
        m=(o[:,0]>=d["w0"])&(o[:,0]<=T)
        if m.sum()<3 or T-o[m,0][-1]>2*3600: continue
        t=o[m,0]; v=o[m,1]
        ext=v.max() if MET=="high" else v.min(); cur=v[-1]
        def ago(h):
            j=np.searchsorted(t,T-h*3600,side="right")-1
            return v[j] if j>=0 and abs(t[j]-(T-h*3600))<5400 else np.nan
        tr1=cur-ago(1); tr3=cur-ago(3)
        k=int(np.floor(ext+0.5))
        lhr=((T-d["w0"])/3600)%24   # local standard hour
        for lo,hi,b,a,tl,th,yes,tk,later in lib.quotes(d,T):
            if not (np.isfinite(b) and np.isfinite(a)) or not (0<b<a<1): continue
            if a-b<0.04-1e-9: continue
            need=(MET=="low" and hi is not None and hi<k) or (MET=="high" and lo is not None and lo>k)
            holds=(lo is None or k>=lo) and (hi is None or k<=hi)
            if not (need or holds): continue
            gap=((k-hi) if MET=="low" else (lo-k)) if need else 0
            side,px=("no",round(1-(a-0.01),2)) if need else ("yes",round(b+0.01,2))
            if not 0.03<=px<=0.97: continue
            win=(0.0 if yes else 1.0) if side=="no" else (1.0 if yes else 0.0)
            filled=False;thru=False
            if later is not None and len(later):
                col=later[:,3] if side=="yes" else later[:,4]
                lvl=px if side=="yes" else 1-px
                hit=(col<=lvl+1e-9) if side=="yes" else (col>=lvl-1e-9)
                th_=(col<lvl-1e-9) if side=="yes" else (col>lvl+1e-9)
                hit&=np.isfinite(col); th_&=np.isfinite(col)
                filled=bool(hit.any()); thru=bool(th_.any())
            # room to edge of holds bracket (degrees until bracket breaks, in extreme direction)
            room=np.nan
            if holds:
                edge=hi if MET=="high" else lo
                room=(edge-k) if MET=="high" and edge is not None else ((k-edge) if edge is not None else 99)
            out.append(dict(tk=tk,day=d["day"],city=d["city"],series=d["series"],metric=MET,pos="need" if need else "holds",
                side=side,H=H,lhr=lhr,px=px,sp=a-b,gap=gap,room=room,ext=ext,cur=cur,togo=SG*(cur-ext),
                tr1=SG*tr1,tr3=SG*tr3,filled=filled,thru=thru,pnl=win-px,win=win))
pickle.dump(out,open(sys.argv[2],"wb")); print(len(out))
