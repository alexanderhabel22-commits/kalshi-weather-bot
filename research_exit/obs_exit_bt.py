"""Early-sell test using LIVE READINGS + forecast, checked through the event day.

usage: python research_exit/obs_exit_bt.py <backtest root> [days.pkl]

Value of a held bracket at check time t (H hours before close):
  final extreme = max(observed so far + intra-hour gap, rest-of-day forecast)
  observed so far : hourly METAR readings in the CLI day up to t (0.1C T-group)
  rest-of-day     : DAY-BEFORE hourly model forecast (no look-ahead), shifted by
                    today's forecast error over the last 3 observed hours,
                    sd grows with hours left. (Live would use fresher runs.)
  P(bracket) analytic; then blended with the market mid, coefficients fit per
  check hour on earlier months only (walk-forward). Lows: everything negated.
Entries = live rule at 36h. Sell at the bid if bid - fee >= blend value + margin.
"""
import sys, gzip, csv, json, math, pickle, re, collections, os
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
sys.path.insert(0, '.')
from wxbot import backtest as bt
from wxbot.stations import default_catalog, cli_window
from wxbot import obs_exit

ROOT = Path(sys.argv[1])
HS = (18, 15, 12, 9, 6, 3)
UTC = timezone.utc
if len(sys.argv) > 2 and os.path.exists(sys.argv[2]):
    days = pickle.load(open(sys.argv[2], 'rb'))
else:
    days = bt.load(ROOT, hours=(36,) + HS, since=date(2025, 9, 28))
    if len(sys.argv) > 2:
        pickle.dump(days, open(sys.argv[2], 'wb'))
CAT = {s.ticker: s for s in default_catalog()}
TG = re.compile(r' T([01])(\d{3})([01])(\d{3})')

def readings(icao):
    p = ROOT / 'readings' / f'{icao}.csv.gz'
    out = []
    if not p.exists():
        return out
    for r in csv.DictReader(gzip.open(p, 'rt')):
        t = datetime.strptime(r['valid'], '%Y-%m-%d %H:%M').replace(tzinfo=UTC)
        m = TG.search(r.get('metar') or '')
        if m:
            c = int(m.group(2)) / 10 * (-1 if m.group(1) == '1' else 1); f = c * 9 / 5 + 32
        else:
            try: f = float(r['tmpf'])
            except ValueError: continue
        out.append((t, f))
    return sorted(out)

def hourly_fc(icao):
    p = ROOT / 'forecasts' / f'{icao}.json.gz'
    d = bt._read(p) if p.exists() else {}
    out = {}
    for m, h in d.items():
        v = h.get('temperature_2m_previous_day1') or []
        out[m] = {datetime.strptime(t, '%Y-%m-%dT%H:%M').replace(tzinfo=UTC): x for t, x in zip(h['time'], v) if x is not None}
    return out

RD, FC = {}, {}

def value(d, lo, hi, t, fit):
    si = CAT[d.series]; st = si.station
    if st.icao not in RD:
        RD[st.icao] = readings(st.icao); FC[st.icao] = hourly_fc(st.icao)
    w0, w1 = cli_window(d.day, st.tz)
    return obs_exit.value(si.metric, lo, hi, t, w0, w1, RD[st.icao], FC[st.icao], fit[0], fit[1])

def lg(p): p = min(max(p, 0.005), 0.995); return math.log(p / (1 - p))
_src = open('tools/fit.py').read(); _ns = {'math': math}
exec(_src[_src.index('def sig'):_src.index('def sim')], _ns); sig, fit = _ns['sig'], _ns['fit']

by = collections.defaultdict(list)
for d in days: by[d.series].append(d)
recs = []
for s, L in by.items():
    if s not in CAT:
        continue
    for d in L:
        pr = bt.predict(L, d, 'd1')
        past = [h for h in L if h.day < d.day and (d.day - h.day).days <= 45]
        models = [m for m in d.fc if 'd1' in d.fc[m]]
        f = bt.fit_city(past, 'd1', models) if len(past) >= 15 and models else None
        if not pr or not f:
            continue
        cw = (f[0], f[1])
        close_by_tk = {}
        for tk, lo, hi, won, q in d.brackets:
            if 36 not in q or not (0 < q[36][0] < q[36][1] < 1):
                continue
            h = {36: (lg((q[36][0] + q[36][1]) / 2), lg(bt.bracket_prob(*pr, lo, hi))) + tuple(q[36])}
            # close time: from quotes we only know H; use the series' CLI window end + Kalshi close lag
            for H in HS:
                if H in q and 0 < q[H][0] < q[H][1] < 1:
                    h[H] = (lo, hi) + tuple(q[H])
            recs.append(dict(d=d, cw=cw, ym=(d.day.year, d.day.month), y=1.0 if won else 0.0, h=h, lo=lo, hi=hi))

