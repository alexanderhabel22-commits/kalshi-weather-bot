import sys, pickle, math
sys.path.insert(0,'.')
from datetime import date
from wxbot import backtest as bt
rows=pickle.load(open(sys.argv[1],'rb'))
CUT=date(2026,9,5)
def sig(z): return 1/(1+math.exp(-max(min(z,30),-30)))
def fit(R):
    w=[0.0,1.0,0.0]
    for _ in range(25):
        g=[0,0,0]; H=[[0]*3 for _ in range(3)]
        for r in R:
            x=(1,r['m'],r['f']); p=sig(w[0]+w[1]*x[1]+w[2]*x[2])
            for i in range(3):
                g[i]+=(r['y']-p)*x[i]
                for j in range(3): H[i][j]-=p*(1-p)*x[i]*x[j]
        A=[row[:] for row in H]; b=[-v for v in g]
        for i in range(3):
            for j in range(i+1,3):
                f=A[j][i]/A[i][i]
                for k in range(i,3): A[j][k]-=f*A[i][k]
                b[j]-=f*b[i]
        dx=[0]*3
        for i in (2,1,0): dx[i]=(b[i]-sum(A[i][k]*dx[k] for k in range(i+1,3)))/A[i][i]
        w=[a+c for a,c in zip(w,dx)]
    se=[math.sqrt(-1/H[i][i]) for i in range(3)]
    return w,se
def sim(R,w,thr,lo=0.03,hi=0.97):
    n=wins=0;pnl=st=0.0
    for r in R:
        p=sig(w[0]+w[1]*r['m']+w[2]*r['f'])
        for prob,price,win in ((p,r['a'],r['y']==1),(1-p,1-r['b'],r['y']==0)):
            if lo<=price<=hi:
                f=bt.fee(price)
                if prob-price-f>=thr:
                    n+=1;wins+=win;pnl+=(1 if win else 0)-price-f;st+=price+f
    return n,wins,round(pnl,2),round(pnl/st*100,1) if st else 0
for H in (36,24,12):
    R=[r for r in rows if r['H']==H]
    tr=[r for r in R if r['day']<CUT]; te=[r for r in R if r['day']>=CUT]
    w,se=fit(tr)
    bm=sum((sig(r['m'])-r['y'])**2 for r in te)/len(te)
    bb=sum((sig(w[0]+w[1]*r['m']+w[2]*r['f'])-r['y'])**2 for r in te)/len(te)
    bf=sum((sig(r['f'])-r['y'])**2 for r in te)/len(te)
    print(f"H={H} train={len(tr)} test={len(te)} coef const/mkt/model={[round(x,3) for x in w]} se={[round(x,3) for x in se]}")
    print(f"   test Brier: market {bm:.4f}  model-alone {bf:.4f}  blend {bb:.4f}")
    for thr in (0.02,0.04,0.06,0.10):
        print('   thr',thr,'trades/wins/pnl/roi%',sim(te,w,thr), ' (train',sim(tr,w,thr),')')

print("=== walk-forward by month (fit on all earlier months) ===")
import collections
for H in (36,24):
    R=[r for r in rows if r['H']==H]
    months=sorted(set((r['day'].year,r['day'].month) for r in R))
    for ym in months[3:]:
        tr=[r for r in R if (r['day'].year,r['day'].month)<ym]
        te=[r for r in R if (r['day'].year,r['day'].month)==ym]
        w,_=fit(tr)
        bm=sum((sig(r['m'])-r['y'])**2 for r in te)/len(te)
        bb=sum((sig(w[0]+w[1]*r['m']+w[2]*r['f'])-r['y'])**2 for r in te)/len(te)
        print(H, ym, 'n',len(te),'model coef',round(w[2],2),'brier mkt',round(bm,4),'blend',round(bb,4),'thr.06',sim(te,w,0.06))
