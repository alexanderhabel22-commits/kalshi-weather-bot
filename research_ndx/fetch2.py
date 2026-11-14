# markets via series listing, then batched candlesticks over the last H hours before close
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
            if e.code==400: print("400",path[:200],e.read()[:300],file=sys.stderr); return None
            time.sleep(1.5*(i+1))
        except Exception: time.sleep(2*(i+1))
    raise RuntimeError(path)
P=lambda s: int(dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp())
series,since,H,per=sys.argv[1],sys.argv[2],float(sys.argv[3]),int(sys.argv[4])
every=int(sys.argv[5]) if len(sys.argv)>5 else 1
mk,cur=[], ""
while True:
    d=get(f"/markets?series_ticker={series}&status=settled&limit=1000"+(f"&cursor={cur}" if cur else ""))
    mk+=d["markets"]; cur=d.get("cursor")
    if not cur or not d["markets"] or min(m["close_time"] for m in d["markets"])<since: break
mk=[m for m in mk if m["close_time"]>=since and m.get("result") in("yes","no") and float(m.get("volume_fp") or 0)>0]
evs=sorted({m["event_ticker"] for m in mk}); keep=set(evs[::every]); mk=[m for m in mk if m["event_ticker"] in keep]
print(series,len(mk),"markets",len(keep),"events",file=sys.stderr)
out=open(f"c_{series}.jsonl","w")
ncand=int(H*60/per)+2; bs=max(1,min(100,9000//ncand))
mk.sort(key=lambda m:m["close_time"])
# group by close time so one start/end works
i=0
while i<len(mk):
    grp=[mk[i]]; j=i+1
    while j<len(mk) and len(grp)<bs and mk[j]["close_time"]==mk[i]["close_time"]: grp.append(mk[j]); j+=1
    c=P(grp[0]["close_time"]); st=max(c-int(H*3600), min(P(m["open_time"]) for m in grp))
    d=get(f"/markets/candlesticks?market_tickers={','.join(m['ticker'] for m in grp)}&start_ts={st}&end_ts={c}&period_interval={per}")
    cs={x["market_ticker"]:x["candlesticks"] for x in (d or {}).get("markets",[])} if d else {}
    for m in grp:
        rows=[]
        for k in cs.get(m["ticker"],[]):
            ph=k["price"].get("high_dollars"); 
            rows.append((k["end_period_ts"], float(ph) if ph else None, float(k["volume_fp"] or 0),
                         float(k["yes_bid"].get("close_dollars") or 0), float(k["yes_ask"].get("close_dollars") or 0)))
        out.write(json.dumps({"event":m["event_ticker"],"ticker":m["ticker"],"close":m["close_time"],"open":m["open_time"],"st":m.get("strike_type"),
            "floor":m.get("floor_strike"),"cap":m.get("cap_strike"),"result":m["result"],"c":rows})+"\n")
    i=j
    if (i//bs)%20==0: print(series,i,len(mk),file=sys.stderr); out.flush()
print(series,"done",file=sys.stderr)
