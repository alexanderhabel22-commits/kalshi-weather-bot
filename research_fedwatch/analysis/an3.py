import pickle,math,collections,statistics as st
rows=pickle.load(open('pk.pkl','rb'))
fee=lambda p: math.ceil(7*p*(100-p)/10000)
idx={(r['ym'],r['c'],r['h']):r for r in rows}
def run(X,flip=False,lag=1,hold=None,fav=False):
    seen=set(); out=[]
    for r in sorted(rows,key=lambda r:r['h']):
        if not (1<=r['hrs']<720): continue
        e=idx.get((r['ym'],r['c'],r['h']+lag*3600))
        if not e: continue
        g=r['p']-r['a'] if not flip else r['b']-r['p']
        g2=r['b']-r['p'] if not flip else r['p']-r['a']
        if fav:
            mid=(r['b']+r['a'])/2
            if mid>=50 and g>=-100: s='Y'
            else: s='N'
            if abs(r['p']-mid)<X: continue
        elif g>=X: s='Y'
        elif g2>=X: s='N'
        else: continue
        px=e['a'] if s=='Y' else 100-e['b']
        if px>=99 or px<=0: continue
        k=(r['ym'],r['c'],s,int(r['h']//86400))
        if k in seen: continue
        seen.add(k)
        if hold:
            x=idx.get((r['ym'],r['c'],r['h']+(lag+hold)*3600))
            if not x or x['hrs']<0.5: continue
            sell=x['b'] if s=='Y' else 100-x['a']
            out.append((r['ym'],sell-px-fee(px)-fee(max(sell,1))))
        else:
            win=r['y'] if s=='Y' else 1-r['y']
            out.append((r['ym'],100*win-px-fee(px)))
    pm=collections.defaultdict(float)
    for a,b in out: pm[a]+=b
    return len(out), st.mean(b for a,b in out) if out else 0, f"{sum(v>0 for v in pm.values())}/{len(pm)}"
for X in [2,3]:
    print('gap',X,'follow poly',run(X),' fade poly (flip)',run(X,flip=True),' buy kalshi favorite same moments',run(X,fav=True))
for H in [6,24,72]:
    print('short hold',H,'h gap>=2',run(2,hold=H),'gap>=3',run(3,hold=H))
