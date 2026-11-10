import pickle,math,collections,statistics as st
rows=pickle.load(open('pk.pkl','rb'))
fee=lambda p: math.ceil(7*p*(100-p)/10000)
# meeting-level stats for follow-poly
idx={(r['ym'],r['c'],r['h']):r for r in rows}
def meet(X):
    seen=set(); pm=collections.defaultdict(list)
    idx2=idx
    for r in sorted(rows,key=lambda r:r['h']):
        if not (1<=r['hrs']<720): continue
        e=idx2.get((r['ym'],r['c'],r['h']+3600))
        if not e: continue
        if r['p']-r['a']>=X: s,px,w='Y',e['a'],r['y']
        elif r['b']-r['p']>=X: s,px,w='N',100-e['b'],1-r['y']
        else: continue
        if px>=99 or px<=0: continue
        k=(r['ym'],r['c'],s,int(r['h']//86400))
        if k in seen: continue
        seen.add(k); pm[r['ym']].append(100*w-px-fee(px))
    v=[st.mean(x) for x in pm.values()]
    print('gap',X,'meeting means',[round(a) for a in v],'mean',round(st.mean(v),1),'se',round(st.stdev(v)/math.sqrt(len(v)),1))
meet(2); meet(3)
# 90-99c lead: daily at 20 UTC, side priced 90-98 at ask (maker would be at bid; use ask=taker and bid=maker approx)
for filt in ['none','poly>=ask','poly<ask']:
  for mode in ['taker','maker']:
    pm=collections.defaultdict(list)
    for r in rows:
        if r['h']%86400!=20*3600 or not (2<=r['hrs']<720): continue
        for s in 'YN':
            if s=='Y': px=r['a'] if mode=='taker' else r['b']; pp=r['p']; w=r['y']
            else: px=100-r['b'] if mode=='taker' else 100-r['a']; pp=100-r['p']; w=1-r['y']
            if not 90<=px<=98: continue
            if filt=='poly>=ask' and pp<px: continue
            if filt=='poly<ask' and pp>=px: continue
            f=fee(px) if mode=='taker' else 0
            pm[r['ym']].append(100*w-px-f)
    allv=[x for v in pm.values() for x in v]
    if allv: print(f"90-98c {mode:5} filter={filt:10} n={len(allv)} avg={st.mean(allv):+.2f}c meetings+ {sum(sum(v)>0 for v in pm.values())}/{len(pm)} losses={sum(1 for x in allv if x<0)}")
