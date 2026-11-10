import pickle,math,collections,statistics as st
rows=pickle.load(open('pk.pkl','rb'))
fee=lambda p: math.ceil(7*p*(100-p)/10000)
idx={(r['ym'],r['c'],r['h']):r for r in rows}
def trades(X, lag=1, hmin=1, hmax=720, onlyfav=None):
    seen=set(); out=[]
    for r in sorted(rows,key=lambda r:r['h']):
        if not (hmin<=r['hrs']<hmax): continue
        e=idx.get((r['ym'],r['c'],r['h']+lag*3600))
        if not e: continue
        day=int(r['h']//86400)
        if r['p']-r['a']>=X:
            side,px,win='Y',e['a'],r['y']
        elif r['b']-r['p']>=X:
            side,px,win='N',100-e['b'],1-r['y']
        else: continue
        if px>=99 or px<=0: continue
        k=(r['ym'],r['c'],side,day)
        if k in seen: continue
        seen.add(k); out.append((r['ym'],100*win-px-fee(px),px,side,r['c']))
    return out
for lag in [0,1,3]:
  for X in [2,3,5]:
    t=trades(X,lag)
    pm=collections.defaultdict(list)
    for x in t: pm[x[0]].append(x[1])
    tot={k:sum(v) for k,v in pm.items()}
    worst=max(tot,key=lambda k:abs(tot[k]))
    ex=[x[1] for x in t if x[0]!=worst]
    print(f"lag={lag}h gap>={X}c n={len(t)} avg={st.mean(x[1] for x in t):+.1f}c  meetings+ {sum(v>0 for v in tot.values())}/{len(tot)}  biggest={worst}({tot[worst]:+.0f}c)  without it avg={st.mean(ex) if ex else 0:+.1f}c n={len(ex)}")
print()
t=trades(2,1)
pm=collections.defaultdict(list)
for x in t: pm[x[0]].append(x)
for k in sorted(pm): print(k, len(pm[k]), round(sum(x[1] for x in pm[k])), collections.Counter((x[3],x[4]) for x in pm[k]).most_common(3), 'avg px',round(st.mean(x[2] for x in pm[k])))
# by price band of purchase
print()
for lo,hi in [(0,10),(10,40),(40,60),(60,90),(90,99)]:
    s=[x[1] for x in t if lo<=x[2]<hi]
    if s: print(f"buy px {lo}-{hi}c n={len(s)} avg={st.mean(s):+.1f}c")
