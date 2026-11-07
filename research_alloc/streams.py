"""Build one historical trade stream per live strategy for the allocator backtest.

usage: python research_alloc/streams.py <research data dir> <weather backtest root> <out.csv>

<research data dir> holds the runs/ folders of mentions-, cat- and fin-research-data.
Fills are maker fills like the live strategies (a taker sold our side to us inside
the band, earliest qualifying time bucket, median price there), one per market:
  mentions   NO 75-90c, 1h+ before the event, words with 1,000+ contracts traded
  favorites  85-96c favorite, 1h+ before close, Elections/Politics/Entertainment
             events with 1M+ contracts traded, at most 2 markets per event
  macro_fade NO 71-95c in Fed / CPI / PCE markets, 1h+ before close, 3 per event
  weather    the walk-forward day-before model's trades (wxbot.backtest, taker rule)
Output rows: strategy, date (settle), event, ticker, price, won
"""
import collections
import csv
import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wxbot import backtest as bt  # noqa: E402

D, WX, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
ORDER = {k: i for i, k in enumerate(['a<15m', 'b15-60m', 'c1-4h', 'd4-24h', 'e1-7d', 'f7-30d', 'g>30d'])}
MAC = ('KXFED-', 'KXFEDDECISION-', 'KXCPI-', 'KXCPIYOY-', 'KXCPICORE-', 'KXPCECORE-')


def fills(trade_files, meta, sides, lo, hi, min_rank, rank, keep=lambda tk: True):
    tr = collections.defaultdict(list)
    for f in trade_files:
        for r in csv.DictReader(gzip.open(f, 'rt')):
            tk = r['ticker']
            if tk not in meta or not keep(tk):
                continue
            y = float(r['yes_cents']) / 100 if 'yes_cents' in r else float(r['yes_price'])
            tr[tk].append((rank(r['bucket'] if 'bucket' in r else r['ttc']), r['taker_side'], y))
    out = []
    for tk, ts in tr.items():
        best = None
        for side in sides:
            fl = [(b, y if side == 'yes' else 1 - y) for b, s, y in ts
                  if s != side and b >= min_rank and lo <= (y if side == 'yes' else 1 - y) <= hi]
            if not fl:
                continue
            be = max(b for b, _ in fl)
            ps = sorted(p for b, p in fl if b == be)
            cand = (be, side, ps[len(ps) // 2])
            best = cand if best is None or cand[0] > best[0] else best
        if best:
            m = meta[tk]
            out.append({"date": m["date"], "event": m["event"], "ticker": tk, "price": round(best[2], 2),
                        "won": int(m["result"] == best[1])})
    return out


def per_event(rows, k):
    seen = collections.Counter()
    out = []
    for r in sorted(rows, key=lambda r: (r["event"], -r["price"])):
        if seen[r["event"]] < k:
            seen[r["event"]] += 1
            out.append(r)
    return out


rows = []
# Mentions
mn = D / '20261105T0910Z'
meta = {}
for r in csv.DictReader(gzip.open(mn / 'markets.csv.gz', 'rt')):
    if r['result'] in ('yes', 'no') and float(r['volume'] or 0) >= 1000:
        meta[r['ticker']] = {"date": (r['settled_at'] or r['close'])[:10], "event": r['event'], "result": r['result']}
m = fills(sorted(mn.glob('trades_*.csv.gz')), meta, ['no'], 0.75, 0.90, 3, int)
rows += [dict(x, strategy="mentions") for x in per_event(m, 3)]

# Favorites + macro (category samples)
def cat_meta(cat):
    meta, evvol = {}, collections.Counter()
    for r in csv.DictReader(gzip.open(D / f'20261105-{cat}' / 'sample.csv.gz', 'rt')):
        evvol[r['event']] += float(r['volume'] or 0)
        if r['result'] in ('yes', 'no'):
            meta[r['ticker']] = {"date": r['close'][:10], "event": r['event'], "result": r['result']}
    return meta, evvol

for cat in ('Elections', 'Politics', 'Entertainment'):
    meta, evvol = cat_meta(cat)
    meta = {k: v for k, v in meta.items() if evvol[v["event"]] >= 1e6}
    f = fills([D / f'20261105-{cat}' / 'trades_agg.csv.gz'], meta, ['yes', 'no'], 0.85, 0.96, 2, ORDER.get)
    rows += [dict(x, strategy="favorites") for x in per_event(f, 2)]
meta, _ = cat_meta('Economics')
f = fills([D / '20261105-Economics' / 'trades_agg.csv.gz'], meta, ['no'], 0.71, 0.95, 2, ORDER.get,
          lambda t: t.startswith(MAC))
rows += [dict(x, strategy="macro_fade") for x in per_event(f, 3)]

# Weather
days = bt.load(WX)
res = bt.evaluate(days, lead="d1", hour=24, min_edge=0.05)
for day, series, tk, side, prob, price, pnl in res["trade_list"]:
    rows.append({"strategy": "weather", "date": day.isoformat(), "event": f"{series}-{day}", "ticker": tk,
                 "price": price, "won": int(pnl > 0)})

with open(OUT, "w", newline="") as fh:
    w = csv.DictWriter(fh, ["strategy", "date", "event", "ticker", "price", "won"])
    w.writeheader()
    w.writerows(sorted(rows, key=lambda r: r["date"]))
c = collections.Counter(r["strategy"] for r in rows)
for s, n in sorted(c.items()):
    rs = [r for r in rows if r["strategy"] == s]
    roi = sum(r["won"] - r["price"] for r in rs) / sum(r["price"] for r in rs)
    print(f"{s}: {n} fills, {min(r['date'] for r in rs)}..{max(r['date'] for r in rs)}, "
          f"hit {sum(r['won'] for r in rs)/n:.0%}, avg price {sum(r['price'] for r in rs)/n:.2f}, "
          f"{roi*100:+.1f}c per $1")
