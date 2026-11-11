import pandas as pd, numpy as np, math
L=pd.read_parquet('L.parquet')
def meq(d,col='mret'):
    pm=d.groupby(['event','ticker'])[col].mean().reset_index()
    if len(pm)<30: return ''
    ge=pm.groupby('event')[col].agg(['sum','size']); mu=ge['sum'].sum()/ge['size'].sum(); k=len(ge)
    se=math.sqrt(((ge['sum']-mu*ge['size'])**2).sum()*k/(k-1))/ge['size'].sum() if k>1 else float('nan')
    return f"{mu:+.1f}±{1.96*se:.1f} ({len(pm)})"
L['pre']=(L.close_ts-L.t)>4*3600
L['mp']=1-L.q
F=L[L.pre&(L.mp>=.5)&(L.mp<.9)]
F=F[~F.grp.isin(['Fin/Crypto'])]
L['bucket']=pd.cut(L.smc,[-1,.2,.5,1.01],labels=['<20%','20-50%','50%+']); F=F.assign(bucket=L.bucket)
print("Maker buys the 50-90c favourite from a taker, 4h+ before close, all categories except crypto/financials")
for b,d in F.groupby('bucket',observed=True):
    print(b,' | '.join(meq(x) for x in (d[~d.oos],d[d.oos],d[~d.oos&d.thr],d[d.oos&d.thr])))
print("ex-sports"); 
for b,d in F[F.grp!='Sports'].groupby('bucket',observed=True):
    print(b,' | '.join(meq(x) for x in (d[~d.oos],d[d.oos],d[~d.oos&d.thr],d[d.oos&d.thr])))
H=F[F.smc>=.5].copy()
H['half']=pd.to_datetime(H.close_ts,unit='s').dt.to_period('Q')
print("casual 50%+ by quarter: FRONT | BACK")
for h,d in H.groupby('half'): print(h, meq(d),'|',meq(d[d.thr]))
print("by taker side (taker buys YES longshot vs NO longshot): FRONT IS|OOS")
for y,d in H.groupby('yes'): print('taker buys','YES' if y else 'NO', meq(d[~d.oos]),'|',meq(d[d.oos]))
print("taker order size in these fills: FRONT")
for s,d in H.groupby('size',observed=True): print(s, meq(d[~d.oos]),'|',meq(d[d.oos]), 'share of orders %.2f'%(len(d)/len(H)))
print("by category OOS+IS pooled FRONT | BACK")
for g,d in H.groupby('grp'): print(g, meq(d),'|',meq(d[d.thr]))
# fills per market: how many qualifying orders per market (median)
c=H.groupby('ticker').size(); print('qualifying taker orders per market: median',c.median(),'mean',c.mean())
