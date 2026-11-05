import csv, gzip, pickle, collections, math, sys, json
CAND = sys.argv[1]
G = '/tmp/claude-0/d3/games.csv.gz'
out = pickle.load(open('/tmp/claude-0/work/matched.pkl', 'rb'))
games = {r['event_id']: r for r in csv.DictReader(gzip.open(G, 'rt')) if r['provider_id'] == '100'}
FEEM = {'mlb': 0.5}

def imp(ml):
    try: x = float(str(ml).replace('+', ''))
    except ValueError: return None
    if x == 0 or abs(x) < 100: return None
    return 100 / (x + 100) if x > 0 else -x / (-x + 100)

def fair(g, side, which):
    h, a = imp(g[f'h_ml_{which}']), imp(g[f'a_ml_{which}'])
    if not h or not a: return None, None
    return (h if side == 'h' else a) / (h + a), h + a - 1

def fee(p, lg, maker=False):
    return (0.0175 if maker else 0.07) * FEEM.get(lg, 1) * p * (1 - p)

cs = collections.defaultdict(lambda: {'m': [], 'h': []})
for r in csv.DictReader(gzip.open(CAND, 'rt')):
    b, a = r['yes_bid'], r['yes_ask']
    cs[r['ticker']][r['kind']].append((int(r['end_ts']), float(b) if b else None, float(a) if a else None,
                                       float(r['volume'] or 0)))

def at(t, kind, ts):
    best = None
    for x in cs[t][kind]:
        if x[0] <= ts and x[1] is not None and x[2] is not None and 0 < x[1] < x[2] < 1:
            if best is None or x[0] > best[0]: best = x
    if best and ts - best[0] <= (900 if kind == 'm' else 7200): return best
    return None

H = {'T-5m': ('m', 300, 'close'), 'T-60m': ('m', 3600, 'close'), 'T-24h': ('h', 86400, 'open')}
rows = []
for o in out:
    g = games.get(o['event_id'])
    if not g: continue
    y = 1 if o['result'] == 'yes' else 0
    for hn, (kind, off, which) in H.items():
        q = at(o['ticker'], kind, o['start_ts'] - off)
        f_, vig = fair(g, o['side'], which)
        fc, _ = fair(g, o['side'], 'close')
        if not q or f_ is None: continue
        rows.append(dict(lg=o['league'], h=hn, t=o['ticker'], side=o['side'], y=y, bid=q[1], ask=q[2],
                         mid=(q[1] + q[2]) / 2, dk=f_, dkc=fc, vig=vig, start=o['start_ts']))
pickle.dump(rows, open('/tmp/claude-0/work/rows.pkl', 'wb'))

def brier(v): return sum((p - y) ** 2 for p, y in v) / len(v)
res = {}
print('== Calibration (home-side markets only, one per game)')
for hn in H:
    for lg in sorted({r['lg'] for r in rows}) + ['ALL']:
        v = [r for r in rows if r['h'] == hn and r['side'] == 'h' and (lg == 'ALL' or r['lg'] == lg)]
        if len(v) < 30: continue
        bk = brier([(r['mid'], r['y']) for r in v]); bd = brier([(r['dk'], r['y']) for r in v])
        gap = sum(abs(r['mid'] - r['dk']) for r in v) / len(v)
        spr = sum(r['ask'] - r['bid'] for r in v) / len(v)
        print(f"{hn:6} {lg:6} n={len(v):5} brier kalshi {bk:.4f} dk {bd:.4f} | mean|kalshi-dk| {gap*100:.1f}c spread {spr*100:.1f}c")

print('\n== Taker rule: buy YES at ask when DK fair - ask >= thr (both team markets), after taker fee')
for hn in H:
    for thr in (0.0, 0.02, 0.03, 0.05, 0.08):
        for lg in sorted({r['lg'] for r in rows}) + ['ALL']:
            v = [r for r in rows if r['h'] == hn and (lg == 'ALL' or r['lg'] == lg) and r['dk'] - r['ask'] >= thr]
            if len(v) < (20 if lg != 'ALL' else 1): continue
            pnl = [r['y'] - r['ask'] - fee(r['ask'], r['lg']) for r in v]
            m = sum(pnl) / len(pnl); sd = (sum((x - m) ** 2 for x in pnl) / max(1, len(pnl) - 1)) ** .5
            print(f"{hn:6} thr {thr:.2f} {lg:6} n={len(v):5} pnl/contract {m*100:+.2f}c  (se {sd/len(pnl)**.5*100:.2f}c)")

print('\n== Maker proxy: resting bid at current bid when DK fair - bid >= thr; assumes fill, maker fee')
for hn in ('T-60m', 'T-24h'):
    for thr in (0.03, 0.05, 0.08):
        v = [r for r in rows if r['h'] == hn and r['dk'] - r['bid'] >= thr]
        if not v: continue
        pnl = [r['y'] - r['bid'] - fee(r['bid'], r['lg'], True) for r in v]
        m = sum(pnl) / len(pnl); sd = (sum((x - m) ** 2 for x in pnl) / max(1, len(pnl) - 1)) ** .5
        print(f"{hn:6} thr {thr:.2f} n={len(v):5} pnl/contract {m*100:+.2f}c (se {sd/len(pnl)**.5*100:.2f}c)  [fill not guaranteed]")
