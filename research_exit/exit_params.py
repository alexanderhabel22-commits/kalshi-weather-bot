"""Add the model-exit block to data/daybefore_params.json.

usage: python research_exit/exit_params.py days.pkl [params.json]

Per series: same-day ('d0') model weights, biases and error sd from the trailing
45 days. Blend: sig(c0 + c1*logit(mid) + c2*logit(model p)) fit on the last 6
months of quotes 12 hours before close, using walk-forward d0 predictions.
Entry parameters are left untouched."""
import json, math, pickle, sys
from collections import defaultdict
sys.path.insert(0, '.')
from wxbot import backtest as bt

days = pickle.load(open(sys.argv[1], 'rb'))
out = sys.argv[2] if len(sys.argv) > 2 else 'data/daybefore_params.json'
src = open('tools/fit.py').read()
_ns = {'math': math}
exec(src[src.index('def sig'):src.index('def sim')], _ns)      # sig, fit from tools/fit.py
fit = _ns['fit']
def lg(p): p = min(max(p, 0.005), 0.995); return math.log(p/(1-p))

by = defaultdict(list)
for d in days: by[d.series].append(d)
last = max(d.day for d in days)
series, R = {}, []
for s, L in by.items():
    past = [d for d in L if (last - d.day).days < 45]
    models = sorted({m for d in past for m in d.fc if 'd0' in d.fc[m]})
    f = bt.fit_city(past, 'd0', models)
    if f is not None:
        w, bias, a, _ = f
        series[s] = {"weights": {m: round(v, 4) for m, v in w.items()},
                     "bias": {m: round(v, 3) for m, v in bias.items()},
                     "sd": round(max(math.sqrt(a), 1.2), 3), "days": len(past)}
    for d in L:
        if (last - d.day).days >= 183:
            continue
        pr = bt.predict(L, d, 'd0')
        if not pr:
            continue
        for tk, lo, hi, won, q in d.brackets:
            if 12 in q and 0 < q[12][0] < q[12][1] < 1:
                b, a = q[12][:2]
                R.append(dict(m=lg((a + b) / 2), f=lg(bt.bracket_prob(*pr, lo, hi)), y=1.0 if won else 0.0))
coef, se = fit(R)
P = json.load(open(out))
P["exit"] = {"built_from": str(last), "hours_before_close": 12, "window_hours": 0.75,
             "margin": 0.08, "blend": [round(c, 4) for c in coef], "blend_rows": len(R),
             "series": series}
json.dump(P, open(out, 'w'), indent=1)
print(out, len(series), 'series, exit blend', [round(c, 3) for c in coef], 'se', [round(x, 3) for x in se])
