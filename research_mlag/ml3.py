# unbiased: detect jumps in ALL Mentions markets (either result): price <=0.50 (10-min median) then a trade >=0.85 within 60s of the first trade >=0.60.
import csv,gzip,glob,sys,collections,statistics as st,bisect
H=sys.argv[1]
cat={}
for f in glob.glob(H+"/*/series.csv"):
    for r in csv.DictReader(open(f)): cat[r["series"]]=r["category"]
mk={}
for f in glob.glob(H+"/*/sample.csv.gz"):
    for r in csv.DictReader(gzip.open(f,"rt")):
        if cat.get(r["series"])=="Mentions": mk[r["ticker"]]=r
tr=collections.defaultdict(list)
for f in glob.glob(H+"/*/trades_*.csv.gz"):
    for r in csv.DictReader(gzip.open(f,"rt")):
        if r["ticker"] in mk: tr[r["ticker"]].append((int(r["t"]),float(r["yes_price"]),float(r["count"])))
B=[(10,60),(60,300)]
res=collections.defaultdict(lambda:[0,0,0.0,0.0,0.0,0.0]); rows=[]
for tk,t in tr.items():
    m=mk[tk]; t.sort(); T=[x[0] for x in t]; R=1.0 if m["result"]=="yes" else 0.0
    last=0
    for i,x in enumerate(t):
        if x[0]<last or x[1]<0.60: continue
        pre=[y[1] for y in t[bisect.bisect_left(T,x[0]-600):i]]
        if len(pre)<2 or st.median(pre)>0.5: continue
        nxt=t[i:bisect.bisect_left(T,x[0]+60)]
        if max(y[1] for y in nxt)<0.85: continue
        t0=x[0]; last=t0+900
        fam="EARN" if ("EARN" in m["series"]) else "TRUMP" if "TRUMP" in m["series"] else "OTHER"
        a=res[(fam,m["result"])]; a[0]+=1
        for k,(lo,hi) in enumerate(B):
            for y in t[bisect.bisect_left(T,t0+lo):bisect.bisect_left(T,t0+hi)]:
                if y[1]<=0.85:
                    a[1+k]+=y[2]; a[3+k]+=y[2]*(R-y[1]-0.07*y[1]*(1-y[1]))
for k,v in sorted(res.items()): print(k,"jumps",v[0],"ct10-60s",round(v[1]),"ct1-5m",round(v[2]),"pnl10-60s",round(v[3]),"pnl1-5m",round(v[4]))
