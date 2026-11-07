import csv, gzip, collections, math, sys
D=sys.argv[1]
M={r['ticker']:r for r in csv.DictReader(open(D+'/markets.csv'))}
q=collections.defaultdict(dict)  # (event, ts) -> ticker -> (bid, ask)
for r in csv.DictReader(gzip.open(D+'/candles.csv.gz','rt')):
    m=M.get(r['ticker'])
    if not m or m['strike_type']!='greater' or not m['floor']: continue
    b=float(r['bid'] or 0); a=float(r['ask'] or 0)
    q[(m['event'],int(r['ts']))][r['ticker']]=(float(m['floor']),b,a)
def fee(p): return math.ceil(round(7*p*(1-p),6))/100
viol=0; tot=0; prof=[]; evs=set()
for (ev,ts),d in q.items():
    xs=sorted(d.values())
    for i in range(len(xs)):
        for j in range(i+1,len(xs)):
            klo,blo,alo=xs[i]; khi,bhi,ahi=xs[j]
            if alo<=0 or alo>=1 or bhi<=0: continue
            tot+=1
            # buy YES on lower strike at ask, sell YES on higher strike at bid (= buy NO higher at 1-bhi): pays >=1 always
            cost = alo + (1-bhi) + fee(alo) + fee(1-bhi)
            if cost < 1: viol+=1; prof.append(1-cost); evs.add(ev)
print('pairs checked',tot,'arb pairs after fees',viol,'events',len(evs), 'avg profit c', round(100*sum(prof)/max(1,len(prof)),2))
print(sorted(evs)[:20])
