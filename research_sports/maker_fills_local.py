import pickle,csv,gzip,collections
rows=pickle.load(open('rows.pkl','rb'))
FEEM={'mlb':0.5}
need={r['t'] for r in rows}
cs=collections.defaultdict(list)
for r in csv.DictReader(gzip.open('candles_all.csv.gz','rt')):
    if r['yes_ask']: cs[r['ticker']].append((int(r['end_ts']),float(r['yes_ask']),float(r['yes_bid'] or 0)))
for v in cs.values(): v.sort()
OFF={'T-24h':86400,'T-60m':3600}
for hn in OFF:
  for thr in (0.03,0.05,0.08):
    for strict in (0.0,0.01):
      n=f=0;pnl=[];per=collections.defaultdict(list)
      for r in rows:
        if r['h']!=hn or r['dk']-r['bid']<thr: continue
        n+=1; t0=r['start']-OFF[hn]
        later=[x for x in cs[r['t']] if t0<x[0]<=r['start']]
        if any(a<=r['bid']-strict for _,a,_ in later):
            f+=1; p=r['y']-r['bid']-0.0175*FEEM.get(r['lg'],1)*r['bid']*(1-r['bid']); pnl.append(p); per[r['lg']].append(p)
      m=sum(pnl)/len(pnl) if pnl else 0; sd=(sum((x-m)**2 for x in pnl)/max(1,len(pnl)-1))**.5
      print(hn,thr,'strict',strict,'signals',n,'filled',f,f'pnl {m*100:+.2f}c se {sd/max(1,len(pnl))**.5*100:.2f}c', {k:(len(v),round(sum(v)/len(v)*100,1)) for k,v in per.items()})
