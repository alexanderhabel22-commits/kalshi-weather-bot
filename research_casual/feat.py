import pandas as pd, numpy as np
m=pd.read_parquet('markets.parquet'); t=pd.read_parquet('trades.parquet')
t['yes']=(t.taker_side=='yes')
t['q']=np.where(t.yes,t.yes_price,1-t.yes_price)
t['cost']=t['count']*t.q
t['frac']=(np.abs(t['count']-np.round(t['count']))>1e-6)
# orders
o=t.groupby(['ticker','t','yes'],sort=False).agg(n=('count','sum'),cost=('cost','sum'),frac=('frac','max')).reset_index()
o['q']=o.cost/o.n
o.to_parquet('orders.parquet')
print(len(o))
print(o.cost.describe(percentiles=[.1,.25,.5,.75,.9,.99]))
o=o.merge(m[['ticker','category','close_ts']],on='ticker')
o['mon']=pd.to_datetime(o.t,unit='s').dt.to_period('M')
print(o.groupby('mon').agg(orders=('n','size'),frac=('frac','mean'),med=('cost','median'),small=('cost',lambda x:(x<=10).mean())))
for thr in (5,10,25):
    o['s']=o.cost<=thr
    g=o.groupby('category').apply(lambda d: pd.Series({'orders':len(d),'share_orders':d.s.mean(),'share_contracts':d.n[d.s].sum()/d.n.sum(),'frac':d.frac.mean(),'med$':d.cost.median()}))
    print(thr); print(g.round(3))
