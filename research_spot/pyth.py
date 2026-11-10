"""Probe free real-time/historical price sources for the 15-minute commodity markets, and print
Kalshi's own settlement rules for each 15-minute series. Read-only; never trades.
usage: python research_spot/pyth.py OUT_DIR"""
import csv
import gzip
import json
import sys
import time
from pathlib import Path

import requests

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
BM = "https://benchmarks.pyth.network"
H = "https://hermes.pyth.network"
K = "https://api.elections.kalshi.com/trade-api/v2"
S = requests.Session()
LOG = []


def log(*a):
    m = " ".join(str(x) for x in a)
    print(m, flush=True)
    LOG.append(m)


def get(url, params=None, quiet=False):
    for i in range(5):
        try:
            r = S.get(url, params=params, timeout=60)
            if r.status_code == 429:
                time.sleep(5 * (i + 1))
                continue
            if not r.ok:
                if not quiet:
                    log("HTTP", r.status_code, url, str(params)[:100], r.text[:200])
                return None
            return r.json()
        except (requests.RequestException, ValueError) as e:
            log("ERR", url, e)
            time.sleep(3)
    return None


# 1) Kalshi's own rules for each 15-minute series (settlement source)
for s in ["KXBTC15M", "KXETH15M", "KXSILVER15M", "KXGOLD15M", "KXCOPPER15M", "KXPLATINUM15M",
          "KXPALLADIUM15M", "KXWTI15M", "KXBRENT15M", "KXNATGAS15M"]:
    js = get(f"{K}/series/{s}") or {}
    ser = js.get("series") or {}
    log("SERIES", s, json.dumps({k: ser.get(k) for k in ("settlement_sources", "contract_url",
                                                        "title")})[:600])
    js = get(f"{K}/markets", {"series_ticker": s, "limit": 1, "status": "settled"}) or {}
    for m in js.get("markets") or []:
        log("RULES", s, (m.get("rules_primary") or "")[:700], "||", (m.get("rules_secondary") or "")[:500])

# 2) Pyth feeds we care about
IDS = {}
for at in ("metal", "commodities", "crypto"):
    for x in get(f"{H}/v2/price_feeds", {"asset_type": at}) or []:
        sym = x.get("attributes", {}).get("symbol")
        if sym in ("Metal.XAG/USD", "Metal.XAU/USD", "Metal.XPT/USD", "Metal.XPD/USD",
                   "Commodities.Index.CU/USD", "Commodities.Index.COPPER/USD",
                   "Commodities.Index.WTI1M/USD", "Commodities.Index.PYTHOIL/USD",
                   "Commodities.Index.RWTI/USD", "Commodities.Index.BRENT1M/USD",
                   "Commodities.Index.NATGAS/USD", "Crypto.BTC/USD", "Crypto.ETH/USD"):
            IDS[sym] = x["id"]
            log("FEED", sym, x["id"], json.dumps(x.get("attributes"))[:300])

# 3) does historical lookup work? try Hermes and Benchmarks endpoints at one timestamp
t0 = int(time.time()) - 3 * 86400
for sym, fid in list(IDS.items())[:3]:
    for url in (f"{H}/v2/updates/price/{t0}", f"{BM}/v1/updates/price/{t0}"):
        js = get(url, {"ids[]": fid, "parsed": "true"}, quiet=True) or get(url, {"ids": fid, "parsed": "true"}, quiet=True)
        log("HIST-TRY", sym, url, str(js)[:300])
    js = get(f"{BM}/v1/shims/tradingview/history", {"symbol": sym, "resolution": "1",
                                                     "from": t0, "to": t0 + 3600}, quiet=True)
    log("TV-TRY", sym, str(js)[:300])

# 4) pull 1-minute history for the last ~10 days via Hermes/Benchmarks point lookups
#    (one call per minute, all feeds in one call)
end = int(time.time()) // 60 * 60 - 120
start = end - int(float(sys.argv[2]) if len(sys.argv) > 2 else 6) * 86400
ids = list(IDS.items())
n, ok_url = 0, None
with gzip.open(OUT / "pyth.csv.gz", "wt", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["product", "t", "close", "publish_time"])
    for t in range(start, end, 60):
        params = [("ids[]", fid) for _, fid in ids] + [("parsed", "true")]
        js = None
        for url in ([ok_url] if ok_url else [f"{BM}/v1/updates/price/{t}", f"{H}/v2/updates/price/{t}"]):
            js = get(url.format(t=t) if "{t}" in url else url, params, quiet=True)
            if js:
                ok_url = ok_url or url.replace(str(t), "{t}")
                break
        if ok_url and "{t}" in ok_url:
            ok_url = ok_url
        par = (js or {}).get("parsed") if isinstance(js, dict) else None
        if par is None and isinstance(js, list):
            par = [p for item in js for p in (item.get("parsed") or [])]
        name = {fid.lower(): s for s, fid in ids}
        for p in par or []:
            pr = p.get("price") or {}
            try:
                px = int(pr["price"]) * 10 ** int(pr["expo"])
            except (KeyError, ValueError, TypeError):
                continue
            w.writerow([name.get(p.get("id", "").lower(), p.get("id")), t, px, pr.get("publish_time")])
            n += 1
        if (t - start) % 21600 == 0:
            log("progress", t, n, ok_url)
        time.sleep(0.12)
log("rows", n)
(OUT / "log.txt").write_text("\n".join(LOG) + "\n")
