import csv,gzip,collections,sys
L={r['series']:r for r in csv.DictReader(open('launches.csv'))}
mk={}
with gzip.open('markets.csv.gz','rt') as fh:
    for r in csv.DictReader(fh): mk[r['ticker']]=r
A=collections.defaultdict(lambda:[0.,0.])
with gzip.open('trades_agg.csv.gz','rt') as fh:
    for t in csv.DictReader(fh):
        m=mk.get(t['ticker'])
        if not m or m['result'] not in('yes','no') or m['age_days']=='':continue
        a=float(m['age_days']); p=float(t['yes_price']); c=float(t['count'])
        if t['taker_side']=='yes': paid,won=p,m['result']=='yes'
        elif t['taker_side']=='no': paid,won=1-p,m['result']=='no'
        else: continue
        if not 0<paid<1: continue
        b='d0-3' if a<3 else 'd3-7' if a<7 else 'd7-28' if a<28 else 'd56-112' if 56<=a<112 else None
        if not b: continue
        sp=L.get(m['series'],{}).get('category')=='Sports'
        for k in ['all']+(['fade'] if paid<.3 else []):
            x=A[(m['series'],b,k)]; x[0]+=c; x[1]+=(paid-won)*c
for grp in ('sports','nonsports'):
  for k in ('all','fade'):
    for b in ('d0-3','d3-7','d7-28'):
        diffs=[]
        for s in {s for s,_,_ in A}:
            if (L.get(s,{}).get('category')=='Sports')!=(grp=='sports'):continue
            e=A.get((s,b,k)); l=A.get((s,'d56-112',k))
            if e and l and e[0]>=50 and l[0]>=50: diffs.append(e[1]/e[0]-l[1]/l[0])
        if diffs:
            mu=sum(diffs)/len(diffs); sd=(sum((d-mu)**2 for d in diffs)/(len(diffs)-1))**.5
            print(grp,k,b,'minus late: %+.2fc SE %.2fc n=%d'%(mu*100,sd/len(diffs)**.5*100,len(diffs)))
    # equal-weight level per bucket
    for b in ('d0-3','d3-7','d7-28','d56-112'):
        v=[x[1]/x[0] for (s,bb,kk),x in A.items() if bb==b and kk=='all' and x[0]>=50 and (L.get(s,{}).get('category')=='Sports')==(grp=='sports')]
        print('  ',grp,b,'equal-weight maker %+.2fc n=%d'%(sum(v)/len(v)*100,len(v)))
