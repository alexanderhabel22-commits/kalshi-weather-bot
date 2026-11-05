"""Maker (resting limit order) version of the walk-forward test.

Post 1¢ inside the spread at H hours before close when the market+model blend
says the side is worth >= thr more than our price. Filled if a later trade
printed at or through our price. Weather makers pay no fee."""
import sys, pickle, math
sys.path.insert(0, '.')
sys.argv += []
exec(open('tools/fit.py').read().split('for H in (36,24,12):')[0])   # reuse rows/fit/sig

def maker(R, w, thr, use_model=True):
    n = wins = 0; pnl = st = 0.0
    for r in R:
        if r['tlo'] is None and r['thi'] is None:
            continue
        p = sig(w[0] + w[1]*r['m'] + w[2]*r['f']) if use_model else sig(r['m'])
        P = round(r['b'] + 0.01, 2)                 # buy YES at bid+1
        if P < r['a'] and r['tlo'] is not None and r['tlo'] <= P and p - P >= thr and 0.03 <= P <= 0.97:
            n += 1; wins += r['y'] == 1; pnl += (1 if r['y'] == 1 else 0) - P; st += P
        Q = round(1 - (r['a'] - 0.01), 2)           # buy NO = sell YES at ask-1
        if r['a'] - 0.01 > r['b'] and r['thi'] is not None and r['thi'] >= r['a'] - 0.01 and (1-p) - Q >= thr and 0.03 <= Q <= 0.97:
            n += 1; wins += r['y'] == 0; pnl += (1 if r['y'] == 0 else 0) - Q; st += Q
    return n, wins, round(pnl, 2), round(pnl/st*100, 1) if st else 0

for H in (36, 24):
    R = [r for r in rows if r['H'] == H]
    months = sorted(set((r['day'].year, r['day'].month) for r in R))
    tot = {}
    for ym in months[3:]:
        tr = [r for r in R if (r['day'].year, r['day'].month) < ym]
        te = [r for r in R if (r['day'].year, r['day'].month) == ym]
        w, _ = fit(tr)
        res = [maker(te, w, t) for t in (0.0, 0.03, 0.06)]
        base = maker(te, w, -1.0, use_model=False)
        print(H, ym, 'thr0', res[0], 'thr.03', res[1], 'thr.06', res[2], '| no-model all fills', base)
