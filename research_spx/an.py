import json, csv, sys, statistics as st
from math import log, sqrt, erf, ceil
N = lambda x: 0.5 * (1 + erf(x / sqrt(2)))
fred = {}
for r in csv.DictReader(open(sys.argv[2] if len(sys.argv) > 2 else "fred.csv")):
    try: fred[r["observation_date"]] = (float(r["VIXCLS"]), float(r["SP500"]))
    except: pass
days = sorted(fred)
def prev(day):
    p = [d for d in days if d < day]
    return fred[p[-1]] if p else None
rows = []
seen=set(); ML=[]
for fn in sys.argv[1].split(","):
    for l in open(fn):
        m=json.loads(l)
        if m["ticker"] in seen: continue
        seen.add(m["ticker"]); ML.append(m)
for m in ML:
    if not m["trades"] or m["result"] not in ("yes", "no"): continue
    pv = prev(m["day"])
    if not pv: continue
    vix, s0 = pv
    y = 1 if m["result"] == "yes" else 0
    for t, px, cnt, side in m["trades"]:
        p = float(px)
        if p <= 0.01 or p >= 0.99: continue
        rows.append((m["day"], m["strike_type"], m["floor"], m["cap"], s0, vix, p, side, y))
print("trades", len(rows), "days", len({r[0] for r in rows}))
def prob(st_, fl, cp, s0, sig):
    def up(k): return 1 - N((log(k / s0) + sig * sig / 2) / sig)
    if st_ == "greater": return up(fl)
    if st_ == "less": return 1 - up(cp)
    return up(fl) - up(cp)
def fee(p): return ceil(7 * p * (1 - p) - 1e-9) / 100  # 1-contract taker order
def run(sub, k, th):
    pl = []
    for d, st_, fl, cp, s0, vix, p, side, y in sub:
        sig = k * vix / 100 / sqrt(252)
        q = prob(st_, fl, cp, s0, sig)
        if q - p > th and side == "yes": pl.append((d, y - p - fee(p)))
        elif p - q > th and side == "no": pl.append((d, (1 - y) - (1 - p) - fee(p)))
    if not pl: return (0, 0, 0, 0)
    byd = {}
    for d, v in pl: byd.setdefault(d, []).append(v)
    dm = [st.mean(v) for v in byd.values()]
    se = st.pstdev(dm) / sqrt(len(dm)) if len(dm) > 1 else 0
    return (len(pl), 100 * st.mean(dm), 100 * se, len(dm))
dd = sorted({r[0] for r in rows}); half = dd[len(dd) // 2]
A = [r for r in rows if r[0] < half]; B = [r for r in rows if r[0] >= half]
print("split", half, len(A), len(B))
# calibration of Kalshi price itself
brier_k = st.mean([(r[6] - r[8]) ** 2 for r in rows])
for k in (0.6, 0.8, 1.0):
    bm = st.mean([(prob(r[1], r[2], r[3], r[4], k * r[5] / 100 / sqrt(252)) - r[8]) ** 2 for r in rows])
    print(f"brier kalshi {brier_k:.4f} model k={k} {bm:.4f}")
for k in (0.6, 0.8, 1.0):
    for th in (0.03, 0.05, 0.10, 0.15):
        a = run(A, k, th); b = run(B, k, th); al = run(rows, k, th)
        print(f"k={k} th={th:.2f}  IS n={a[0]}/{a[3]}d {a[1]:+.1f}c±{a[2]:.1f}  OOS n={b[0]}/{b[3]}d {b[1]:+.1f}c±{b[2]:.1f}  ALL n={al[0]}/{al[3]}d {al[1]:+.1f}c±{al[2]:.1f}")
