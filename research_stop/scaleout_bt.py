"""Does selling SOME contracts early (scaling out) beat holding all, or selling all?

usage: python research_stop/scaleout_bt.py <dir with the research data files>

Same data and entry as stop_bt.py (trade tapes bucketed by time before close:
Mentions 2025-Nov 2026, Entertainment / Economics / Elections samples).

Entry = a maker fill like the live strategies. Trigger = in a LATER bucket, a
trade shows someone bidding for our side at the trigger price (we could have
sold into it). Triggers tested:
  up +3c / +5c / +10c / 97c   take profit after a rise
  down -10c / -20c            cut a loser after a drop
At a trigger we sell at the trigger price minus the taker fee, and compare:
  hold all, sell all, sell half (scale out), each per contract and as
  bankroll growth (log) with the position at 15% of the bankroll (the event cap).
Depth: contracts that traded against bids at or above the trigger price in that
bucket, i.e. how many we could likely have sold there (a lower bound on the book).
Coarse: trade times are known only to the bucket.
"""
import csv, gzip, math, sys, collections
from pathlib import Path

D = Path(sys.argv[1])
F = 0.15            # position size as a share of bankroll (live per-event cap)


def fee(p):
    return math.ceil(round(0.07 * p * (1 - p) * 100, 6)) / 100


def load(trades, results, order):
    res = {r['ticker']: r['result'] for r in csv.DictReader(gzip.open(results, 'rt'))
           if r.get('result') in ('yes', 'no')}
    tr = collections.defaultdict(list)
    for t in trades:
        for r in csv.DictReader(gzip.open(t, 'rt')):
            if r['ticker'] not in res:
                continue
            y = float(r['yes_cents']) / 100 if 'yes_cents' in r else float(r['yes_price'])
            b = order(r['bucket'] if 'bucket' in r else r['ttc'])
            tr[r['ticker']].append((b, r['taker_side'], round(y, 2), float(r['count'])))
    return tr, res


TRIGS = [('up +3c', 0.03), ('up +5c', 0.05), ('up +10c', 0.10), ('at 97c', None),
         ('down -10c', -0.10), ('down -20c', -0.20)]


def sim(tr, res, sides, lo, hi, min_rank, keep=lambda tk: True):
    """-> list of (entry p, win, {trigger: (sale price, depth) or None})"""
    out = []
    for tk, ts in tr.items():
        if not keep(tk):
            continue
        for side in sides:
            our = lambda y: y if side == 'yes' else round(1 - y, 2)  # noqa: E731
            fills = [(b, our(y)) for b, s, y, c in ts
                     if s != side and b >= min_rank and lo <= our(y) <= hi]
            if not fills:
                continue
            be = max(b for b, _ in fills)
            ps = sorted(p for b, p in fills if b == be)
            p = ps[len(ps) // 2]
            later = sorted({b for b, s, y, c in ts if b < be})[::-1]
            # bids for our side that got hit, per later bucket: [(price, count)]
            bids = {b: [(our(y), c) for bb, s, y, c in ts if bb == b and s != side] for b in later}
            got = {}
            for name, d in TRIGS:
                th = 0.97 if d is None else round(p + d, 2)
                if not (0.02 <= th <= 0.99) or (d is None and p >= 0.97):
                    got[name] = None
                    continue
                hit = None
                for b in later:
                    if d is not None and d < 0:
                        q = [(x, c) for x, c in bids[b] if x <= th]
                        if q:
                            sp = max(x for x, _ in q)      # best bid at or under the trigger
                            hit = (sp, sum(c for x, c in bids[b] if x >= sp))
                            break
                    else:
                        q = [(x, c) for x, c in bids[b] if x >= th]
                        if q:
                            hit = (th, sum(c for _, c in q))
                            break
                got[name] = hit
            out.append((p, 1.0 if res[tk] == side else 0.0, got))
    return out


def report(name, rows):
    n = len(rows)
    if not n:
        print(f"{name}: none")
        return
    hold = sum(w - p for p, w, _ in rows) / n
    print(f"\n{name}: {n} positions, hold {hold*100:+.2f}c/contract")
    print(f"   {'trigger':<10} {'fired':>6} {'won after':>9} {'sell all':>9} {'sell half':>9} "
          f"{'growth hold/all/half (bp per position)':>40} {'depth>=6':>8} {'>=20':>5}")
    for tname, _ in TRIGS:
        tot = {'all': 0.0, 'half': 0.0}
        lg = {'hold': 0.0, 'all': 0.0, 'half': 0.0}
        fired, won, deep6, deep20 = 0, 0, 0, 0
        for p, w, got in rows:
            h = got.get(tname)
            r_hold = (w - p) / p
            if h is None:
                r_all = r_half = r_hold
            else:
                q, depth = h
                fired += 1
                won += w
                deep6 += depth >= 6
                deep20 += depth >= 20
                r_all = (q - fee(q) - p) / p
                r_half = (r_all + r_hold) / 2
            tot['all'] += p * r_all
            tot['half'] += p * r_half
            for k, r in (('hold', r_hold), ('all', r_all), ('half', r_half)):
                lg[k] += math.log(max(1 + F * r, 1e-9))
        if not fired:
            print(f"   {tname:<10} {0:>6}")
            continue
        g = '/'.join(f"{lg[k]/n*1e4:+.1f}" for k in ('hold', 'all', 'half'))
        print(f"   {tname:<10} {fired/n*100:>5.1f}% {won/fired*100:>8.1f}% "
              f"{(tot['all']/n - hold)*100:>+8.2f}c {(tot['half']/n - hold)*100:>+8.2f}c "
              f"{g:>40} {deep6/fired*100:>7.0f}% {deep20/fired*100:>4.0f}%")


mn = D / 'mentions'
tr, res = load(sorted(mn.glob('trades_*.csv.gz')), mn / 'markets.csv.gz', lambda b: int(b))
report("Mentions NO 75-90c (live band)", sim(tr, res, ['no'], 0.75, 0.90, 3))
ORDER = {k: i for i, k in enumerate(['a<15m', 'b15-60m', 'c1-4h', 'd4-24h', 'e1-7d', 'f7-30d', 'g>30d'])}
for cat in ('Entertainment', 'Economics', 'Elections'):
    p = D / cat
    if not p.exists():
        continue
    tr, res = load([p / 'trades_agg.csv.gz'], p / 'sample.csv.gz', lambda b: ORDER[b])
    if cat == 'Economics':
        MAC = ('KXFED-', 'KXFEDDECISION-', 'KXCPI-', 'KXCPIYOY-', 'KXCPICORE-', 'KXPCECORE-')
        report("Fed/CPI NO 71-95c (live band)", sim(tr, res, ['no'], 0.71, 0.95, 1,
                                                    lambda t: t.startswith(MAC)))
    report(f"{cat} favorites 85-96c (live band)", sim(tr, res, ['yes', 'no'], 0.85, 0.96, 1))
print("\n(sell half is exactly halfway between hold and sell all in expected cents, because every "
      "contract in a position pays the same; growth is log bankroll growth with the position at "
      f"{F:.0%} of the bankroll, which also counts the risk reduction)")
