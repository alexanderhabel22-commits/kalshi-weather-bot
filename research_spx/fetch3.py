import json, time, urllib.request, sys, datetime as dt
B = "https://api.elections.kalshi.com/trade-api/v2"
def get(path):
    for i in range(6):
        try:
            with urllib.request.urlopen(B + path, timeout=30) as r:
                return json.load(r)
        except Exception:
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(path)

evs, cur = [], ""
while True:
    d = get("/events?series_ticker=KXINX&status=settled&limit=200&with_nested_markets=true" + (f"&cursor={cur}" if cur else ""))
    evs += d["events"]; cur = d.get("cursor")
    if not cur or not d["events"]: break
evs = [e for e in evs if e["event_ticker"].endswith("H1600")]
print("events", len(evs), file=sys.stderr)
import concurrent.futures as cf, threading
done={json.loads(l)["event"] for f in ("trades.jsonl","trades2.jsonl") for l in open(f)}
evs=[e for e in evs if e["event_ticker"] not in done]
out = open("trades3.jsonl", "w"); lk=threading.Lock()
def work(e):
    rows=[]
    mk = get(f"/historical/markets?event_ticker={e['event_ticker']}&limit=200")["markets"]
    for m in mk:
        ct = dt.datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
        # pre-open window: 13:30 UTC on close day minus ~18h (prev 4pm ET) -> use 20:00 UTC prev day .. 14:30 UTC (9:30 ET in EST; 13:30 in EDT)
        day = ct.date()
        lo = int(dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc).timestamp()) - 2 * 3600 - 55 * 60  # 21:05 UTC prev day
        hi = int(dt.datetime(day.year, day.month, day.day, 13, 25, tzinfo=dt.timezone.utc).timestamp())  # before 9:30 ET either DST
        tr, c = [], ""
        while True:
            d = get(f"/historical/trades?ticker={m['ticker']}&min_ts={lo}&max_ts={hi}&limit=1000" + (f"&cursor={c}" if c else ""))
            tr += d["trades"]; c = d.get("cursor")
            if not c or not d["trades"]: break
        rows.append(json.dumps({"event": e["event_ticker"], "ticker": m["ticker"], "day": str(day), "strike_type": m.get("strike_type"),
                              "floor": m.get("floor_strike"), "cap": m.get("cap_strike"), "result": m.get("result"),
                              "exp": m.get("expiration_value"), "trades": [(t["created_time"], t.get("yes_price_dollars") or t.get("yes_price"), t.get("count_fp") or t.get("count"), t.get("taker_side")) for t in tr]}) + "\n")

    with lk:
        out.write("".join(rows)); out.flush()
with cf.ThreadPoolExecutor(8) as ex: list(ex.map(work, evs))
print("done", file=sys.stderr)
