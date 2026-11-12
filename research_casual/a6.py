import pandas as pd, numpy as np, math
L=pd.read_parquet('L.parquet')
L['mp']=1-L.q
L['bucket']=pd.cut(L.smc,[-1,.2,.5,1.01],labels=['<20%','20-50%','50%+'])
def st(d,col='mret'):
    if len(d)<30: return ''
    ge=d.groupby('event')[col].agg(['sum','size']); mu=ge['sum'].sum()/ge['size'].sum(); k=len(ge)
    se=math.sqrt(((ge['sum']-mu*ge['size'])**2).sum()*k/(k-1))/ge['size'].sum()
    return f"{mu:+.1f}±{1.96*se:.1f} ({len(d)})"
def first(d): return d.sort_values('k').groupby('ticker',sort=False).head(1)
for lo,hi in ((.5,.7),(.7,.9),(.5,.9)):
    Q=L[(L.mp>=lo)&(L.mp<hi)&(L.close_ts-L.t>3600)]
    print(f"\nFIRST fill per market, maker pays {lo}-{hi}; cols: FRONT IS | FRONT OOS | BACK IS | BACK OOS")
    for nm,G in (('ALL ex fin/crypto',Q[Q.grp!='Fin/Crypto']),('ex sports',Q[~Q.grp.isin(['Fin/Crypto','Sports'])]),('Sports',Q[Q.grp=='Sports'])):
        for b,d in G.groupby('bucket',observed=True):
            for side,dd in (('NO fav (taker buys YES)',d[d.yes]),('YES fav (taker buys NO)',d[~d.yes])):
                print(f"{nm:18s} casual {b:7s} {side:24s}",' | '.join(st(first(x)) for x in (dd[~dd.oos],dd[dd.oos],dd[~dd.oos&dd.thr],dd[dd.oos&dd.thr])))
