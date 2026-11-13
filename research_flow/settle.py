"""Fetch settlement data for every ticker in the Copycat logs (copycat-data branch).
Research only: public read-only market data, never trades.
  python research_flow/settle.py <copycat dir> <out.json.gz>"""
import glob, gzip, json, sys, time
import requests

d, out = sys.argv[1:3]
tk = set()
for f in glob.glob(f"{d}/*.jsonl.gz"):
    try:
        for line in gzip.open(f, "rt"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") in ("fill", "whale") and r.get("ticker"):
                tk.add(r["ticker"])
    except EOFError:
        pass
tk = sorted(tk)
print("tickers", len(tk), flush=True)
B = "https://api.elections.kalshi.com/trade-api/v2/markets"
res = {}
s = requests.Session()
for i in range(0, len(tk), 100):
    ch = tk[i:i + 100]
    for a in range(6):
        try:
            r = s.get(B, params={"limit": 1000, "tickers": ",".join(ch)}, timeout=30)
            if r.status_code == 429:
                time.sleep(3 * (a + 1))
                continue
            r.raise_for_status()
            for m in r.json().get("markets", []):
                res[m["ticker"]] = {k: m.get(k) for k in ("result", "status", "close_time", "open_time",
                                                          "event_ticker", "expiration_value", "volume",
                                                          "category", "yes_bid", "yes_ask", "last_price")}
            break
        except Exception as e:  # noqa: BLE001
            print("retry", i, e, flush=True)
            time.sleep(3 * (a + 1))
    time.sleep(0.12)
    if i % 10000 == 0:
        print(i, len(res), flush=True)
json.dump(res, gzip.open(out, "wt"))
print("done", len(res), sum(1 for v in res.values() if v["result"] in ("yes", "no")))
