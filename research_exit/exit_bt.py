"""Offline test of a model-based early exit for the weather day-before strategy.

usage: python research_exit/exit_bt.py days.pkl     (days.pkl from tools/build.py)

Entry (same as live): at 36h before close, rest 1c inside the spread when the
market+model blend says the side is worth >= 6c more than our price; filled if a
later trade printed at our price. Then at 24h and 12h before close, recompute the
blend (24h: day-before forecast; 12h: same-day forecast; blend coefficients
refit for that hour) and SELL at the bid if bid - taker fee >= blend value + margin.
Walk-forward by month: blend coefficients use earlier months only.
Caveat: we assume the entry filled before the exit check (fills can come later).
"""
import sys, pickle, math, collections
sys.path.insert(0, '.')
from wxbot import backtest as bt

days = pickle.load(open(sys.argv[1], 'rb'))
import os
LEAD = {36: 'd1', 24: 'd1', 12: os.environ.get('LEAD12', 'd0')}
METRIC = os.environ.get('METRIC')
def lg(p): p = min(max(p, 0.005), 0.995); return math.log(p/(1-p))
_src = open('tools/fit.py').read()
_ns = {'math': math}
exec(_src[_src.index('def sig'):_src.index('def sim')], _ns)   # sig/fit from tools/fit.py
sig, fit = _ns['sig'], _ns['fit']

by = collections.defaultdict(list)
for d in days:
    if not METRIC or d.metric == METRIC: by[d.series].append(d)
# per (day,series,ticker): {H: (m_logit, f_logit, bid, ask)}, plus won, tlo/thi at 36
recs = []
for s, L in by.items():
    for d in L:
        pr = {lead: bt.predict(L, d, lead) for lead in ('d1', 'd0')}
        for tk, lo, hi, won, q in d.brackets:
            h = {}
            for H, lead in LEAD.items():
                if H in q and pr[lead]:
                    b, a, tlo, thi = q[H]
                    if 0 < b < a < 1:
                        h[H] = (lg((a+b)/2), lg(bt.bracket_prob(*pr[lead], lo, hi)), b, a, tlo, thi)
            if 36 in h:
                recs.append(dict(day=d.day, ym=(d.day.year, d.day.month), y=1.0 if won else 0.0, h=h))

def rows_for(R, H):
    return [dict(m=r['h'][H][0], f=r['h'][H][1], y=r['y']) for r in R if H in r['h']]

months = sorted({r['ym'] for r in recs})
EXIT_HS = tuple(int(x) for x in sys.argv[2].split(",")) if len(sys.argv) > 2 else (24, 12)
MARGINS = (0.0, 0.03, 0.05, 0.08, 0.10, 0.15)
tot = collections.defaultdict(lambda: [0, 0.0, 0.0])   # key -> [n_sold, pnl, cost]
per_month = collections.defaultdict(dict)
for ym in months[3:]:
    tr = [r for r in recs if r['ym'] < ym]
    te = [r for r in recs if r['ym'] == ym]
    W = {H: fit(rows_for(tr, H))[0] for H in LEAD}
    pos = []   # (side, price, rec)
    for r in te:
        m, f, b, a, tlo, thi = r['h'][36]
        p = sig(W[36][0] + W[36][1]*m + W[36][2]*f)
        P = round(b + 0.01, 2)
        if P < a and tlo is not None and tlo <= P and p - P >= 0.06 and 0.03 <= P <= 0.97:
            pos.append(('yes', P, r))
        Q = round(1 - (a - 0.01), 2)
        if a - 0.01 > b and thi is not None and thi >= a - 0.01 and (1-p) - Q >= 0.06 and 0.03 <= Q <= 0.97:
            pos.append(('no', Q, r))
    for mg in ('hold',) + MARGINS:
        n = 0; pnl = cost = 0.0
        for side, P, r in pos:
            out = None
            if mg != 'hold':
                for H in EXIT_HS:
                    if H not in r['h']:
                        continue
                    m, f, b, a, _, _ = r['h'][H]
                    p = sig(W[H][0] + W[H][1]*m + W[H][2]*f)
                    v = p if side == 'yes' else 1 - p
                    bid = b if side == 'yes' else round(1 - a, 2)
                    if 0.01 <= bid <= 0.99 and bid - bt.fee(bid) >= v + mg:
                        out = bid - bt.fee(bid); n += 1
                        break
            win = 1.0 if (r['y'] == 1.0) == (side == 'yes') else 0.0
            pnl += (out if out is not None else win) - P; cost += P
        k = mg
        tot[k][0] += n; tot[k][1] += pnl; tot[k][2] += cost
        per_month[ym][k] = (n, round(pnl, 2), len(pos))

print('positions per month and P&L ($, 1 contract each) by exit rule')
print('month     ', 'hold', *[f'm{m:.2f}' for m in MARGINS])
for ym in sorted(per_month):
    row = per_month[ym]
    print(f'{ym[0]}-{ym[1]:02d} n={row["hold"][2]:4d}', f'{row["hold"][1]:+7.2f}',
          *[f'{row[m][1]:+7.2f}({row[m][0]})' for m in MARGINS])
print('TOTAL', {('hold' if k == 'hold' else f'm{k:.2f}'): (v[0], round(v[1], 2), f'{v[1]/v[2]*100:+.1f}%') for k, v in tot.items()})
better = {m: sum(per_month[ym][m][1] > per_month[ym]['hold'][1] for ym in per_month) for m in MARGINS}
worse = {m: sum(per_month[ym][m][1] < per_month[ym]['hold'][1] for ym in per_month) for m in MARGINS}
print('months better / worse than hold:', {f'{m:.2f}': (better[m], worse[m]) for m in MARGINS})
