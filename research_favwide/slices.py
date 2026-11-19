import sys, numpy as np, pandas as pd
sys.path.insert(0,'research_main')
from analyze import est
d=pd.read_pickle(sys.argv[1]); d['cluster']=d.event
d['sched']=(d.exp_ts-d.close_ts)<2*86400
span=(d.t0.max()-d.t0.min())/86400/30.4
def L(lab,g):
    f=g[g.touch.notna()]; th=g[g.through.notna()]
    if len(f)==0: print(f'{lab:40s} none'); return
    m,se,n=est(f); mt,st,_=est(th)
    med=f.close_ts.median(); m1,s1,_=est(f[f.close_ts<=med]); m2,s2,_=est(f[f.close_ts>med])
    r90=f[f.close_ts>f.close_ts.max()-90*86400]; m9,s9,n9=est(r90)
    print(f'{lab:40s} fills {len(f):4d} ev {n:3d} {len(f)/span:5.1f}/mo | {m:+5.1f}±{se:4.1f} | thru {mt:+5.1f}±{st:.1f} | h1 {m1:+5.1f}±{s1:.1f} h2 {m2:+5.1f}±{s2:.1f} | 90d {m9:+5.1f}±{s9:.1f} n{len(r90)} | px {f.px.mean()*100:.1f} won {f.win.mean():.1%}')
for sch in (False,True):
    print('\n==== ', 'ON-SCHEDULE ONLY' if sch else 'ALL markets')
    D=d[d.sched] if sch else d
    L('live (today rule)',D[D.kind=='live'])
    L('skipped (3rd+ mkt in big event)',D[D.kind=='skipped'])
    s=D[D.kind=='small_event']
    for lo,hi in [(300e3,500e3),(500e3,1e6)]:
        L(f'small event {lo/1e3:.0f}k-{hi/1e3:.0f}k',s[(s.ev_vol>=lo)&(s.ev_vol<hi)])
    w=D[(D.kind=='wide')&(D.ev_vol>=1e6)]
    for lo,hi in [(.60,.70),(.70,.80),(.80,.85),(.965,.985)]:
        L(f'big event, price {lo*100:.0f}-{hi*100:.0f}',w[(w.px>=lo-1e-9)&(w.px<hi-1e-9)])
    w=D[(D.kind=='wide')&(D.ev_vol>=3e5)&(D.ev_vol<1e6)]
    for lo,hi in [(.70,.85),(.965,.985)]:
        L(f'300k-1M event, price {lo*100:.0f}-{hi*100:.0f}',w[(w.px>=lo-1e-9)&(w.px<hi-1e-9)])
    print('-- by category, live+skipped+small>=300k')
    x=D[(D.kind.isin(['live','skipped','small_event']))&(D.ev_vol>=3e5)]
    for c,g in x.groupby('cat'): L(f'  {c}',g)
