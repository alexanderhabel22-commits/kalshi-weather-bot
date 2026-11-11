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
        if r["ticker"] in mk: tr[r["ticker"]].append((int(r["t"]),float(r["yes_price"]),float(r["count"]),r["taker_side"]))
# band fills by bucket relative to t0; price histogram of stale trades after 10s; per-jump totals after 10s
bb=collections.Counter(); hist=collections.Counter(); per=[]; months=collections.Counter(); per_e=[]
for tk,t in tr.items():
    m=mk[tk]; t.sort()
    if m["result"]!="yes": continue
    low=[i for i,x in enumerate(t) if x[1]<=0.60]
    if not low or low[-1]==len(t)-1: continue
    i0=low[-1]+1; t0=t[i0][0]; T=[x[0] for x in t]
    pre=[x[1] for x in t[bisect.bisect_left(T,t0-600):i0]]
    if (st.median(pre) if pre else t[i0-1][1])>0.5: continue
    for x in t:
        if x[3]=="yes" and 0.10<=x[1]<=0.25:
            dt=x[0]-t0
            k="pre<-120" if dt<-120 else "-120..0" if dt<0 else "0-3s" if dt<3 else "3-10s" if dt<10 else "10-60s" if dt<60 else "1-15m" if dt<900 else ">15m"
            bb[k]+=x[2]
    s=0;c=0
    for x in t[bisect.bisect_left(T,t0+10):bisect.bisect_left(T,t0+300)]:
        if x[1]<=0.85:
            hist[int(x[1]*10)/10]+=x[2]; s+=x[2]*(1-x[1]-0.07*x[1]*(1-x[1])); c+=x[2]
    per.append((s,c,m["series"],tk))
    if "EARNINGS" in m["series"]: per_e.append(s)
    from datetime import datetime
    months[datetime.utcfromtimestamp(t0).strftime("%Y-%m")]+=s
print("band NO fills in sharp-jump YES markets by time vs jump:",dict(bb))
print("stale price hist 10-300s:",sorted(hist.items()))
per.sort(reverse=True); tot=sum(p[0] for p in per)
print("jumps",len(per),"total 10-300s pnl",round(tot),"top10 share",round(sum(p[0] for p in per[:10])/tot,2),"jumps with >$5:",sum(p[0]>5 for p in per),">$1:",sum(p[0]>1 for p in per))
print(per[:8])
print("earnings jumps",len(per_e),"with >$5",sum(p>5 for p in per_e),"sum",round(sum(per_e)))
print("by month",sorted((k,round(v)) for k,v in months.items()))
