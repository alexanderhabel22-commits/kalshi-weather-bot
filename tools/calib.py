import gzip, json, glob, math, sys, collections
from datetime import datetime
D=sys.argv[1]
def f(x):
    try: return float(x)
    except: return None
rows=[]
for p in glob.glob(D+'/kalshi*/*.json.gz'):
    d=json.load(gzip.open(p))
    for m in d['markets']:
        res=m.get('result'); 
        if res not in('yes','no'): continue
        c=d['candles'].get(m['ticker']) or []
        close=datetime.fromisoformat(m['close_time'].replace('Z','+00:00')).timestamp()
        for h in (36,24,12,6,3,1):
            t=close-h*3600
            # last candle ending at or before t
            cs=[x for x in c if x['end_period_ts']<=t]
            if not cs: continue
            x=cs[-1]
            ya=f(x['yes_ask'].get('close_dollars')); yb=f(x['yes_bid'].get('close_dollars'))
            if ya is None or yb is None or ya<=0 or ya>=1 or yb<=0: continue
            rows.append(dict(series=d['series'],h=h,ya=ya,yb=yb,mid=(ya+yb)/2,y=res=='yes',st=m['strike_type']))
print(len(rows))
def fee(p): return math.ceil(0.07*p*(1-p)*100)/100
for h in (24,12,6,1):
    R=[r for r in rows if r['h']==h]
    b=collections.defaultdict(list)
    for r in R: b[min(int(r['mid']*10),9)].append(r)
    print(f"--- {h}h before close, n={len(R)}")
    for k in sorted(b):
        g=b[k]; n=len(g); w=sum(r['y'] for r in g)/n; m=sum(r['mid'] for r in g)/n
        # buy yes at ask: pnl per contract
        py=sum((1 if r['y'] else 0)-r['ya']-fee(r['ya']) for r in g)/n
        pn=sum((0 if r['y'] else 1)-(1-r['yb'])-fee(1-r['yb']) for r in g)/n
        print(f"{k*10:3d}-{k*10+9}%  n={n:5d} mid={m:.3f} won={w:.3f}  buyYES={py:+.3f}  buyNO={pn:+.3f}")
