"""Probe Pyth's free Benchmarks API (TradingView shim) for metals/oil/crypto 1-minute history.
Read-only. usage: python research_spot/pyth.py OUT_DIR"""
import csv
import gzip
import sys
import time
from pathlib import Path

import requests

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
B = "https://benchmarks.pyth.network/v1/shims/tradingview"
H = "https://hermes.pyth.network"
S = requests.Session()
LOG = []


def log(*a):
    m = " ".join(str(x) for x in a)
    print(m, flush=True)
    LOG.append(m)


def get(url, params=None):
    for i in range(5):
        try:
            r = S.get(url, params=params, timeout=60)
            if r.status_code == 429:
                time.sleep(5 * (i + 1))
                continue
            if not r.ok:
                log("HTTP", r.status_code, url, str(params)[:100], r.text[:200])
                return None
            return r.json()
        except requests.RequestException as e:
            log("ERR", url, e)
            time.sleep(3)
    return None


QUERIES = ["XAG", "XAU", "XCU", "COPPER", "XPT", "XPD", "PLATINUM", "PALLADIUM", "WTI", "USOIL",
           "BRENT", "UKOIL", "NATGAS", "NGD", "BTC/USD", "ETH/USD"]
syms = {}
for q in QUERIES:
    js = get(f"{B}/search", {"query": q, "limit": 50}) or []
    for s in js:
        syms[s.get("symbol") or s.get("ticker")] = s
    log("search", q, [s.get("symbol") or s.get("ticker") for s in js][:30])
js = get(f"{H}/v2/price_feeds", {"asset_type": "metal"})
log("hermes metal feeds", [(x.get("attributes", {}).get("symbol"), x.get("id")) for x in (js or [])][:40])
js = get(f"{H}/v2/price_feeds", {"asset_type": "commodities"})
log("hermes commodity feeds", [(x.get("attributes", {}).get("symbol"), x.get("id")) for x in (js or [])][:60])

end = int(time.time())
start = end - 29 * 86400
want = [s for s in syms if s and any(k in s.upper() for k in
        ("XAG", "XAU", "XCU", "XPT", "XPD", "WTI", "USOIL", "BRENT", "UKOIL", "NGD", "NATGAS",
         "COPPER", "PLATINUM", "PALLADIUM"))]
log("fetching", want)
with gzip.open(OUT / "pyth.csv.gz", "wt", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["product", "t", "close"])
    for s in want:
        n, t = 0, start
        while t < end:
            t2 = min(t + 86400, end)
            js = get(f"{B}/history", {"symbol": s, "resolution": "1", "from": t, "to": t2})
            if js and js.get("s") == "ok":
                for tt, c in zip(js["t"], js["c"]):
                    w.writerow([s, tt, c])
                    n += 1
            t = t2
            time.sleep(0.3)
        log("history", s, n)
(OUT / "log.txt").write_text("\n".join(LOG) + "\n")
