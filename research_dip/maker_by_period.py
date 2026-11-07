import sys, pickle, csv, gzip, datetime, math, random, collections
ev,tr,cn = pickle.load(open(sys.argv[1],'rb'))
W={}
for c in ('Entertainment','Elections','Politics','Economics'):
    for r in csv.DictReader(gzip.open(f'{sys.argv[2]}/20261105-{c}/sample.csv.gz','rt')): W[r['ticker']]=float(r['weight'])
P=[('2025-01','2025-07'),('2025-07','2026-01'),('2026-01','2026-07'),('2026-07','2026-11')]
P=[(x,datetime.datetime.fromisoformat(x+'-01').timestamp(),datetime.datetime.fromisoformat(y+'-01').timestamp()) for x,y in P]
for bid,cap in ((0.05,10),(0.05,1),(0.03,10)):
  rows=[]
  for e in ev:
    if e['cat']=='Mentions': continue
    side=e['side']; t0=int(e['t_dip'])+300
    for t,y,c,s in tr.get(e['ticker'],()):
        if t<t0 or s==side: continue
        p = y if side=='yes' else 1-y
        if p < bid-1e-9:
            q=min(cap,c); w=W.get(e['ticker'],1.0); win = 1.0 if e['result']==side else 0.0
            f=math.ceil(round(1.75*q*bid*(1-bid),6))/100
            rows.append((t,e['ticker'].rsplit('-',1)[0],w*(q*(win-bid)-f),w*q*bid))
            break
  for name,a,b in P:
    rs=[r for r in rows if a<=r[0]<b]
    by=collections.defaultdict(lambda:[0,0])
    for r in rs: by[r[1]][0]+=r[2]; by[r[1]][1]+=r[3]
    g=list(by.values()); rnd=random.Random(1); bs=[]
    for _ in range(300):
        s=[rnd.choice(g) for _ in g]; bs.append(sum(x[0] for x in s)/sum(x[1] for x in s))
    bs.sort(); roi=sum(x[0] for x in g)/sum(x[1] for x in g)
    print(f"bid {bid*100:.0f}c up to {cap}: {name}+6mo fills {len(rs)} weighted ROI {roi*100:+.0f}% (95% {bs[7]*100:+.0f}..{bs[292]*100:+.0f}%)")
