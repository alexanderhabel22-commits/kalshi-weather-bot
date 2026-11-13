"""Signals from other traders' flow, scored at a price WE could get, with a time holdout.
Research only.  python research_flow/analyze.py <copycat dir> <markets.json.gz>

A. Big bets (anonymous $2k+, named $500+, taker): FOLLOW = buy their side at their price +1c;
   FADE = buy the other side at (1 - their price) + 1c. Taker fee (0.07 p(1-p), rounded up
   to the cent for one contract) taken off.
B. Track-record consensus: each tracked account is scored only on its bets settled BEFORE the
   new fill (>= 20 settled, at its own price). When an account in the top / bottom fifth of
   that score takes a side, FOLLOW / FADE it at the ask we saw ~15 s later (Copycat's snap)
   or, if none, their price +1c. One entry per market and side (the first signal).
Train = trades before 2026-11-12 12:00 UTC, holdout = after. Grouped by event.
"""
import collections, datetime, glob, gzip, json, math, re, sys
import numpy as np, pandas as pd

d, mfile = sys.argv[1:3]
M = json.load(gzip.open(mfile, "rt"))
SPLIT = datetime.datetime(2026, 11, 12, 12, tzinfo=datetime.timezone.utc).timestamp()


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def group(tk):
    s = tk.split("-")[0]
    if s.endswith("15M"):
        return "15-min"
    if s.startswith("KXMVE"):
        return "parlay"
    if re.match(r"KX(NBA|NFL|NHL|MLB|NCAA|ATP|WTA|ITF|EPL|UCL|MLS|UFC|PGA|SOCCER|LALIGA|SERIEA|BUNDES|LIGUE|F1|BOX)", s):
        return "sports"
    if re.match(r"KX(BTC|ETH|SOL|XRP|DOGE)", s):
        return "crypto other"
    return "other"


def fee(p):
    return math.ceil(100 * 0.07 * p * (1 - p) - 1e-9) / 100


def score(side, px, res):
    return 100 * ((1 - px) if side == res else -px) - 100 * fee(px)


def rd(kind):
    for f in sorted(glob.glob(f"{d}/*.jsonl.gz")):
        try:
            for line in gzip.open(f, "rt"):
                if f'"{kind}"' not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("kind") == kind:
                    yield r
        except EOFError:
            pass


def clus(p, g):
    s = pd.DataFrame({"p": p, "g": g}).groupby("g").p.agg(["sum", "count"])
    m = np.mean(p); k = len(s)
    se = np.sqrt(((s["sum"] - m * s["count"]) ** 2).sum() * k / max(k - 1, 1)) / len(p)
    return m, se, k


def report(df, label):
    rows = []
    for (g, sig), x in df.groupby(["group", "signal"]):
        for per, y in (("train", x[x.t < SPLIT]), ("holdout", x[x.t >= SPLIT])):
            if len(y) < 20:
                continue
            m, se, k = clus(y.pnl.values, y.event.values)
            rows.append(dict(group=g, signal=sig, period=per, n=len(y), events=k, c_per_ct=round(m, 1), se=round(se, 1)))
    print(f"\n== {label}\n" + pd.DataFrame(rows).to_string(index=False))


# ---- A. big bets
seen, A = set(), []
for r in rd("whale"):
    if r.get("role") != "taker" or not r.get("trade_time") or r["trade_id"] in seen:
        continue
    seen.add(r["trade_id"])
    m = M.get(r["ticker"]) or {}
    if m.get("result") not in ("yes", "no"):
        continue
    px = float(r["paid"])
    if not 0.03 <= px <= 0.97:
        continue
    other = "no" if r["side"] == "yes" else "yes"
    who = "anon" if r.get("anon") else ("new acct" if r.get("new") else "named")
    for sig, side, p in (("follow", r["side"], min(px + 0.01, 0.99)), ("fade", other, min(1 - px + 0.01, 0.99))):
        A.append(dict(t=ts(r["trade_time"]), group=group(r["ticker"]), signal=f"{sig} {who}", event=m.get("event_ticker"),
                      pnl=score(side, p, m["result"]), px=px))
