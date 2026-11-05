import json,glob,sys,csv
from datetime import datetime,timezone
sys.path.insert(0,'research')
import mm_sim
R=sys.argv[2] if len(sys.argv)>2 else 'index'
D=sys.argv[3] if len(sys.argv)>3 else 'data'
CS={'miami':'KXTEMPMIAH','nyc':'KXTEMPNYCHS','chicago':'KXTEMPCHIHS','la':'KXTEMPLAXHS'}
IDX={}
for c in CS:
  d={}
  for f in glob.glob(f'{R}/{c}/*.json'):
    for p in json.load(open(f))['timeseries']:
      if p.get('v') is not None: d[datetime.fromtimestamp(p['t']/1000,timezone.utc).strftime('%Y-%m-%d %H:%M')]=p['v']
  IDX[c]=d
  # check settlement match
  ok=n=0
  for r in csv.DictReader(open(f'{D}/mk_{CS[c]}.csv')):
    if r['value'] and r['strike_type']=='greater':
      k=r['close'][:16].replace('T',' ')
      v=mm_sim.last_at(d, datetime.fromisoformat(k),5)
      if v is not None: n+=1; ok+= abs(v-float(r['value']))<0.005
  print(c,'index minutes',len(d),'settlement match',ok,'/',n)
split=datetime(2026,10,18)
grid=[dict(h=h,Q=10,TIE=0.3,L=3) for h in (0.01,0.02,0.03,0.05)]
if len(sys.argv)>1: grid=[eval(sys.argv[1])]
for g in grid:
  for c,s in CS.items():
    r=mm_sim.run(D,s,IDX[c],split,**g)
    print(g, c, {k:(round(v,3) if isinstance(v,float) else v) for k,v in r.items()})
