import csv, gzip, math, sys, collections
D=sys.argv[1]
def fee(p): return math.ceil(round(7*p*(1-p),6))/100
for cat in ('Entertainment','Elections','Politics','Economics','Tech-Companies','Other'):
    res={r['ticker']:(r['result'],float(r['weight'])) for r in csv.DictReader(gzip.open(f'{D}/20261105-{cat}/sample.csv.gz','rt')) if r['result'] in('yes','no')}
    acc=collections.defaultdict(lambda:[0.0,0.0,0.0,set()])
    for r in csv.DictReader(gzip.open(f'{D}/20261105-{cat}/trades_agg.csv.gz','rt')):
        t=r['ticker']
        if t not in res: continue
        y=float(r['yes_price']); side=r['taker_side']; p=y if side=='yes' else 1-y
        if not (0.85<=p<=0.98): continue
        band='85-92' if p<0.925 else '93-98'
        k=(r['ttc'][:6],band); c=float(r['count'])*0+1  # per trade-group, unweighted by size
        win=1.0 if res[t][0]==side else 0.0
        for mode,(isk) in (('taker',True),):
            a=acc[(k,'taker')]; a[0]+=1; a[1]+=win-p-fee(p); a[2]+=win; a[3].add(t)
            # maker on the other side of this trade bought the opposite at 1-p (no fee assumed)
            b=acc[(k,'maker-longshot')]; b[0]+=1; b[1]+=(1-win)-(1-p); b[2]+=1-win; b[3].add(t)
    print('==',cat)
    for k in sorted(acc):
        if k[1]!='taker': continue
        n,pn,w,ts=acc[k]
        if n>=50: print(f"  {k[0][0]} {k[0][0:]} band {k[0][1]}: n={n:.0f} mkts={len(ts)} taker win {100*w/n:.1f}% pnl {100*pn/n:+.2f}c")
