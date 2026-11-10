import json,glob,pickle,collections,datetime as dt
R=(__import__('sys').argv[1] if len(__import__('sys').argv)>1 else 'fw/runs/20261110T2308Z/polymarket/')
m,by=pickle.load(open('fed2.pkl','rb'))
MON={x:i+1 for i,x in enumerate('january february march april may june july august september october november december'.split())}
MON.update({k[:3]:v for k,v in list(MON.items())}); MON['sept']=9
def cls(q):
    q=q.lower()
    if 'three decisions' in q or 'dissent' in q or 'another level' in q or 'cut–' in q or 'favored' in q or 'in 2025' in q or '75' in q: return None
    if 'no change' in q: return 'hold'
    if 'decrease' in q and '50' in q: return 'cut50'
    if 'decrease' in q: return 'cut25'
    if ('increase' in q or 'raise' in q) and '50' in q: return 'hike50'
    if ('increase' in q or 'raise' in q) and '25+' in q: return 'hikeany'
    if 'increase' in q: return 'hike25'
    return None
def kcls(sub):
    s=sub.lower()
    if 'maintain' in s or 'no change' in s or 'hike 0' in s: return 'hold'
    if s.startswith('cut >') : return 'cut50'
    if s.startswith('cut 25'): return 'cut25'
    if s.startswith('hike >'): return 'hike50'
    if s.startswith('hike 25'): return 'hike25'
    return None
# Kalshi decision events by (yyyy-mm)
kev={}
for t,x in m.items():
    if 'DECISION' in x['series']:
        c=kcls(x['sub'])
        if c: kev.setdefault(x['close'][:7],{}).setdefault('ev',x['event']); kev[x['close'][:7]].setdefault(c,[]).append(t)
poly=collections.defaultdict(dict)  # (ym,cls)->{hour_ts:p}
for f in glob.glob(R+'*.json'):
    if f.endswith('events.json'): continue
    j=json.load(open(f)); c=cls(j['q'])
    h=j['hist']['history']
    if not c or not h: continue
    end=j['end'][:7]
    poly[(end,c)].update({int(p['t'])//3600*3600:p['p'] for p in h})
print(sorted(set(k[0] for k in poly)))
def kmid(tk):
    d={}
    for r in by.get(tk,[]):
        if r['b'] is not None and r['a'] is not None:
            d[int(r['ts'])//3600*3600]=(r['b'],r['a'])
    return d
rows=[]
for (ym,c),ph in poly.items():
    if ym not in kev: continue
    if c=='hikeany':
        tks=kev[ym].get('hike25',[])+kev[ym].get('hike50',[])
    else: tks=kev[ym].get(c,[])
    if not tks: continue
    ks=[kmid(t) for t in tks]
    res=sum(1 for t in tks if m[t]['result']=='yes')>0
    close=dt.datetime.fromisoformat(m[tks[0]]['close'].replace('Z','+00:00')).timestamp()
    for h in sorted(ph):
        if all(h in k for k in ks):
            b=sum(k[h][0] for k in ks); a=sum(k[h][1] for k in ks) if len(ks)==1 else min(100,sum(k[h][1] for k in ks))
            rows.append(dict(ym=ym,c=c,h=h,hrs=(close-h)/3600,p=ph[h]*100,b=b,a=a,y=int(res),multi=len(ks)>1,close=close))
pickle.dump(rows,open('pk.pkl','wb'))
print(len(rows), collections.Counter(r['ym'] for r in rows))
