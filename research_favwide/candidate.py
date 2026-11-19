import sys, numpy as np, pandas as pd
sys.path.insert(0,'research_main')
from analyze import est
d=pd.read_pickle(sys.argv[1]); d['cluster']=d.event
d['sched']=(d.exp_ts-d.close_ts)<2*86400
span=(d.t0.max()-d.t0.min())/86400/30.4
print('span months',round(span,1), pd.to_datetime(d.t0.min(),unit='s'), pd.to_datetime(d.t0.max(),unit='s'))
def L(lab,g):
    f=g[g.touch.notna()]; th=g[g.through.notna()]
    m,se,n=est(f); mt,st,_=est(th)
    med=f.close_ts.median(); m1,s1,_=est(f[f.close_ts<=med]); m2,s2,_=est(f[f.close_ts>med])
    r=f[f.close_ts>f.close_ts.max()-120*86400]; m9,s9,n9=est(r)
    q=f.copy(); q['q']=pd.to_datetime(q.close_ts,unit='s').dt.to_period('Q')
    qs=' '.join(f'{str(k)[2:]}:{est(v)[0]:+.0f}({len(v)})' for k,v in q.groupby('q'))
    worst=f.groupby('event').pnl.sum().sort_values().head(3)
    print(f'{lab:34s} orders {len(g)} fills {len(f)} ({len(f)/len(g):.0%}) ev {n} {len(f)/span:.1f}/mo | {m:+.1f}±{se:.1f} | thru {mt:+.1f}±{st:.1f} | h1 {m1:+.1f}±{s1:.1f} h2 {m2:+.1f}±{s2:.1f} | last120d {m9:+.1f}±{s9:.1f} n{len(r)} | won {f.win.mean():.1%}\n    quarters {qs}\n    worst events {worst.round(0).to_dict()}')
b=d[d.sched&(d.kind=='small_event')&(d.cat=='Entertainment')]
L('Ent 500k-1M (on-sched)',b[(b.ev_vol>=5e5)&(b.ev_vol<1e6)])
L('Ent 300k-1M (on-sched)',b[(b.ev_vol>=3e5)&(b.ev_vol<1e6)])
a=d[(d.kind=='small_event')&(d.cat=='Entertainment')]
L('Ent 300k-1M (all mkts)',a[(a.ev_vol>=3e5)&(a.ev_vol<1e6)])
p=d[d.sched&(d.kind=='live')]
for c,g in p.groupby('cat'): L(f'LIVE today {c} (on-sched)',g)
