import sys, pandas as pd
sys.path.insert(0,'research_main')
import replay as rp
run=sys.argv[1]
m,c,t=rp.load(run,'favorites')
mk=pd.read_csv(run+'/favorites_markets.csv.gz')
print(mk.columns.tolist())
for mh in (24,):
    r=dict(rp.RULES['favorites']); r['max_h']=mh; r['per_event']=2
    d=rp.replay(m,c,t,r)
    d=d.merge(mk[['ticker','exp_ts']],on='ticker',how='left')
    d.to_pickle(sys.argv[2])
    print(d.kind.value_counts())
