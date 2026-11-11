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
