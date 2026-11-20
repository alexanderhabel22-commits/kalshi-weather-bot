import sys, glob, pickle, numpy as np, pandas as pd
sys.path.insert(0,'research_main')
import replay as rp
from analyze import est
S=sys.argv[1]
C,T=[],[]
for fn in sorted(glob.glob(S+'/chunks/*.pkl')):
    for cs,tr in pickle.load(open(fn,'rb')):
        C+=cs or []; T+=tr or []
c=pd.DataFrame(C,columns=["ticker","t","yb","ya","yb_lo","yb_hi","ya_lo","ya_hi","last","vol"])
t=pd.DataFrame(T,columns=["ticker","t","yes_price","count","taker_side"])
m=pd.read_csv(S+'/ent/favorites_markets.csv.gz')
old=set(pd.read_csv(S+'/d/runs/20261112-0547/favorites_markets.csv.gz').ticker)
m=m[~m.ticker.isin(old)]
for k in ("yb","ya"): c[k]=pd.to_numeric(c[k],errors='coerce')
if c.ya.max()<=0.011:
    for k in ("yb","ya","yb_lo","yb_hi","ya_lo","ya_hi","last"): c[k]=c[k]*100
c=c.sort_values(['ticker','t']); t=t.sort_values(['ticker','t'])
print('markets',len(m),'candle tickers',c.ticker.nunique(),'trades',len(t))
r=dict(rp.RULES['favorites']); r['max_h']=24; r['min_event_vol']=0
d=rp.replay(m,c,t,r); d=d.merge(m[['ticker','exp_ts']],on='ticker',how='left')
d.to_pickle(S+'/ent24.pkl')
d=d[d.kind=='live']; d['cluster']=d.event; d['sched']=(d.exp_ts-d.close_ts)<2*86400
span=22.3
for sch in (True,False):
  D=d[d.sched] if sch else d
  print('== on-schedule' if sch else '== all')
  for lo,hi in [(0,1e5),(1e5,2e5),(2e5,3e5),(3e5,1e6),(1e6,1e12)]:
    g=D[(D.ev_vol>=lo)&(D.ev_vol<hi)]; f=g[g.touch.notna()]
    if len(f)<3: print(lo,hi,'few',len(f)); continue
    mm,se,n=est(f); med=f.close_ts.median(); a,_,_=est(f[f.close_ts<=med]); b,_,_=est(f[f.close_ts>med])
    print(f'{lo/1e3:6.0f}k-{hi/1e3:6.0f}k fills {len(f):4d} ev {n:3d} {len(f)/span:5.1f}/mo {mm:+5.1f}±{se:.1f} halves {a:+.1f}/{b:+.1f} won {f.win.mean():.1%}')
