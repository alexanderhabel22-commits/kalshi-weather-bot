import pandas as pd, numpy as np, math
L=pd.read_parquet('L.parquet')
m=pd.read_parquet('markets.parquet')[['ticker','series','open_ts','exp_ts','occ_ts']]
L=L.merge(m,on='ticker')
def meq(d,col='mret'):
    pm=d.groupby(['event','ticker'])[col].mean().reset_index()
    if len(pm)<30: return ''
    ge=pm.groupby('event')[col].agg(['sum','size']); mu=ge['sum'].sum()/ge['size'].sum(); k=len(ge)
    se=math.sqrt(((ge['sum']-mu*ge['size'])**2).sum()*k/(k-1))/ge['size'].sum() if k>1 else float('nan')
    return f"{mu:+.1f}±{1.96*se:.1f} ({len(pm)})"
L['ttc']=L.close_ts-L.t
L['phase']=np.where(L.ttc>4*3600,'pre (4h+ before close)','late (<4h)')
L['bucket']=pd.cut(L.smc,[-1,.2,.5,1.01],labels=['<20%','20-50%','50%+'])
L['mp']=pd.cut(1-L.q,[0,.3,.5,.7,.9,1],labels=['0-30','30-50','50-70','70-90','90-100'])
for grp in ('Sports','Mentions','Entertainment','Climate and Weather','Elections','Politics'):
    print('\n==',grp,' maker c/ct market-equal: FRONT IS | FRONT OOS | BACK IS | BACK OOS')
    G=L[L.grp==grp]
    for (ph,b),d in G.groupby(['phase','bucket'],observed=True):
        print(f"{ph:24s} casual {b:7s}", ' | '.join(meq(x) for x in (d[~d.oos],d[d.oos],d[~d.oos&d.thr],d[d.oos&d.thr])))
    d0=G[(G.smc>=0.5)&(G.phase.str.startswith('pre'))]
    for b,d in d0.groupby('mp',observed=True):
        print(f"   pre, casual 50%+, maker pays {b:7s}", ' | '.join(meq(x) for x in (d[~d.oos],d[d.oos],d[~d.oos&d.thr],d[d.oos&d.thr])))
