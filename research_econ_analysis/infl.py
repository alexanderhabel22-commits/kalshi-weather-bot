import csv, gzip, json, math, re, sys, collections
from datetime import datetime, timezone, timedelta
D = sys.argv[1]
MON = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}
# --- nowcasts: (year, month, measure) -> sorted [(vintage_date, value)], actual
NC, ACT = collections.defaultdict(list), {}
for c in json.load(open(f"{D}/cleveland_month.json")):
    y, m = map(int, c["chart"]["subcaption"].split("-"))
    for s in c["dataset"]:
        name = s["seriesname"]
        for p in s["data"]:
            if not p.get("value"):
                continue
            lab = p["tooltext"].split("{br}")[1]
            mm, dd = map(int, lab.split("/"))
            yy = y + 1 if mm < m else y
            dt = datetime(yy, mm, dd).date()
            meas = name.replace("Actual ", "").replace(" Inflation", "")
            meas = {"Core CPI": "CORE", "CPI": "CPI", "PCE": "PCE", "Core PCE": "COREPCE"}[meas]
            if name.startswith("Actual"):
                ACT[(y, m, meas)] = float(p["value"])
            else:
                NC[(y, m, meas)].append((dt, float(p["value"])))
for k in NC: NC[k].sort()
SER = {"KXCPI": "CPI", "KXCPICORE": "CORE", "KXPCECORE": "COREPCE"}
# --- markets
mk = [r for r in csv.DictReader(open(f"{D}/markets.csv")) if r["series"] in SER]
cand = collections.defaultdict(list)
want = {r["ticker"] for r in mk}
with gzip.open(f"{D}/candles.csv.gz", "rt") as fh:
    for r in csv.DictReader(fh):
        if r["ticker"] in want and r["bid"] and r["ask"]:
            cand[r["ticker"]].append((int(r["ts"]), float(r["bid"]), float(r["ask"]), float(r["volume"] or 0)))
for k in cand:
    cand[k].sort()
    if max(x[2] for x in cand[k]) <= 0.0100001:  # historical candles: dollars mislabeled -> rescale
        cand[k] = [(a, b * 100, c * 100, v) for a, b, c, v in cand[k]]

def tgt(ev):
    mobj = re.search(r"-(\d\d)([A-Z]{3})", ev)
    return 2000 + int(mobj.group(1)), MON[mobj.group(2)]

def Phi(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def fee(p, mult=0.07): return math.ceil(mult * p * (1 - p) * 100 - 1e-9) / 100

# nowcast error stats per measure, from Cleveland's own history (nowcast day before release vs actual)
def last_before(key, d):
    v = [x for x in NC.get(key, []) if x[0] < d]
    return v[-1] if v else None

def err_hist(meas, before_year_month):
    e = []
    for (y, m, ms), a in ACT.items():
        if ms != meas or (y, m) >= before_year_month: continue
        v = NC.get((y, m, ms))
        if v: e.append(a - v[-1][1])
    return e

HORIZ = [168, 72, 24, 6, 1]
rows = []
for r in mk:
    if r["strike_type"] != "greater" or r["floor"] == "": continue
    y, m = tgt(r["event"])
    meas = SER[r["series"]]
    close = datetime.fromisoformat(r["close"].replace("Z", "+00:00"))
    e = err_hist(meas, (y, m))
    if len(e) < 24: continue
    e = e[-60:]
    bias = sum(e) / len(e); sd = (sum((x - bias) ** 2 for x in e) / (len(e) - 1)) ** .5
    T = float(r["floor"]); out = 1 if r["result"] == "yes" else 0
    cs = cand.get(r["ticker"], [])
    for h in HORIZ:
        t = close.timestamp() - h * 3600
        c = [x for x in cs if x[0] <= t]
        if not c or c[-1][0] < t - 6 * 3600: continue
        _, bid, ask, _ = c[-1]
        if ask - bid > 0.15 or ask <= 0 or bid >= 1: continue
        vd = datetime.fromtimestamp(t, timezone.utc) - timedelta(hours=16)  # vintage published by ~11am ET prior day
        nv = last_before((y, m, meas), vd.date())
        if not nv: continue
        mu = nv[1] + bias
        p = 1 - Phi((T + 0.05 - mu) / sd)
        rows.append(dict(s=r["series"], ev=r["event"], T=T, h=h, out=out, bid=bid, ask=ask, mid=(bid + ask) / 2, p=p, nc=nv[1], sd=sd, ym=(y, m)))

print("rows", len(rows), "events", len({x['ev'] for x in rows}))
def brier(xs, k): return sum((x[k] - x["out"]) ** 2 for x in xs) / len(xs)
for s in SER:
    for h in HORIZ:
        xs = [x for x in rows if x["s"] == s and x["h"] == h and 0.03 < x["mid"] < 0.97]
        if len(xs) < 20: continue
        print(f"{s:10s} h={h:3d} n={len(xs):4d} ev={len({x['ev'] for x in xs}):3d} brier market={brier(xs,'mid'):.4f} nowcast={brier(xs,'p'):.4f}")

# trading sim: take toward the model when edge > thr after taker fee
print("\nTaker sim (buy at ask, pay fee), by threshold, all horizons pooled, 1 trade per market-horizon")
for s in list(SER) + ["ALL"]:
    for thr in (0.05, 0.10, 0.15, 0.20):
        pnl = []; evs = set()
        for x in rows:
            if s != "ALL" and x["s"] != s: continue
            if x["h"] not in (24, 72): continue
            if x["p"] - x["ask"] > thr:
                pnl.append(x["out"] - x["ask"] - fee(x["ask"])); evs.add(x['ev'])
            elif (1 - x["p"]) - (1 - x["bid"]) > thr:
                pnl.append((1 - x["out"]) - (1 - x["bid"]) - fee(1 - x["bid"])); evs.add(x['ev'])
        if pnl:
            n = len(pnl); mu = sum(pnl) / n; sdv = (sum((v - mu) ** 2 for v in pnl) / max(n - 1, 1)) ** .5
            print(f"{s:10s} thr={thr:.2f} trades={n:4d} events={len(evs):3d} avg={mu*100:+.1f}c  se~{sdv/math.sqrt(max(len(evs),1))*100:.1f}c")
json.dump(rows, open("/tmp/claude-0/ana/infl_rows.json", "w"), default=str)
