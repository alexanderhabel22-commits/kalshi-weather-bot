import csv, gzip, math, collections
from datetime import datetime, timezone, timedelta, date
import openpyxl
nc = collections.defaultdict(list)
for r in csv.DictReader(open("/tmp/claude-0/gdp/gdpnow.csv")):
    nc[r["quarter"]].append((date.fromisoformat(r["date"]), float(r["gdp"])))
for k in nc: nc[k].sort()
wb = openpyxl.load_workbook("/tmp/claude-0/gdp/GDPTrackingModelDataAndForecasts.xlsx", read_only=True, data_only=True)
tr = [(r[0].date(), r[5]) for r in list(wb["TrackRecord"].iter_rows(values_only=True))[1:] if r[0] and isinstance(r[5], (int, float))]
mk = [r for r in csv.DictReader(open("/tmp/claude-0/econ/markets.csv")) if r["series"] == "KXGDP" and r["strike_type"] == "greater" and r["floor"]]
want = {r["ticker"] for r in mk}
cand = collections.defaultdict(list)
with gzip.open("/tmp/claude-0/econ/candles.csv.gz", "rt") as fh:
    for r in csv.DictReader(fh):
        if r["ticker"] in want and r["bid"] and r["ask"]:
            cand[r["ticker"]].append((int(r["ts"]), float(r["bid"]), float(r["ask"])))
def Phi(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def fee(p): return math.ceil(0.07*p*(1-p)*100-1e-9)/100
def qend(close):  # quarter ending before the release
    d = close.date().replace(day=1) - timedelta(days=1)
    while d.month % 3: d = d.replace(day=1) - timedelta(days=1)
    return d
rows = []
for r in mk:
    close = datetime.fromisoformat(r["close"].replace("Z", "+00:00"))
    q = qend(close); key = q.isoformat()
    if key not in nc: continue
    errs = [e for qq, e in tr if qq < q][-40:]
    if len(errs) < 12: continue
    sd = (sum(e*e for e in errs)/len(errs))**.5
    cs = sorted(cand.get(r["ticker"], []))
    if cs and max(x[2] for x in cs) <= 0.0100001: cs = [(a, b*100, c*100) for a, b, c in cs]
    T = float(r["floor"]); out = r["result"] == "yes"
    for h in (168, 72, 24, 6):
        t = close.timestamp() - h*3600
        c = [x for x in cs if x[0] <= t]
        if not c or c[-1][0] < t-6*3600 or c[-1][2]-c[-1][1] > 0.15: continue
        vd = (datetime.fromtimestamp(t, timezone.utc) - timedelta(hours=20)).date()
        v = [x for x in nc[key] if x[0] <= vd]
        if not v: continue
        # error sd shrinks as release nears; scale by days left (crude: final-nowcast sd)
        p = 1 - Phi((T + 0.05 - v[-1][1]) / sd)
        rows.append(dict(ev=r["event"], h=h, out=out, bid=c[-1][1], ask=c[-1][2], mid=(c[-1][1]+c[-1][2])/2, p=p, T=T, nc=v[-1][1]))
print("rows", len(rows), "events", len({x["ev"] for x in rows}))
for h in (168, 72, 24, 6):
    xs = [x for x in rows if x["h"] == h and 0.03 < x["mid"] < 0.97]
    if xs: print(h, len(xs), "brier market", round(sum((x["mid"]-x["out"])**2 for x in xs)/len(xs), 4), "gdpnow", round(sum((x["p"]-x["out"])**2 for x in xs)/len(xs), 4))
for thr in (0.05, 0.1, 0.2):
    pnl = []
    for x in rows:
        if x["h"] not in (72, 24): continue
        if x["p"]-x["ask"] > thr: pnl.append(x["out"]-x["ask"]-fee(x["ask"]))
        elif x["bid"]-x["p"] > thr: pnl.append((1-x["out"])-(1-x["bid"])-fee(1-x["bid"]))
    if pnl: print("thr", thr, len(pnl), f"{sum(pnl)/len(pnl)*100:+.1f}c")
print("per event, thr 0.1, h in 72/24")
pe = collections.defaultdict(list)
for x in rows:
    if x["h"] not in (72, 24): continue
    if x["p"]-x["ask"] > 0.1: pe[x["ev"]].append(x["out"]-x["ask"]-fee(x["ask"]))
    elif x["bid"]-x["p"] > 0.1: pe[x["ev"]].append((1-x["out"])-(1-x["bid"])-fee(1-x["bid"]))
for k in sorted(pe): print(k, len(pe[k]), round(sum(pe[k]), 2))
for e in sorted({x["ev"] for x in rows}):
    x=[y for y in rows if y["ev"]==e and y["h"]==24]
    if x: print(e, "gdpnow", round(x[0]["nc"],2))
