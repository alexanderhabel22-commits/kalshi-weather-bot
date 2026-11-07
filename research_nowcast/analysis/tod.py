import csv, gzip, math, sys, collections, datetime, random
D=sys.argv[1]
res={}; cat={}
for r in csv.DictReader(gzip.open(D+'/settled.csv.gz','rt')):
    if r.get('result') in ('yes','no'): res[r['ticker']]=r['result']; cat[r['ticker']]=r.get('category','')
def fee(p): return math.ceil(round(7*p*(1-p),6))/100
acc=collections.defaultdict(list)
ET=datetime.timezone(datetime.timedelta(hours=-4))
for r in csv.DictReader(gzip.open(D+'/trades.csv.gz','rt')):
    t=r['ticker']
    if t not in res: continue
    c=cat[t]
    if c in ('Sports','Crypto'): continue
    y=float(r['yes_price']); s=r['taker_side']; p=y if s=='yes' else 1-y
    if not 0.05<=p<=0.95: continue
    d=datetime.datetime.fromisoformat(r['time'].replace('Z','+00:00')).astimezone(ET)
    win=1.0 if res[t]==s else 0.0
    slot = ('weekend' if d.weekday()>=5 else 'weekday') + ' ' + ('night 0-7' if d.hour<7 else 'day 7-18' if d.hour<18 else 'eve 18-24')
    acc[slot].append((t, win-p-fee(p), (1-win)-(1-p)))
for k in sorted(acc):
    v=acc[k]; n=len(v)
    by=collections.defaultdict(list)
    for t,a,b in v: by[t].append(b)
    g=list(by.values()); rnd=random.Random(1); bs=sorted(sum(sum(x) for x in s)/sum(len(x) for x in s) for s in ([rnd.choice(g) for _ in g] for _ in range(200)))
    print(f"{k:22s} trades {n:7d} mkts {len(g):5d} taker {100*sum(x[1] for x in v)/n:+.2f}c  maker(no fee) {100*sum(x[2] for x in v)/n:+.2f}c (95% {100*bs[5]:+.1f}..{100*bs[194]:+.1f})")
