"""Would selling a crashed position (our side's bid falls below 10c) beat holding?

usage: python research_stop/stop_bt.py <dir with the research data files>

Data (no new downloads): trade tapes bucketed by time before close from the
research branches -- Mentions (mentions-research-data, 2025-Nov 2026),
Entertainment and Economics samples (cat-/fin-research-data, Feb 2025-Oct 2026).

Entry = a maker fill like the live strategies (first time bucket where a taker
sold our side to us inside the band). Exit test = in a LATER bucket, someone bid
for our side below 10c (a trade we could have hit). We sell there at min(9c,
that bid) minus the taker fee and compare with holding to settlement.
Coarse: we only know trade times to the bucket, so same-bucket ordering is ignored.
"""
import csv, gzip, math, sys, collections
from pathlib import Path

D = Path(sys.argv[1])
def fee(p, n=1): return math.ceil(round(0.07 * n * p * (1 - p) * 100, 6)) / 100 / n

def load(trades, results, order):
    """-> {ticker: [(bucket_rank, taker_side, yes_price, count)]}, {ticker: result}"""
    res = {r['ticker']: r['result'] for r in csv.DictReader(gzip.open(results, 'rt')) if r.get('result') in ('yes', 'no')}
    tr = collections.defaultdict(list)
    for t in trades:
        for r in csv.DictReader(gzip.open(t, 'rt')):
            if r['ticker'] not in res:
                continue
            y = float(r['yes_cents']) / 100 if 'yes_cents' in r else float(r['yes_price'])
            b = order(r['bucket'] if 'bucket' in r else r['ttc'])
            tr[r['ticker']].append((b, r['taker_side'], y, float(r['count'])))
    return tr, res

def sim(tr, res, sides, lo, hi, min_rank, keep=lambda tk: True, trig=0.10):
    """rank: bigger = earlier (farther from close)."""
    out = []
    for tk, ts in tr.items():
        if not keep(tk):
            continue
        for side in sides:
            # maker buy of `side` at p: a taker SOLD that side to us
            fills = [(b, (y if side == 'yes' else 1 - y)) for b, s, y, c in ts
                     if s != side and b >= min_rank and lo <= (y if side == 'yes' else 1 - y) <= hi]
            if not fills:
                continue
            be = max(b for b, _ in fills)
            ps = sorted(p for b, p in fills if b == be)
            p = ps[len(ps) // 2]
            # taker_side is the side the taker bought, so a maker BIDDING for our side shows
            # up as a trade with taker_side != side; that bid is what we could sell into
            later = sorted({b for b, s, y, c in ts if b < be})[::-1]
            sold = None
            for b in later:
                bids = [(y if side == 'yes' else 1 - y) for bb, s, y, c in ts if bb == b and s != side]
                low = [q for q in bids if q < trig]
                if low:
                    q = min(trig - 0.01, max(low))
                    sold = q - fee(q)
                    break
            win = 1.0 if res[tk] == side else 0.0
            out.append((p, win, sold))
    return out

def report(name, rows):
    n = len(rows); trig = [r for r in rows if r[2] is not None]
    hold = sum(w - p for p, w, s in rows)
    stop = sum((s if s is not None else w) - p for p, w, s in rows)
    tw = sum(w for p, w, s in trig); ts = sum(s for p, w, s in trig)
    print(f"{name}: {n} positions, hold {hold/n*100:+.2f}c/pos" if n else f"{name}: none")
    if n:
        print(f"   triggered {len(trig)} ({len(trig)/n*100:.1f}%); those then won {tw:.0f} "
              f"({(tw/len(trig)*100 if trig else 0):.1f}%), sale avg {(ts/len(trig)*100 if trig else 0):.1f}c")
        print(f"   with stop {stop/n*100:+.2f}c/pos -> change {(stop-hold)/n*100:+.3f}c/pos, "
              f"{(ts - tw)*100/ max(len(trig),1):+.1f}c per triggered position")
    return n, len(trig), tw, ts

mn = D / 'mentions'
tr, res = load(sorted(mn.glob('trades_*.csv.gz')), mn / 'markets.csv.gz', lambda b: int(b))
report("Mentions NO 75-90c (live band)", sim(tr, res, ['no'], 0.75, 0.90, 3))
report("Mentions any side 56-96c", sim(tr, res, ['yes', 'no'], 0.56, 0.96, 3))
ORDER = {k: i for i, k in enumerate(['a<15m', 'b15-60m', 'c1-4h', 'd4-24h', 'e1-7d', 'f7-30d', 'g>30d'])}
for cat in ('Entertainment', 'Economics', 'Elections'):
    p = D / cat
    if not p.exists():
        continue
    tr, res = load([p / 'trades_agg.csv.gz'], p / 'sample.csv.gz', lambda b: ORDER[b])
    if cat == 'Economics':
        MAC = ('KXFED-', 'KXFEDDECISION-', 'KXCPI-', 'KXCPIYOY-', 'KXCPICORE-', 'KXPCECORE-')
        report("Fed/CPI NO 71-95c (live band)", sim(tr, res, ['no'], 0.71, 0.95, 1, lambda t: t.startswith(MAC)))
    report(f"{cat} favorites 85-96c (live band)", sim(tr, res, ['yes', 'no'], 0.85, 0.96, 1))
    report(f"{cat} any side 56-96c", sim(tr, res, ['yes', 'no'], 0.56, 0.96, 1))
