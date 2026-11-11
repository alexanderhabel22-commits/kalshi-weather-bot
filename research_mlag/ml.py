import csv,gzip,glob,sys,collections,statistics as st,bisect
from datetime import datetime
from zoneinfo import ZoneInfo
ET=ZoneInfo("America/New_York")
H=sys.argv[1]
cat={}
for f in glob.glob(H+"/*/series.csv"):
    for r in csv.DictReader(open(f)): cat[r["series"]]=r["category"]
mk={}
for f in glob.glob(H+"/*/sample.csv.gz"):
    for r in csv.DictReader(gzip.open(f,"rt")):
        if cat.get(r["series"])=="Mentions": mk[r["ticker"]]=r
print("mentions markets",len(mk),collections.Counter(m["result"] for m in mk.values()))
tr=collections.defaultdict(list)
for f in glob.glob(H+"/*/trades_*.csv.gz"):
    for r in csv.DictReader(gzip.open(f,"rt")):
        if r["ticker"] in mk: tr[r["ticker"]].append((int(r["t"]),float(r["yes_price"]),float(r["count"]),r["taker_side"]))
B=[(0,3),(3,10),(10,30),(30,60),(60,300),(300,900)]
w=csv.writer(open(H+"/../mlag.csv","w"))
w.writerow(["series","ticker","t0_et","pre","after_s","close_after_s","res","band_fills_total","band_fills_jump"]+[f"{k}_{a}_{b}" for a,b in B for k in ("ct","pnl")])
for tk,t in tr.items():
    m=mk[tk]; t.sort()
    # our band NO-maker fills: YES taker at yes 0.10-0.25
    band=lambda x: x[3]=="yes" and 0.10<=x[1]<=0.25
    tot=sum(x[2] for x in t if band(x))
    if m["result"]!="yes":
        w.writerow([m["series"],tk,"","","","","no",round(tot),0]+[0]*12); continue
    low=[i for i,x in enumerate(t) if x[1]<=0.60]
    if not low or low[-1]==len(t)-1: 
        w.writerow([m["series"],tk,"","","","","yes_nojump",round(tot),0]+[0]*12); continue
    i0=low[-1]+1; t0=t[i0][0]
    T=[x[0] for x in t]
    pre=[x[1] for x in t[bisect.bisect_left(T,t0-600):i0]]
    p_pre=st.median(pre) if pre else t[i0-1][1]
    jb=sum(x[2] for x in t if band(x) and t0-120<=x[0]<=t0+900)
    out=[]
    for a,b in B:
        ct=pnl=0
        for x in t[bisect.bisect_left(T,t0+a):bisect.bisect_left(T,t0+b)]:
            if x[1]<=0.85:
                ct+=x[2]; pnl+=x[2]*(1-x[1]-0.07*x[1]*(1-x[1]))
        out+=[round(ct),round(pnl,2)]
    w.writerow([m["series"],tk,datetime.fromtimestamp(t0,ET).strftime("%Y-%m-%d %H:%M:%S"),round(p_pre,2),t[-1][0]-t0,int(m["close_ts"])-t0,"yes",round(tot),round(jb)]+out)
