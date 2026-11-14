import json, time, urllib.request, urllib.error, sys, datetime as dt
B="https://api.elections.kalshi.com/trade-api/v2"; last=[0.0]
def get(path):
    for i in range(12):
        w=0.4-(time.time()-last[0])
        if w>0: time.sleep(w)
        last[0]=time.time()
        try:
            with urllib.request.urlopen(B+path,timeout=60) as r: return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (400,404): return None
            time.sleep(1.5*(i+1))
        except Exception: time.sleep(2*(i+1))
    raise RuntimeError(path)
P=lambda s: int(dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp())
series,since,h0,h1,minvol=sys.argv[1],sys.argv[2],float(sys.argv[3]),float(sys.argv[4]),float(sys.argv[5])
mk,cur=[], ""
while True:
    d=get(f"/historical/markets?series_ticker={series}&limit=1000"+(f"&cursor={cur}" if cur else ""))
    if not d: break
    mk+=d["markets"]; cur=d.get("cursor")
    if not cur or not d["markets"]: break
mk=[m for m in mk if m["close_time"]>=since and m.get("result") in("yes","no") and m.get("strike_type")=="between" and float(m.get("volume_fp") or 0)>=minvol]
mk.sort(key=lambda m:m["close_time"])
print(series,len(mk),"hist markets",len({m['event_ticker'] for m in mk}),"events",file=sys.stderr)
out=open(f"h_{series}.jsonl","w")
for k,m in enumerate(mk):
    c=P(m["close_time"]); lo=int(c-h1*3600); hi=int(c-h0*3600)
    tr,cu=[], ""
    while True:
        d=get(f"/historical/trades?ticker={m['ticker']}&min_ts={lo}&max_ts={hi}&limit=1000"+(f"&cursor={cu}" if cu else ""))
        if not d: break
        tr+=d["trades"]; cu=d.get("cursor")
        if not cu or not d["trades"]: break
    out.write(json.dumps({"event":m["event_ticker"],"ticker":m["ticker"],"close":m["close_time"],"floor":m.get("floor_strike"),"cap":m.get("cap_strike"),
        "result":m["result"],"vol":m.get("volume_fp"),"tr":[(t["created_time"],float(t["yes_price_dollars"]),float(t["count_fp"]),t["taker_side"]) for t in tr]})+"\n")
    if k%200==0: print(series,k,len(mk),file=sys.stderr); out.flush()
print(series,"done",file=sys.stderr)
