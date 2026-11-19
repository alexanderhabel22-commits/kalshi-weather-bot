import sys, numpy as np, pandas as pd
sys.path.insert(0,'research_main')
from analyze import est
d=pd.read_pickle(sys.argv[1]); d['cluster']=d.event
d['sched']=(d.exp_ts-d.close_ts)<2*86400
span=(d.t0.max()-d.t0.min())/86400/30.4
b=d[d.sched&(d.kind=='small_event')&(d.cat=='Entertainment')&(d.ev_vol>=3e5)&(d.ev_vol<1e6)]
b=b.sort_values(['t0','px'],ascending=[True,False]).groupby('event').head(2)
f=b[b.touch.notna()]; m,se,n=est(f); print('cap2', len(f), n, f'{len(f)/span:.1f}/mo {m:+.1f}±{se:.1f}')
f=f[~f.series.str.contains('ATTEND')]; m,se,n=est(f); print('cap2 no ATTEND', len(f), n, f'{len(f)/span:.1f}/mo {m:+.1f}±{se:.1f}')
