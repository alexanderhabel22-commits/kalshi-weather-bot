"""Research probe for Kalshi hourly temperature markets (Weather Index).

Runs on GitHub Actions (the Claude sandbox can't reach Kalshi/NOAA).
Read-only: public market data and public weather data. Never trades.
Writes JSON/CSV files into the output folder given as argv[1].
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

K = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = ["KXTEMPNYCHS", "KXTEMPMIAH", "KXTEMPCHIHS", "KXTEMPLAXHS"]
MEMBERS = {
    "KXTEMPMIAH": ["MIA", "OPF", "FLL", "FXE", "PMP"],
    "KXTEMPNYCHS": ["LGA", "EWR", "TEB", "CDW", "HPN", "FRG", "SMQ", "ISP"],
    "KXTEMPCHIHS": ["MDW", "ORD", "PWK", "DPA", "ARR"],
    "KXTEMPLAXHS": ["HHR", "LAX", "SMO", "LGB", "SNA"],
}
S = requests.Session()
S.headers["User-Agent"] = "kalshi-weather-research/0.1"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
OUT.mkdir(parents=True, exist_ok=True)
LOG = []


def get(url, params=None, tries=4):
    for i in range(tries):
        try:
            r = S.get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 * (i + 1))
                continue
            return r
        except requests.RequestException as e:
            LOG.append(f"ERR {url}: {e}")
            time.sleep(2)
    return None


def save(name, obj):
    (OUT / name).write_text(json.dumps(obj, indent=1, default=str))


def section(fn):
    t0 = time.time()
    try:
        fn()
        LOG.append(f"OK {fn.__name__} {time.time()-t0:.0f}s")
    except Exception:
        LOG.append(f"FAIL {fn.__name__}\n{traceback.format_exc()}")


def kalshi_series():
    out = {}
    for s in SERIES:
        r = get(f"{K}/series/{s}")
        out[s] = r.json() if r is not None and r.ok else (r.status_code if r is not None else None)
    save("series.json", out)


def kalshi_markets():
    """All markets (open + settled) for each series, paginated."""
    for s in SERIES:
        allm = []
        for status in ("open", "closed", "settled"):
            cur = None
            for _ in range(200):
                p = {"series_ticker": s, "status": status, "limit": 1000}
                if cur:
                    p["cursor"] = cur
                r = get(f"{K}/markets", p)
                if r is None or not r.ok:
                    LOG.append(f"markets {s} {status}: {r.status_code if r is not None else 'none'}")
                    break
                js = r.json()
                allm += js.get("markets") or []
                cur = js.get("cursor")
                if not cur:
                    break
        # historical archive (older settled markets)
        cur = None
        for _ in range(100):
            p = {"series_ticker": s, "limit": 1000}
            if cur:
                p["cursor"] = cur
            r = get(f"{K}/historical/markets", p)
            if r is None or not r.ok:
                LOG.append(f"hist markets {s}: {r.status_code if r is not None else 'none'}")
                break
            js = r.json()
            ms = js.get("markets") or []
            for m in ms:
                m["_hist"] = True
            allm += ms
            cur = js.get("cursor")
            if not cur:
                break
        save(f"markets_{s}.json", allm)
        LOG.append(f"{s}: {len(allm)} markets")


def kalshi_books():
    """Current order books for every open market."""
    books = {}
    for s in SERIES:
        r = get(f"{K}/markets", {"series_ticker": s, "status": "open", "limit": 200})
        if r is None or not r.ok:
            continue
        for m in r.json().get("markets") or []:
            b = get(f"{K}/markets/{m['ticker']}/orderbook")
            books[m["ticker"]] = {"market": m, "book": b.json() if b is not None and b.ok else None}
    save("books_now.json", {"fetched": datetime.now(timezone.utc).isoformat(), "books": books})


def kalshi_trades():
    """Recent trades per series (last ~3 days) to see volume by minutes-to-close."""
    since = int((datetime.now(timezone.utc) - timedelta(days=3)).timestamp())
    for s in SERIES:
        r = get(f"{K}/markets", {"series_ticker": s, "status": "settled", "limit": 1000,
                                 "min_close_ts": since})
        ms = (r.json().get("markets") or []) if r is not None and r.ok else []
        trades = []
        for m in ms[:400]:
            cur = None
            for _ in range(20):
                p = {"ticker": m["ticker"], "limit": 1000}
                if cur:
                    p["cursor"] = cur
                t = get(f"{K}/markets/trades", p)
                if t is None or not t.ok:
                    break
                js = t.json()
                trades += js.get("trades") or []
                cur = js.get("cursor")
                if not cur:
                    break
        save(f"trades_{s}.json", trades)
        LOG.append(f"trades {s}: {len(ms)} mkts {len(trades)} trades")


def candles_sample():
    """1-minute candlesticks for a handful of recently settled markets."""
    out = {}
    since = int((datetime.now(timezone.utc) - timedelta(days=2)).timestamp())
    for s in SERIES:
        r = get(f"{K}/markets", {"series_ticker": s, "status": "settled", "limit": 200,
                                 "min_close_ts": since})
        ms = (r.json().get("markets") or []) if r is not None and r.ok else []
        for m in ms[:30]:
            ct = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
            ot = datetime.fromisoformat(m["open_time"].replace("Z", "+00:00"))
            c = get(f"{K}/series/{s}/markets/{m['ticker']}/candlesticks",
                    {"start_ts": int(ot.timestamp()), "end_ts": int(ct.timestamp()) + 60,
                     "period_interval": 1})
            out[m["ticker"]] = c.json() if c is not None and c.ok else (c.status_code if c is not None else None)
    save("candles_sample.json", out)


def weather_sources():
    """What public station data exists and how fresh it is right now."""
    now = datetime.now(timezone.utc)
    res = {"probe_time": now.isoformat()}
    # NWS API latest obs (5-min HF-METAR rows + hourly METARs)
    nws = {}
    for s, ids in MEMBERS.items():
        for st in ids:
            r = get(f"https://api.weather.gov/stations/K{st}/observations", {"limit": 15})
            if r is None or not r.ok:
                nws[st] = r.status_code if r is not None else None
                continue
            rows = []
            for f in r.json().get("features", []):
                p = f["properties"]
                rows.append([p.get("timestamp"), (p.get("temperature") or {}).get("value"),
                             p.get("rawMessage", "")[:80]])
            nws[st] = rows
    res["nws"] = nws
    # aviationweather METAR
    ids = ",".join("K" + x for v in MEMBERS.values() for x in v)
    r = get("https://aviationweather.gov/api/data/metar", {"ids": ids, "format": "json", "hours": 2})
    res["awc"] = r.json() if r is not None and r.ok else (r.status_code if r is not None else None)
    # IEM 1-minute archive: latest few hours available, for one station per city
    iem = {}
    for s, ids in MEMBERS.items():
        st = ids[0]
        for back in (1, 2, 3, 5, 8):
            d = now - timedelta(days=back)
            r = get("https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py",
                    {"station": st, "vars": "tmpf", "sts": d.strftime("%Y-%m-%dT00:00Z"),
                     "ets": d.strftime("%Y-%m-%dT01:00Z"), "sample": "1min", "what": "view",
                     "tz": "UTC"})
            txt = r.text[:600] if r is not None else None
            iem[f"{st}-{back}d"] = txt
            if txt and txt.count("\n") > 5:
                break
    res["iem1min"] = iem
    # IEM current obs (realtime METAR collection incl. 5-min?)
    r = get("https://mesonet.agron.iastate.edu/api/1/currents.json", {"network": "NY_ASOS"})
    res["iem_currents_ny"] = r.json() if r is not None and r.ok else (r.status_code if r is not None else None)
    save("weather_sources.json", res)


def main():
    for fn in (kalshi_series, kalshi_books, candles_sample, kalshi_markets, kalshi_trades,
               weather_sources):
        section(fn)
    (OUT / "log.txt").write_text("\n".join(LOG))
    print("\n".join(LOG))


if __name__ == "__main__":
    main()