def close_time(d):
    # Kalshi weather markets close ~ the morning after; quotes were taken at close - H.
    # The archive keeps H relative to the real close; we rebuild it from the CLI window
    # end (local midnight std time) + 1h, the usual Kalshi close for these series.
    _, w1 = cli_window(d.day, CAT[d.series].station.tz)
    return w1 + timedelta(hours=1)

print('records', len(recs))
# precompute model values at each check hour
for r in recs:
    ct = close_time(r['d'])
    r['v'] = {}
    for H in HS:
        if H in r['h']:
            v = value(r['d'], r['lo'], r['hi'], ct - timedelta(hours=H), r['cw'])
            if v is not None:
                b, a = r['h'][H][2], r['h'][H][3]
                r['v'][H] = (lg((a + b) / 2), lg(v), b, a)

months = sorted({r['ym'] for r in recs})
MARGINS = (0.03, 0.05, 0.08, 0.12)
RULES = {'all': HS, 'H>=12': (18, 15, 12), 'H<=9': (9, 6, 3)}
tot = collections.defaultdict(lambda: [0, 0.0, 0.0]); pm = collections.defaultdict(dict)
for ym in months[3:]:
    tr = [r for r in recs if r['ym'] < ym]; te = [r for r in recs if r['ym'] == ym]
    W36 = fit([dict(m=r['h'][36][0], f=r['h'][36][1], y=r['y']) for r in tr])[0]
    W = {H: fit([dict(m=r['v'][H][0], f=r['v'][H][1], y=r['y']) for r in tr if H in r['v']])[0] for H in HS}
    pos = []
    for r in te:
        m, f, b, a, tlo, thi = r['h'][36]
        p = sig(W36[0] + W36[1] * m + W36[2] * f)
        P = round(b + 0.01, 2)
        if P < a and tlo is not None and tlo <= P and p - P >= 0.06 and 0.03 <= P <= 0.97:
            pos.append(('yes', P, r))
        Q = round(1 - (a - 0.01), 2)
        if a - 0.01 > b and thi is not None and thi >= a - 0.01 and (1 - p) - Q >= 0.06 and 0.03 <= Q <= 0.97:
            pos.append(('no', Q, r))
    keys = [('hold', None, None)] + [(rn, hs, mg) for rn, hs in RULES.items() for mg in MARGINS]
    for name, hs, mg in keys:
        n = 0; pnl = cost = 0.0
        for side, P, r in pos:
            out = None
            for H in (hs or ()):
                if H not in r['v']:
                    continue
                m, f, b, a = r['v'][H]
                v = sig(W[H][0] + W[H][1] * m + W[H][2] * f)
                v = v if side == 'yes' else 1 - v
                bid = b if side == 'yes' else round(1 - a, 2)
                if 0.01 <= bid <= 0.99 and bid - bt.fee(bid) >= v + mg:
                    out = bid - bt.fee(bid); n += 1; break
            win = 1.0 if (r['y'] == 1.0) == (side == 'yes') else 0.0
            pnl += (out if out is not None else win) - P; cost += P
        k = (name, mg); tot[k][0] += n; tot[k][1] += pnl; tot[k][2] += cost; pm[ym][k] = pnl
print('month   ', *[f'{k[0]}/{k[1]}' for k in tot])
for ym in sorted(pm):
    print(f'{ym[0]}-{ym[1]:02d}', *[f'{pm[ym][k]:+7.2f}' for k in tot])
for k, v in tot.items():
    better = sum(pm[ym][k] > pm[ym][('hold', None)] + 1e-9 for ym in pm)
    worse = sum(pm[ym][k] < pm[ym][('hold', None)] - 1e-9 for ym in pm)
    print(f'{k[0]:6s} margin {k[1]}: sells {v[0]:4d}  P&L {v[1]:+8.2f}  ({v[1]/v[2]*100:+.1f}%)  months better/worse {better}/{worse}')

# ---- live parameters: blend per check hour fit on the last 6 months
if os.environ.get('WRITE_PARAMS'):
    last = max(r['d'].day for r in recs)
    R6 = [r for r in recs if (last - r['d'].day).days < 183]
    Pj = json.load(open('data/daybefore_params.json'))
    Pj['exit_obs'] = {"built_from": str(last), "hours_before_close": list(HS), "window_hours": 0.25,
                      "margin": 0.08, "shadow": True,
                      "blend": {str(H): [round(c, 4) for c in fit([dict(m=r['v'][H][0], f=r['v'][H][1], y=r['y'])
                                                                for r in R6 if H in r['v']])[0]] for H in HS}}
    json.dump(Pj, open('data/daybefore_params.json', 'w'), indent=1)
    print('wrote exit_obs', Pj['exit_obs']['blend'])
