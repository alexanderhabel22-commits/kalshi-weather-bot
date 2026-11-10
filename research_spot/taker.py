"""Fair-value taker test. usage: taker.py SAMPLE TRADES SPOT PRODUCT MAXLEFT FEE(taker|maker0)"""
import sys, numpy as np, pandas as pd
from math import erf
cdf=np.vectorize(lambda x:0.5*(1+erf(x/2**.5)))
sm=pd.read_csv(sys.argv[1]); tr=pd.read_csv(sys.argv[2]); sp=pd.read_csv(sys.argv[3])
prod=sys.argv[4]; MAXLEFT=int(sys.argv[5])
sp=sp[sp['product']==prod].drop_duplicates('t').sort_values('t'); st=sp.t.values; sc=sp.close.values.astype(float); lr=np.diff(np.log(sc))
G=dict(tuple(tr.groupby('ticker'))); R=[]
for m in sm.itertuples():
    if m.ticker not in G: continue
    jo=np.searchsorted(st,m.open_ts-60,side='right')-1
    if jo<0 or st[jo]<m.open_ts-180: continue
    K=sc[jo]; yw=m.result=='yes'
    g=G[m.ticker].sort_values('t'); g=g[(m.close_ts-g.t<=MAXLEFT)&(g.t<m.close_ts)]
    g=g.assign(b=(g.t//20)).groupby('b').tail(1)   # one print per 20 s
    for r in g.itertuples():
        j=np.searchsorted(st,(r.t//60)*60-60,side='right')-1
        if j<61 or st[j]<r.t-200: continue
        sig=lr[j-60:j].std(); left=m.close_ts-r.t; tau=max(left-30,15)/60
        pm=float(cdf(np.log(sc[j]/K)/(sig*np.sqrt(tau)+1e-12)))
        # taker paid r.yes_price for yes or 1-yes_price for no -> that ask existed
        ask_side='yes' if r.taker_side=='yes' else 'no'
        ask=r.yes_price if ask_side=='yes' else 1-r.yes_price
        fair=pm if ask_side=='yes' else 1-pm
        won=yw if ask_side=='yes' else (not yw)
        R.append(dict(tk=m.ticker,left=left,ymid=r.yes_price,pm=pm,y=yw,ask=ask,fair=fair,won=won))
d=pd.DataFrame(R)
print(prod,'markets',d.tk.nunique(),'obs',len(d))
print(' Brier market(last print)',((d.ymid-d.y)**2).mean().round(4),' spot model',((d.pm-d.y)**2).mean().round(4),
      ' blend',(((d.ymid+d.pm)/2-d.y)**2).mean().round(4))
fee=0.07*d.ask*(1-d.ask)
d['pnl']=100*(d.won-d.ask-fee)
for mgn in (0.0,0.03,0.05,0.10,0.15):
    k=d[(d.fair-d.ask-fee>mgn)]
    if len(k)==0: continue
    mk=k.groupby('tk').pnl.mean()
    print(f'  buy when fair-ask-fee>{mgn:.2f}: n={len(k)} mk={k.tk.nunique()} {k.pnl.mean():+.2f}c/ct (per-market mean {mk.mean():+.2f} se {mk.std()/np.sqrt(len(mk)):.2f}) win {k.won.mean():.2f} ask {k.ask.mean():.2f}')
