"""Extra slices for the GO candidates. usage: python research_fin/extra_checks.py RUN_DIR ..."""
import sys,random,collections
RUNS = sys.argv[1:]
sys.path.insert(0, __import__('os').path.dirname(__file__))
import analyze as A
_,_,F=A.load(RUNS)
rnd=random.Random(1)
C=['Crypto 15-min up/down','Central banks','Inflation','Gas/diesel prices weekly/monthly','Index hourly above-below','Index daily range','Crypto max/min/yearly','Econ other','Jobs','GDP/recession']
def per(rows,keyf):
    g=collections.defaultdict(list)
    for r in rows: g[keyf(r)].append(r)
    for k in sorted(g):
        s=A.summarize(g[k],rnd,200)
        if s and s['events']>=8: print(f"  {k}: ev {s['events']} up {s['pos']:.0f}% {s['cpc']:+.2f} [{s['lo']:+.1f},{s['hi']:+.1f}]")
for model in ('all','through'):
    print('=== ',model)
    rows=[r for r in F if r['sub'] in C and (model=='all' or r['through'])]
    print('by half-year'); per(rows,lambda r:(r['sub'],r['month'][:4]+('H1' if r['month'][5:]<'07' else 'H2')))
print('#### band x model for GO candidates')
G=['Crypto 15-min up/down','Central banks','Inflation']
for model in ('all','through'):
    rows=[r for r in F if r['sub'] in G and (model=='all' or r['through']) and r['month']>='2026-01']
    print('==',model,'2026 only'); per(rows,lambda r:(r['sub'],r['band']))
rows=[r for r in F if r['sub']=='Crypto 15-min up/down' and r['through']]
print('crypto15 by series (through)'); per(rows,lambda r:r['series'])
rows=[r for r in F if r['sub'] in ('Central banks','Inflation') and r['through']]
print('cb/infl by series (through)'); per(rows,lambda r:r['series'])
print('cb/infl by ttc (through)'); per(rows,lambda r:(r['sub'],r['ttc']))
