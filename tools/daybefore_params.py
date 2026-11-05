"""Build data/daybefore_params.json for the paper day-before maker strategy.

usage: python tools/daybefore_params.py days.pkl rows.pkl [out.json]
(days.pkl from tools/build.py, rows.pkl from tools/full.py)

Per series: model weights, biases and error sd fit on the trailing 45 days
(same fit as the walk-forward backtest). Blend: logistic
sig(c0 + c1*logit(market mid) + c2*logit(model p)) fit on the last 6 months of
quotes 36 hours before close."""
import json, math, pickle, sys
from collections import defaultdict
sys.path.insert(0, '.')
from wxbot import backtest as bt

days = pickle.load(open(sys.argv[1], 'rb'))
rows = pickle.load(open(sys.argv[2], 'rb'))
out = sys.argv[3] if len(sys.argv) > 3 else 'data/daybefore_params.json'
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

by = defaultdict(list)
for d in days:
    by[d.series].append(d)
last = max(d.day for d in days)
series = {}
for s, L in by.items():
    past = [d for d in L if (last - d.day).days < 45]
    models = sorted({m for d in past for m in d.fc if 'd1' in d.fc[m]})
    f = bt.fit_city(past, 'd1', models)
    if f is None:
        continue
    w, bias, a, _ = f
    series[s] = {"weights": {m: round(v, 4) for m, v in w.items()},
                 "bias": {m: round(v, 3) for m, v in bias.items()},
                 "sd": round(max(math.sqrt(a), 1.2), 3), "days": len(past)}
R = [r for r in rows if r['H'] == 36 and (last - r['day']).days < 183]
coef, se = fit(R)
json.dump({"built_from": str(last), "hours_before_close": 36, "threshold": 0.06,
           "blend": [round(c, 4) for c in coef], "blend_rows": len(R), "series": series},
          open(out, 'w'), indent=1)
print(out, len(series), 'series, blend', [round(c, 3) for c in coef], 'se', [round(x, 3) for x in se])
