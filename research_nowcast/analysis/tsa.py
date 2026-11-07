import csv,gzip,sys,re,math,collections,datetime as dt,statistics as st
R=sys.argv[1]
D={}
for f in ('tsa_2024','tsa_2025','tsa_current'):
    t=open(f'{R}/tsa/{f}.html').read()
    for d,v in re.findall(r'<td[^>]*>\s*([0-9/]+)\s*</td>\s*<td[^>]*>\s*([0-9,]+)\s*</td>',t):
        m,dd,y=map(int,d.split('/')); D[dt.date(y,m,dd)]=int(v.replace(',',''))
print('tsa days',len(D),min(D),max(D))
ms=[m for m in csv.DictReader(gzip.open(R+'/markets.csv.gz','rt')) if m['series']=='KXTSAW']
def wkend(m): return dt.datetime.fromisoformat(m['close'].replace('Z','+00:00'))
# parse week end date from ticker KXTSAW-26NOV01-A1.80
MON={k:i+1 for i,k in enumerate('JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC'.split())}
def wend(tk):
    x=tk.split('-')[1]; return dt.date(2000+int(x[:2]),MON[x[2:5]],int(x[5:]))
print('close examples',[(m['ticker'],m['close']) for m in ms[-3:]])
def pred(we, k):
    days=[we-dt.timedelta(days=6-i) for i in range(7)]
    if any(d not in D for d in days): return None
    ly=lambda d: D.get(d-dt.timedelta(days=364))
    last=days[k-1] if k>0 else days[0]-dt.timedelta(days=1)
    ref=[last-dt.timedelta(days=i) for i in range(14)]
    if any(ly(d) is None or d not in D for d in ref): return None
    r=sum(D[d] for d in ref)/sum(ly(d) for d in ref)
    rest=[ly(d) for d in days[k:]]
    if any(x is None for x in rest): return None
    return (sum(D[d] for d in days[:k])+r*sum(rest))/7, sum(D[d] for d in days)/7
# residual sd per k (log)
res=collections.defaultdict(list)
weeks=sorted({wend(m['ticker']) for m in ms})
for we in [w for w in weeks if w.year<=2025]:
    for k in range(7):
        p=pred(we,k)
        if p: res[k].append(math.log(p[1]/p[0]))
sd={k:st.pstdev(v) for k,v in res.items()}
print('sd by days known',{k:round(v*100,2) for k,v in sd.items()}, 'n',{k:len(v) for k,v in res.items()})
cn=collections.defaultdict(list)
for r in csv.DictReader(gzip.open(R+'/candles.csv.gz','rt')):
    cn[r['ticker']].append(r)
def Phi(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def fee(p): return math.ceil(round(7*p*(1-p),6))/100
trades=[]
for m in ms:
    we=wend(m['ticker'])
    if m['strike_type']!='greater' or not m['floor']: continue
    K=float(m['floor']); win=m['result']
    done=set()
    for c in cn.get(m['ticker'],()):
        t=dt.datetime.fromtimestamp(int(c['end_ts']),dt.timezone.utc)
        # days known: TSA day d available by d+1 16:00 UTC
        k=sum(1 for i in range(7) if t>=dt.datetime.combine(we-dt.timedelta(days=6-i)+dt.timedelta(days=1),dt.time(16),dt.timezone.utc))
        if k>=7 or k in done: continue
        p=pred(we,k)
        if not p: continue
        try: yb=float(c['yes_bid'] or 0); ya=float(c['yes_ask'] or 0)
        except ValueError: continue
        if ya<=0 or ya>=1: continue
        q=1-Phi((math.log(K)-math.log(p[0]))/sd[k])
        for side,price,prob in (('yes',ya,q),('no',1-yb if yb>0 else None,1-q)):
            if price is None or not 0.03<=price<=0.97: continue
            e=prob-price-fee(price)
            if e>=0.08:
                done.add(k)
                trades.append(dict(we=we,k=k,side=side,price=price,prob=prob,win=1.0 if win==side else 0.0,pnl=(1.0 if win==side else 0.0)-price-fee(price)))
                break
print('trades',len(trades))
for y in (2025,2026):
    for kk in (range(0,3),range(3,7)):
        v=[x for x in trades if x['we'].year==y and x['k'] in kk]
        if v: print(y,'days known',list(kk),'n',len(v),'weeks',len({x['we'] for x in v}),'avg price %.2f model %.2f won %.2f pnl %+.1fc'%(st.mean(x['price'] for x in v),st.mean(x['prob'] for x in v),st.mean(x['win'] for x in v),100*st.mean(x['pnl'] for x in v)))
print('--- consistency')
ok=bad=0; ex=[]
for m in ms:
    if m['strike_type']!='greater' or not m['floor']: continue
    we=wend(m['ticker']); days=[we-dt.timedelta(days=6-i) for i in range(7)]
    if any(d not in D for d in days): continue
    avg=sum(D[d] for d in days)/7; y=avg>float(m['floor'])
    if y==(m['result']=='yes'): ok+=1
    else: bad+=1; ex.append((m['ticker'],round(avg),m['floor'],m['result']))
print('ok',ok,'bad',bad,ex[:8])
yrs=collections.Counter()
for tk,v in cn.items():
    if tk.startswith('KXTSAW'):
        for c in v: yrs[(dt.datetime.fromtimestamp(int(c['end_ts'])).year, bool(c['yes_ask']))]+=1
print(yrs)
print('--- brier at noon UTC+? per days known (one snapshot per market per k)')
B=collections.defaultdict(lambda:[0,0,0])
for m in ms:
    if m['strike_type']!='greater' or not m['floor']: continue
    we=wend(m['ticker']); K=float(m['floor']); o=1.0 if m['result']=='yes' else 0.0
    seen=set()
    for c in cn.get(m['ticker'],()):
        t=dt.datetime.fromtimestamp(int(c['end_ts']),dt.timezone.utc)
        k=sum(1 for i in range(7) if t>=dt.datetime.combine(we-dt.timedelta(days=6-i)+dt.timedelta(days=1),dt.time(16),dt.timezone.utc))
        if k>=7 or k in seen: continue
        p=pred(we,k)
        try: yb=float(c['yes_bid'] or 0); ya=float(c['yes_ask'] or 0)
        except ValueError: continue
        if not p or ya<=0 or ya>=1 or yb<=0: continue
        seen.add(k); mid=(yb+ya)/2; q=1-Phi((math.log(K)-math.log(p[0]))/sd[k])
        if 0.05<mid<0.95:
            b=B[k]; b[0]+=1; b[1]+=(mid-o)**2; b[2]+=(q-o)**2
for k in sorted(B): n,a,b=B[k]; print('days known',k,'n',n,'market brier %.3f model %.3f'%(a/n,b/n))
print('--- per k trades')
for y in (2025,2026):
  for k in range(7):
    v=[x for x in trades if x['we'].year==y and x['k']==k]
    if v: print(y,'k',k,'n',len(v),'price %.2f model %.2f won %.2f pnl %+.1fc'%(st.mean(x['price'] for x in v),st.mean(x['prob'] for x in v),st.mean(x['win'] for x in v),100*st.mean(x['pnl'] for x in v)))
