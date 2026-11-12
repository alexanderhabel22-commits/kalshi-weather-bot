import pandas as pd, numpy as np, glob
R='hist/runs/'
v=pd.concat([pd.read_csv(f) for f in glob.glob(R+'*-s*/volume.csv.gz')]).drop_duplicates(['series','month'])
s=pd.concat([pd.read_csv(f) for f in glob.glob(R+'*-s*/series.csv')]).drop_duplicates('series')
v=v.merge(s[['series','category']],on='series')
p=v.pivot_table(index='month',columns='category',values='volume',aggfunc='sum').fillna(0)/1e6
pd.set_option('display.width',250)
print("Monthly contracts (millions), all series with 50k+ lifetime volume"); print(p[['Climate and Weather','Mentions','Entertainment','Sports','Crypto','Elections','Politics','Economics']].round(1).tail(24).to_string())
pm=v.pivot_table(index='month',columns='category',values='markets',aggfunc='sum').fillna(0)
print("markets"); print(pm[['Climate and Weather','Mentions','Entertainment','Sports']].tail(24).to_string())
o=pd.read_parquet('o2.parquet')
o['mon']=pd.to_datetime(o.t,unit='s').dt.to_period('M').astype(str)
for cat in ('Climate and Weather','Mentions','Entertainment'):
    d=o[(o.category==cat)&(o.mon>='2025-01')]
    g=d.groupby('mon').apply(lambda x: pd.Series({'mkts':x.ticker.nunique(),'orders/mkt':len(x)/x.ticker.nunique(),'small orders/mkt':(x.cost<=25).sum()/x.ticker.nunique(),
        'small share of contracts':x.n[x.cost<=25].sum()/x.n.sum(),'small taker ret c':x.ret[x.cost<=25].mean(),'big(100+) ret c':x.ret[x.cost>100].mean()}))
    print(cat); print(g.round(2).to_string())
