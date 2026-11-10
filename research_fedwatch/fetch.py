"""Fed funds futures (FedWatch-style) vs Kalshi Fed markets: data pull (read-only).

Research only. Never trades, never alerts. Saves raw responses into argv[1]:
  zq/<source>_<sym>.json   daily history of each 30-day fed funds futures contract
  effr.json                NY Fed effective fed funds rate history
  polymarket/*.json        Polymarket Fed-decision price history (second outside view)
  probes.txt               status of every source tried
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
(OUT / "zq").mkdir(parents=True, exist_ok=True)
(OUT / "polymarket").mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64) research"
PROBES: list[str] = []
MONTHS = "FGHJKMNQUVXZ"


def get(url, params=None, headers=None, tries=4):
    err = None
    for i in range(tries):
        try:
            r = S.get(url, params=params, headers=headers, timeout=40)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(3 * (i + 1))
                continue
            return r
        except requests.RequestException as e:
            err = e
            time.sleep(3 * (i + 1))
    PROBES.append(f"FAIL {url} {err}")
    return None


def save(path, r):
    Path(path).write_bytes(r.content)


def contracts():
    for yy in range(21, 28):
        for m in MONTHS:
            yield f"ZQ{m}{yy:02d}"


def yahoo():
    n = 0
    for c in contracts():
        sym = f"{c}.CBT"
        r = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                {"period1": 1577836800, "period2": int(time.time()), "interval": "1d"})
        ok = r is not None and r.ok and b'"timestamp"' in r.content
        if ok:
            save(OUT / "zq" / f"yahoo_{c}.json", r)
            n += 1
        PROBES.append(f"yahoo {sym} {r.status_code if r is not None else 'none'} ok={ok}")
        time.sleep(0.4)
    return n


def barchart():
    n = 0
    for c in contracts():
        r = get("https://www.barchart.com/proxies/timeseries/queryeod.ashx",
                {"symbol": c, "data": "daily", "maxrecords": "1500", "volume": "contract",
                 "order": "asc", "dividends": "false", "backadjust": "false"},
                headers={"Referer": "https://www.barchart.com/"})
        ok = r is not None and r.ok and len(r.content) > 200 and b"," in r.content[:200]
        if ok:
            save(OUT / "zq" / f"barchart_{c}.csv", r)
            n += 1
        PROBES.append(f"barchart {c} {r.status_code if r is not None else 'none'} ok={ok} len={len(r.content) if r is not None else 0}")
        time.sleep(0.6)
    return n


def stooq():
    n = 0
    for c in ["zq.f"] + [c.lower() + ".f" for c in contracts()][:6]:
        r = get("https://stooq.com/q/d/l/", {"s": c, "i": "d"})
        ok = r is not None and r.ok and r.content.startswith(b"Date")
        if ok:
            save(OUT / "zq" / f"stooq_{c}.csv", r)
            n += 1
        PROBES.append(f"stooq {c} {r.status_code if r is not None else 'none'} ok={ok}")
    return n


def effr():
    r = get("https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json",
            {"startDate": "2020-01-01", "endDate": time.strftime("%Y-%m-%d")})
    PROBES.append(f"nyfed effr {r.status_code if r is not None else 'none'}")
    if r is not None and r.ok:
        save(OUT / "effr.json", r)
    r = get("https://fred.stlouisfed.org/graph/fredgraph.csv", {"id": "DFEDTARU"})
    PROBES.append(f"fred DFEDTARU {r.status_code if r is not None else 'none'}")
    if r is not None and r.ok:
        save(OUT / "fred_DFEDTARU.csv", r)


def polymarket():
    found = []
    for q in ["fed decision", "fed interest rates", "fomc"]:
        for closed in ("true", "false"):
            r = get("https://gamma-api.polymarket.com/events",
                    {"limit": 500, "closed": closed, "tag_slug": "fed-rates"})
            if r is None or not r.ok:
                PROBES.append(f"polymarket events {q} {r.status_code if r is not None else 'none'}")
                continue
            for ev in r.json():
                t = (ev.get("title") or "").lower()
                if "fed" in t and ("decision" in t or "rates" in t or "interest" in t):
                    found.append(ev)
        break
    seen = {}
    for ev in found:
        seen[ev.get("id")] = ev
    (OUT / "polymarket" / "events.json").write_text(json.dumps(list(seen.values())))
    PROBES.append(f"polymarket fed events {len(seen)}")
    n = 0
    for ev in seen.values():
        for m in ev.get("markets") or []:
            toks = m.get("clobTokenIds")
            try:
                toks = json.loads(toks) if isinstance(toks, str) else toks
            except Exception:
                toks = None
            if not toks:
                continue
            r = get("https://clob.polymarket.com/prices-history",
                    {"market": toks[0], "interval": "max", "fidelity": 60})
            if r is not None and r.ok:
                (OUT / "polymarket" / f"{m.get('id')}.json").write_text(
                    json.dumps({"q": m.get("question"), "end": m.get("endDate"),
                                "outcome": m.get("outcomePrices"), "hist": r.json()}))
                n += 1
            time.sleep(0.2)
    PROBES.append(f"polymarket histories {n}")


def main():
    effr()
    for name, fn in [("yahoo", yahoo), ("barchart", barchart), ("stooq", stooq), ("poly", polymarket)]:
        try:
            PROBES.append(f"== {name}: {fn()}")
        except Exception as e:  # keep going, record it
            PROBES.append(f"== {name} ERROR {e!r}")
        (OUT / "probes.txt").write_text("\n".join(PROBES))
    print("\n".join(p for p in PROBES if p.startswith("==") or "nyfed" in p or "fred" in p))


if __name__ == "__main__":
    main()
