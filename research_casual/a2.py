import pandas as pd, numpy as np, math
o=pd.read_parquet('o2.parquet').sort_values(['ticker','t'],kind='stable').reset_index(drop=True)
o['k']=o.groupby('ticker').cumcount()
o['N']=o.groupby('ticker').k.transform('max')+1
# back-of-queue flag: a later same-side order within 1h at a higher taker price
o['thr']=False
g=o.groupby(['ticker','yes'],sort=False)
for s in range(1,31):
    qn=g.q.shift(-s); tn=g.t.shift(-s)
    o['thr']|=((qn>o.q+1e-9)&(tn-o.t<=3600)).fillna(False)
K=30
f=o[o.k<K].groupby('ticker').agg(sm=('cost',lambda x:(x<=25).mean()),med=('cost','median'),
     smc=('cost',lambda x:0.0),t0=('t','max'))
first=o[o.k<K]
f['smc']=first[first.cost<=25].groupby('ticker').n.sum().reindex(f.index).fillna(0)/first.groupby('ticker').n.sum()
o=o.merge(f,left_on='ticker',right_index=True)
L=o[(o.k>=K)&(o.N>=60)].copy()
L['mfee']=np.where(L.category.isin(['Sports']),0.25,0)*0  # ignore maker fee (flag in text)
print('markets',L.ticker.nunique(),'orders',len(L))
L['grp']=np.where(L.category=='Sports','Sports',np.where(L.category.isin(['Crypto','Commodities','Financials']),'Fin/Crypto',L.category))
L.to_parquet('L.parquet')
def meq(d,col):
    # market-equal mean of per-market order-equal mean; event-clustered CI
    pm=d.groupby(['event','ticker'])[col].mean().reset_index()
    ge=pm.groupby('event')[col].agg(['sum','size']); mu=ge['sum'].sum()/ge['size'].sum(); k=len(ge)
    se=math.sqrt(((ge['sum']-mu*ge['size'])**2).sum()*k/(k-1))/ge['size'].sum() if k>1 else float('nan')
    return f"{mu:+.1f}±{1.96*se:.1f} ({len(pm)})"
L['bucket']=pd.cut(L.smc,[-1,.05,.2,.5,1.01],labels=['<5%','5-20%','20-50%','50%+'])
for name,sub in (('FRONT of queue: maker vs every later order (order-equal per market, market-equal)',L),
                 ('BACK of queue: only orders traded through within 1h',L[L.thr])):
    print('\n'+name)
    for oos in (False,True):
        rows=[]
        for grp,d in sub[sub.oos==oos].groupby('grp'):
            if d.ticker.nunique()<150: continue
            rows.append([grp]+[meq(x,'mret') if x.ticker.nunique()>=40 else '' for _,x in d.groupby('bucket',observed=False)])
        print('OOS' if oos else 'IS'); print(pd.DataFrame(rows,columns=['group','<5%','5-20%','20-50%','50%+']).to_string(index=False))
