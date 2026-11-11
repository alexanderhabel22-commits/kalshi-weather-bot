"""Novig public-data probe (research only, read-only, never trades).
Stage 1: download Novig daily trades/markets CSVs, summarise shape + volume by league."""
import csv, io, json, os, sys, collections, requests
OUT = sys.argv[1]; os.makedirs(OUT, exist_ok=True)
B = "https://data.novig.com/reporting/trade-data"
S = requests.Session(); S.headers["User-Agent"] = "research-probe"
def get(u):
    r = S.get(u, timeout=60); return r.status_code, r.content
log = open(f"{OUT}/probe.txt", "w")
def p(*a):
    print(*a); print(*a, file=log); log.flush()
c, b = get(f"{B}/index.json"); p("index", c, b[:600])
idx = json.loads(b) if c == 200 else {}
open(f"{OUT}/index.json", "wb").write(b)
dates = sorted({d for v in (idx.values() if isinstance(idx, dict) else []) if isinstance(v, list) for d in (x if isinstance(x, str) else json.dumps(x) for x in v)})
p("dates sample", dates[:3], dates[-3:])
days = ["2026-11-0%d" % i for i in range(2, 10)]
os.makedirs(f"{OUT}/raw", exist_ok=True)
for d in days:
    for f in ("markets.csv", "trades.csv"):
        c, b = get(f"{B}/{d}/{f}")
        p(d, f, c, len(b))
        if c != 200:
            p("  body", b[:300]); continue
        if len(b) < 15_000_000: open(f"{OUT}/raw/{d}_{f}", "wb").write(b)
        rows = list(csv.reader(io.StringIO(b.decode("utf-8", "replace"))))
        p("  header", rows[0] if rows else None, "rows", len(rows) - 1)
        for r in rows[1:4]: p("  ", r)
        if f == "markets.csv" and rows:
            h = rows[0]; iv = h.index("dailyVolume") if "dailyVolume" in h else None
            it = h.index("reportTicker") if "reportTicker" in h else None
            vol = collections.Counter(); n = collections.Counter()
            for r in rows[1:]:
                k = r[it].split("-")[0] if it is not None else "?"
                n[k] += 1
                try: vol[k] += float(r[iv] or 0)
                except Exception: pass
            p("  volume by prefix", vol.most_common(25)); p("  count", n.most_common(25))

# Stage 1b: Kalshi game-winner markets + trade tapes for the same days (for offline matching)
K = "https://api.elections.kalshi.com/trade-api/v2"
import time, datetime as dt
lo = int(dt.datetime(2026, 11, 2, 5, tzinfo=dt.timezone.utc).timestamp())
hi = int(dt.datetime(2026, 11, 10, 12, tzinfo=dt.timezone.utc).timestamp())
os.makedirs(f"{OUT}/kalshi", exist_ok=True)
def kget(path, **params):
    for a in range(5):
        try:
            r = S.get(K + path, params=params, timeout=30)
            if r.status_code == 429: time.sleep(2 + a * 2); continue
            return r.json()
        except Exception as e:
            p("kalshi err", path, e); time.sleep(2)
    return {}
for ser in ("KXNFLGAME", "KXNBAGAME", "KXNHLGAME", "KXNCAAFGAME"):
    mk, cur = [], None
    while True:
        j = kget("/markets", series_ticker=ser, min_close_ts=lo, max_close_ts=hi, limit=1000, **({"cursor": cur} if cur else {}))
        mk += j.get("markets", []); cur = j.get("cursor")
        if not cur: break
    p(ser, "markets", len(mk))
    json.dump(mk, open(f"{OUT}/kalshi/{ser}_markets.json", "w"))
    with open(f"{OUT}/kalshi/{ser}_trades.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["ticker", "created_time", "yes_price", "count", "taker_side"])
        for m in mk:
            cur, pages = None, 0
            while pages < 15:
                j = kget("/markets/trades", ticker=m["ticker"], limit=1000, **({"cursor": cur} if cur else {}))
                for t in j.get("trades", []):
                    w.writerow([t.get("ticker"), t.get("created_time"), t.get("yes_price"), t.get("count"), t.get("taker_side")])
                cur = j.get("cursor"); pages += 1
                if not cur: break
                time.sleep(0.05)
p("done")
