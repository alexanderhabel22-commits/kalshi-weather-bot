# Event-size split of the fills (run analyze.load first and pickle; see README in the report).
import pickle, collections, random
fills=pickle.load(open('/tmp/claude-0/-home-claude-kalshi-weather-bot/8ccbbcfe-ef8e-500d-98ed-87d5453690f1/scratchpad/fills.pkl','rb'))
evvol=collections.Counter()
for r in fills:
    if r['strat'] in ('fade_any','fav_tkr'): evvol[r['event']]+=r['c']
rnd=random.Random(3)
def stats(rows):
    ev=collections.defaultdict(lambda:[0,0])
    for r in rows: ev[r['event']][0]+=r['c']; ev[r['event']][1]+=r['pnl']
    v=[x for x in ev.values() if x[0]>0]
    if len(v)<5: return None
    C=sum(x[0] for x in v); P=sum(x[1] for x in v)
    eq=[x[1]/x[0] for x in v]
    bs=sorted(sum(rnd.choice(eq) for _ in eq)/len(eq)*100 for _ in range(300))
    return len(v),P/C*100,sum(eq)/len(eq)*100,bs[15],bs[284],sum(x[1]>0 for x in v)/len(v)
def show(lbl,rows):
    s=stats(rows)
    if s: print(f"  {lbl:28s} n={s[0]:4d} vw={s[1]:+5.1f} eqw={s[2]:+5.1f} eqwCI=[{s[3]:+.1f},{s[4]:+.1f}] up={s[5]:.0%}")
for th in [3e5,1e6]:
  for strat in ['fade','fav_mkr','fav_tkr']:
    rows=[r for r in fills if r['strat']==strat and r['through'] and evvol[r['event']]>=th]
    print(f"== {strat} events>={th:.0e}")
    show('ALL',rows)
    for k in ['cat','band','ttc']:
        g=collections.defaultdict(list)
        for r in rows: g[r[k]].append(r)
        for kk in sorted(g): show(f"{k}={kk}",g[kk])
    if th==1e6:
        g=collections.defaultdict(list)
        for r in rows: g[r['month'][:4]+('H1' if r['month'][5:]<'07' else 'H2')].append(r)
        for kk in sorted(g): show(f"half={kk}",g[kk])
