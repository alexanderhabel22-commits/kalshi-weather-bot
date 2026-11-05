import sys, pickle, math, collections
sys.path.insert(0,'.')
from datetime import date
from wxbot import backtest as bt
days=pickle.load(open(sys.argv[1],'rb'))
by=collections.defaultdict(list)
for d in days: by[d.series].append(d)
def lg(p): p=min(max(p,0.005),0.995); return math.log(p/(1-p))
rows=[]
for s,L in by.items():
    for d in L:
        pr=bt.predict(L,d,'d1')
        if not pr: continue
        mu,sd=pr
        for tk,lo,hi,won,q in d.brackets:
            p=bt.bracket_prob(mu,sd,lo,hi)
            for H,(b,a) in q.items():
                if 0<b<a<1:
                    rows.append(dict(day=d.day,H=H,m=lg((a+b)/2),f=lg(p),y=1.0 if won else 0.0,a=a,b=b,city=d.city,metric=d.metric,err=abs(d.actual-mu)))
pickle.dump(rows,open(sys.argv[2],'wb'))
print('rows',len(rows))
