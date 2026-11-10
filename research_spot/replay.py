"""Spot-signal replay for 15-min markets. usage: replay.py SAMPLE TRADES SPOT PRODUCT [MAXLEFT]"""
import sys, numpy as np, pandas as pd
from math import erf
sm = pd.read_csv(sys.argv[1]); tr = pd.read_csv(sys.argv[2]); sp = pd.read_csv(sys.argv[3])
prod = sys.argv[4]; MAXLEFT = int(sys.argv[5]) if len(sys.argv) > 5 else 600
REL = len(sys.argv) > 6 and sys.argv[6] == 'rel'
sp = sp[sp["product"] == prod].sort_values("t").drop_duplicates("t")
st = sp.t.values; sc = sp.close.values.astype(float)
lr = np.diff(np.log(sc))
Phi = np.vectorize(lambda x: 0.5 * (1 + erf(x / 2 ** 0.5)))
if "series" in sm: sm = sm[sm.series == sm.series.iloc[0]] if sm.series.nunique()==1 else sm
sm = sm[sm.ticker.isin(set(tr.ticker))]
rows = []
G = dict(tuple(tr.groupby('ticker')))
for m in sm.itertuples():
    g = G[m.ticker].sort_values("t")
    jo = np.searchsorted(st, m.open_ts - 60, side='right') - 1
    if jo < 0 or st[jo] < m.open_ts - 180: continue
    K = sc[jo] if REL else m.floor; yes_won = m.result == "yes"; close = m.close_ts
    t = g.t.values; y = g.yes_price.values; side = g.taker_side.values
    pr = np.where(side == "yes", y, 1 - y)
    for s_name in ("yes", "no"):
        a = np.where(side == s_name, pr, -1.0)
        suf = np.append(np.maximum.accumulate(a[::-1])[::-1][1:], -1.0)
        for i in np.where((side == s_name) & (pr >= 0.05) & (pr <= 0.29))[0]:
            left = close - t[i]
            if left > MAXLEFT or left < 0: continue
            q = round(1 - pr[i], 2)
            fav = "no" if s_name == "yes" else "yes"
            won = yes_won if fav == "yes" else not yes_won
            # spot as of the last completed minute (no look-ahead)
            j = np.searchsorted(st, (t[i] // 60) * 60 - 60, side="right") - 1
            if j < 61 or st[j] < t[i] - 200: continue
            S = sc[j]; sig = lr[j - 60:j].std()
            sgn = 1 if fav == "yes" else -1
            tau = max(left - 30, 15) / 60
            z = sgn * np.log(S / K) / (sig * np.sqrt(tau) + 1e-12)
            d1 = sgn * 1e4 * np.log(S / sc[j - 1]); d3 = sgn * 1e4 * np.log(S / sc[j - 3])
            rows.append(dict(tk=m.ticker, close=close, left=left, q=q, won=won,
                             paper=bool(suf[i] > pr[i] + 1e-9), z=z, pm=float(Phi(z)),
                             d1=d1, d3=d3, sigbps=sig * 1e4))
df = pd.DataFrame(rows)
fee = lambda q: 0.0175 * q * (1 - q)
df["pnl"] = 100 * (df.won.astype(float) - df.q - fee(df.q))
df["gap"] = df.pm - df.q
print(f"{prod}: markets {df.tk.nunique()} candidate fills {len(df)} (paper {df.paper.sum()})")

def stat(d):
    if len(d) == 0: return "n=0"
    mk = d.groupby("tk").pnl.agg(["sum", "count"])
    rng = np.random.default_rng(1); bs = []
    idx = np.arange(len(mk))
    for _ in range(400):
        b = mk.iloc[rng.choice(idx, len(idx))]; bs.append(b["sum"].sum() / b["count"].sum())
    return f"n={len(d):5d} mk={d.tk.nunique():4d} {d.pnl.mean():+6.2f}c [{np.percentile(bs,2.5):+.1f},{np.percentile(bs,97.5):+.1f}] win {d.won.mean():.2f} q {d.q.mean():.2f}"

for lab, base in (("PAPER", df[df.paper]), ("PESSIMISTIC(touch)", df)):
    print(f"\n== {lab}  all: {stat(base)}")
    print(" by model gap (spot-implied win prob minus price):")
    for lo, hi in ((-1, -.15), (-.15, -.05), (-.05, .05), (.05, .15), (.15, 1)):
        print(f"  {lo:+.2f}..{hi:+.2f}: {stat(base[(base.gap > lo) & (base.gap <= hi)])}")
    print(" by 1-min spot drift toward our side (bps):")
    for lo, hi in ((-1e9, -10), (-10, -3), (-3, 3), (3, 10), (10, 1e9)):
        print(f"  {lo:>6}..{hi:<6}: {stat(base[(base.d1 > lo) & (base.d1 <= hi)])}")
    print(" rules (kept fills):")
    for name, keep in (("skip gap<-0.05", base.gap >= -0.05), ("skip gap<-0.10", base.gap >= -0.10),
                       ("skip gap<0", base.gap >= 0), ("skip d1<-5bps", base.d1 >= -5),
                       ("skip d3<-10bps", base.d3 >= -10)):
        print(f"  {name:16s} kept {stat(base[keep])}")
        print(f"  {'':16s} skip {stat(base[~keep])}")
df.to_csv(f"/tmp/claude-0/an/fills_{prod}.csv", index=False)
