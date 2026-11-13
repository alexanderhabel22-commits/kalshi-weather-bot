import pandas as pd,numpy as np,math,sys
SP=sys.argv[1]
c=pd.concat([pd.read_csv(SP+"/sp/candles.csv.gz"),pd.read_csv(SP+"/sp/c2.csv.gz")]).drop_duplicates(["ticker","kind","end_ts"])
k=pd.read_csv(SP+"/sp/kalshi.csv.gz").drop_duplicates("ticker").set_index("ticker")
end=c.groupby("ticker").end_ts.max()
rows=[]
for lead in (300,3600,4*3600):
    sub=c[c.kind==("m" if lead<=3600 else "h")].copy()
    sub["t"]=sub.ticker.map(end)-lead
    sub=sub[sub.end_ts<=sub.t]
    q=sub.sort_values("end_ts").groupby("ticker").tail(1)
    q=q[(q.t-q.end_ts)<1800]
    for _,r in q.iterrows():
        if r.ticker not in k.index: continue
        kk=k.loc[r.ticker]
        if kk.result not in ("yes","no"): continue
        a=r.yes_ask
        if not (0<r.yes_bid<a<1): continue
        y=1.0 if kk.result=="yes" else 0.0
        fee=math.ceil(0.07*a*(1-a)*100*5)/500  # 5 contracts
        rows.append(dict(lead=lead,lg=kk.league,ev=kk.event,ask=a,bid=r.yes_bid,pnl_t=y-a-fee,
                         pnl_m=y-(r.yes_bid+0.01),day=str(kk.occurrence)[:10],vol=kk.volume,mon=str(kk.occurrence)[:7]))
R=pd.DataFrame(rows)
def cl(d,col):
    m=d[col].mean(); g=d.groupby("ev")[col].sum()-m*d.groupby("ev").size()
    return m, math.sqrt((g**2).sum())/len(d)
for lead in (300,3600,4*3600):
  for lo,hi in ((0.85,0.96),(0.90,0.96),(0.75,0.85),(0.96,0.99)):
    d=R[(R.lead==lead)&(R.ask>=lo)&(R.ask<=hi)]
    if len(d)<20: continue
    m,se=cl(d,"pnl_t"); 
    print(f"lead {lead/60:4.0f}m ask {lo:.2f}-{hi:.2f} n={len(d):5d} taker {100*m:+.1f}c ±{100*se:.1f}  winrate {(d.pnl_t>0).mean():.3f} avg ask {d.ask.mean():.3f}")
    for lg in sorted(d.lg.unique()):
        e=d[d.lg==lg]
        if len(e)>=30: m,se=cl(e,"pnl_t"); print(f"      {lg:6s} n={len(e):4d} {100*m:+.1f} ±{100*se:.1f}")
d=R[(R.lead==3600)&(R.ask>=0.85)&(R.ask<=0.96)]
print(d.groupby("mon").apply(lambda e: f"{len(e)} {100*e.pnl_t.mean():+.1f}"))