A = pd.DataFrame(A).drop_duplicates()
report(A, "A. big bets, follow vs fade (cents per contract after taker fee)")
# by price band for the fade, holdout only, to see where any effect sits
A["band"] = pd.cut(A.px, [0, .2, .4, .6, .8, 1])
x = A[A.signal.str.startswith("fade")]
print("\nfade by their price, train | holdout:")
for (g, b), y in x.groupby(["group", "band"], observed=True):
    a, h = y[y.t < SPLIT], y[y.t >= SPLIT]
    if len(a) >= 30 and len(h) >= 30:
        print(f"  {g:12s} {str(b):11s} {clus(a.pnl.values, a.event.values)[0]:+6.1f} (n={len(a)}) | "
              f"{clus(h.pnl.values, h.event.values)[0]:+6.1f} ±{clus(h.pnl.values, h.event.values)[1]:.1f} (n={len(h)})")

# ---- B. track-record consensus (live-logged fills only)
snaps = {}
for r in rd("snap"):
    if r.get("delay") in (0, 15, 30, 60) and r.get("ask") is not None:
        snaps.setdefault(r["trade_id"], {})[r["delay"]] = (r.get("bid"), r.get("ask"))
F = {}
for r in rd("fill"):
    if r.get("nick") and r.get("trade_time"):
        F[(r["nick"], r["trade_id"])] = r
bets = []
for r in F.values():
    m = M.get(r["ticker"]) or {}
    if m.get("result") not in ("yes", "no") or not m.get("close_time"):
        continue
    yp = float(r["yes_price"]); side = r["side"]
    px = yp if side == "yes" else round(1 - yp, 4)
    if not 0.02 <= px <= 0.98:
        continue
    bets.append(dict(nick=r["nick"], t=ts(r["trade_time"]), settle=ts(m["close_time"]), ticker=r["ticker"],
                     event=m.get("event_ticker"), side=side, px=px, role=r["role"], live=not r.get("backfill"),
                     res=m["result"], tid=r["trade_id"], own=100 * ((1 - px) if side == m["result"] else -px)))
B = pd.DataFrame(bets).sort_values("t")
print(f"\nB: {len(B)} settled fills, {B.nick.nunique()} accounts, live {B.live.sum()}")
# each account's record from bets settled before t
hist = collections.defaultdict(list)          # nick -> list of (settle, own)
for nick, g in B.groupby("nick"):
    hist[nick] = (g.settle.values[np.argsort(g.settle.values)], np.sort(g.settle.values), g.own.values[np.argsort(g.settle.values)])
recs = []
for r in B[B.live].itertuples():
    st, _, own = hist[r.nick]
    n = np.searchsorted(st, r.t, side="left")
    if n < 20:
        continue
    recs.append((r.Index, own[:n].mean(), n))
R = pd.DataFrame(recs, columns=["i", "rec", "n_prior"]).set_index("i")
L = B.loc[R.index].join(R)
tr = L[L.t < SPLIT]
lo, hi = np.quantile(tr.rec, [0.2, 0.8])
print(f"record cut points from train: bottom fifth <= {lo:+.1f}c, top fifth >= {hi:+.1f}c")
out, used = [], set()
for r in L.itertuples():
    if r.rec >= hi:
        sig, side = "follow top fifth", r.side
    elif r.rec <= lo:
        sig, side = "fade bottom fifth", ("no" if r.side == "yes" else "yes")
    else:
        continue
    key = (sig, r.ticker, side)
    if key in used:
        continue
    used.add(key)
    sn = snaps.get(r.tid, {})
    q = next((sn[k] for k in (15, 30, 0, 60) if k in sn), None)
    if q and q[0] is not None and q[1] is not None:
        yb, ya = float(q[0]), float(q[1])
        p = ya if side == "yes" else round(1 - yb, 4)
    else:
        yp = r.px if r.side == "yes" else 1 - r.px
        p = (yp + 0.01) if side == "yes" else (1 - yp + 0.01)
    if not 0.02 <= p <= 0.98:
        continue
    out.append(dict(t=r.t, group=group(r.ticker), signal=sig, event=r.event, pnl=score(side, p, r.res)))
report(pd.DataFrame(out), "B. track-record consensus (cents per contract after taker fee, at the ask ~15 s later)")
