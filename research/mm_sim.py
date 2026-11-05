"""Replay a simple market-making bot on Kalshi hourly temp markets.

index: dict city-> {minute datetime (UTC, naive str 'YYYY-MM-DD HH:MM') : value F}
Fair p = P(I(S) > K | I(t-L)) with normal changes, sd(tau) fitted on train days.
Quotes refreshed each minute: bid = p-h, ask = p+h (cents), size Q, never cross the book.
Fills from real trades: taker sells (taker_side 'no') at yes_price <= our bid fill our bid;
taker buys at >= our ask fill our ask. Ties at our price get share TIE.
"""
import csv, math, collections, statistics as st
from datetime import datetime, timedelta

def Phi(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def T(s): return datetime.fromisoformat(s.replace('Z','+00:00')).replace(tzinfo=None)
def key(t): return t.strftime('%Y-%m-%d %H:%M')

def last_at(idx, t, back=15):
    for k in range(back):
        v = idx.get(key(t - timedelta(minutes=k)))
        if v is not None: return v
    return None

def fit_sd(idx, vals, split):
    """sd and mean of I(S)-I(S-tau) for tau 1..70 on training settlements."""
    out = {}
    for tau in range(1, 71):
        d = []
        for S, v in vals.items():
            if S >= split: continue
            a = idx.get(key(S - timedelta(minutes=tau)))
            if a is not None: d.append(v - a)
        if len(d) > 30: out[tau] = (st.mean(d), max(st.pstdev(d), 0.15))
    return out

def run(D, city_series, idx, split, L=3, h=0.03, Q=10, TIE=0.3, maxpos=50, maker_fee=0.0,
        test_only=True, pmin=0.05, fair='model', w=0.5):
    mk = {}; vals = {}
    for r in csv.DictReader(open(f'{D}/mk_{city_series}.csv')):
        if r['strike_type'] != 'greater': continue
        r['S'] = T(r['close']); mk[r['ticker']] = r
        if r['value']: vals[r['S']] = float(r['value'])
    sdt = fit_sd(idx, vals, split)
    book = collections.defaultdict(dict)
    for r in csv.DictReader(open(f'{D}/cd_{city_series}.csv')):
        if r['bid'] and r['ask']: book[r['ticker']][int(r['min_before_close'])] = (float(r['bid']), float(r['ask']))
    trades = collections.defaultdict(list)
    for r in csv.DictReader(open(f'{D}/tr_{city_series}.csv')):
        trades[r['ticker']].append((T(r['time']), float(r['yes_price']), float(r['count']), r['taker_side']))
    day = collections.defaultdict(float); fills = 0; vol = 0; cap = collections.defaultdict(float)
    per_mkt = []
    for tk, m in mk.items():
        S = m['S']
        if test_only and S < split: continue
        if m['result'] not in ('yes', 'no') or tk not in trades: continue
        K = float(m['floor']); y = 1 if m['result'] == 'yes' else 0
        tl = sorted(trades[tk])
        pos = 0.0; cash = 0.0; i = 0
        for mb in range(59, 0, -1):     # minute buckets before close
            t0 = S - timedelta(minutes=mb + 1)
            cur = last_at(idx, t0 - timedelta(minutes=L), 3)
            # trades in this minute
            mt = []
            while i < len(tl) and tl[i][0] < t0 + timedelta(minutes=1):
                if tl[i][0] >= t0: mt.append(tl[i])
                i += 1
            if cur is None or not mt: continue
            tau = mb + 1 + L
            mu, sd = sdt.get(min(tau, 70), (0, 1.5))
            p = 1 - Phi((K - cur - mu) / sd)
            bb0 = book[tk].get(mb + 1)
            if fair != 'model':
                if not bb0 or bb0[1] - bb0[0] > 0.15: continue
                mid = (bb0[0] + bb0[1]) / 2
                p = mid if fair == 'mid' else w * p + (1 - w) * mid
            if not (pmin < p < 1 - pmin): continue
            bid = math.floor((p - h) * 100) / 100; ask = math.ceil((p + h) * 100) / 100
            bb = book[tk].get(mb + 1)
            if bb:
                bid = min(bid, bb[1] - 0.01); ask = max(ask, bb[0] + 0.01)
            bq = Q if pos < maxpos else 0; aq = Q if pos > -maxpos else 0
            for (_, yp, c, side) in mt:
                if side == 'no' and bq > 0 and bid > 0 and yp <= bid + 1e-9:
                    f = min(bq, c if yp < bid - 1e-9 else c * TIE)
                    if f >= 1:
                        f = int(f); bq -= f; pos += f; cash -= f * bid + maker_fee * f * bid * (1 - bid); fills += 1; vol += f
                elif side == 'yes' and aq > 0 and ask < 1 and yp >= ask - 1e-9:
                    f = min(aq, c if yp > ask + 1e-9 else c * TIE)
                    if f >= 1:
                        f = int(f); aq -= f; pos -= f; cash += f * ask - maker_fee * f * ask * (1 - ask); fills += 1; vol += f
        pnl = cash + pos * y
        if pos or cash:
            day[S.date()] += pnl; per_mkt.append(pnl)
    dv = list(day.values())
    return {'pnl': sum(dv), 'days': len(dv), 'fills': fills, 'contracts': vol,
            'per_contract_c': 100 * sum(dv) / vol if vol else 0,
            'worst_day': min(dv) if dv else 0, 'best_day': max(dv) if dv else 0,
            't': (st.mean(dv) / (st.pstdev(dv) / math.sqrt(len(dv)))) if len(dv) > 2 and st.pstdev(dv) > 0 else 0,
            'sd_fit_30': sdt.get(30), 'sd_fit_5': sdt.get(5)}
