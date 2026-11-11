"""Research only: log 15-min BTC and silver quotes on Kalshi, Polymarket US and international
Polymarket side by side, plus each venue's settlement. Public read-only endpoints, no keys,
never trades.

Questions it answers later (analyze.py):
  - arbitrage: Kalshi Up ask + Polymarket US Down ask (or the reverse) below $1 after fees
  - is Polymarket US silver/BTC priced like Kalshi (longshot bias) or like international
  - does Kalshi's quote lag the busy international book

usage: python research_xv/log.py OUTDIR minutes [every_s]
"""
import calendar
import csv
import json
import os
import sys
import time

import requests

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
PMUS = "https://gateway.polymarket.us/v1"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
KSERIES = {"btc": "KXBTC15M", "silver": "KXSILVER15M"}
INTL = {"btc": "btc", "silver": "xag"}

S = requests.Session()
S.headers["User-Agent"] = "research-logger/1.0"
LOG = None


def log(*a):
    msg = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
    print(msg, flush=True)
    LOG.write(msg + "\n")
    LOG.flush()


def get(url, params=None, quiet=False):
    for i in range(3):
        try:
            r = S.get(url, params=params, timeout=10)
            if r.status_code in (429, 503):
                time.sleep(1 + 2 * i)
                continue
            if r.status_code >= 400:
                if not quiet:
                    log("http", r.status_code, url, params, r.text[:200])
                return None
            return r.json()
        except Exception as e:  # noqa: BLE001 - research logger, keep going
            if i == 2 and not quiet:
                log("ERR", url, e)
            time.sleep(1)
    return None


def num(v):
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def iso(v):
    return calendar.timegm(time.strptime(str(v)[:19], "%Y-%m-%dT%H:%M:%S"))


# ---------- Polymarket US discovery ----------
def pmus_updown(now, probe=False):
    """Open 15-min Up/Down markets on Polymarket US: {coin: [market, ...]}."""
    found, seen = {"btc": [], "silver": []}, 0
    types = {}
    for extra in ({}, {"includeHidden": "true"}):
        for off in range(0, 3000, 100):
            js = get(PMUS + "/markets", params={"active": "true", "closed": "false", "limit": 100,
                                                "offset": off, **extra}, quiet=True)
            ms = (js or {}).get("markets") or []
            seen += len(ms)
            for m in ms:
                apt = m.get("assetPriceTerms") or {}
                mt = apt.get("marketType")
                types[mt] = types.get(mt, 0) + 1
                if mt != "ASSET_PRICE_MARKET_TYPE_UP_DOWN":
                    continue
                sym = str((apt.get("asset") or {}).get("symbol", "")).lower()
                coin = "btc" if "btc" in sym else "silver" if sym in ("xag", "silver") else None
                try:
                    ws = iso(apt["windowStart"])
                    we = iso(apt["windowEnd"])
                except (KeyError, TypeError, ValueError):
                    continue
                if coin and 800 <= we - ws <= 1000 and we > now:
                    m["_ws"], m["_we"] = ws, we
                    if all(x.get("slug") != m.get("slug") for x in found[coin]):
                        found[coin].append(m)
            if len(ms) < 100:
                break
        if found["btc"] or found["silver"]:
            break
    if probe:
        log("pmus scanned", seen, "types", types,
            {k: [(m.get("slug"), m["_we"]) for m in v[:3]] for k, v in found.items()})
        for k, v in found.items():
            if v:
                m = v[0]
                log("pmus sample", k, json.dumps({x: m.get(x) for x in (
                    "slug", "id", "marketSides", "outcomes", "assetPriceTerms",
                    "orderPriceMinTickSize", "feeCoefficient")})[:1500])
    return found


def pmus_quote(slug):
    js = get(PMUS + f"/markets/{slug}/bbo", quiet=True)
    md = (js or {}).get("marketData") or {}
    return num(md.get("bestBid")), num(md.get("bestAsk")), num(md.get("bidDepth")), num(md.get("askDepth"))


# ---------- Kalshi ----------
def kalshi_open(series, now):
    js = get(KALSHI + "/markets", params={"series_ticker": series, "status": "open", "limit": 20})
    out = []
    for m in (js or {}).get("markets") or []:
        try:
            ct = iso(m["close_time"])
        except (KeyError, ValueError):
            continue
        if ct > now:
            m["_we"] = ct
            out.append(m)
    return out


def kalshi_quote(ticker):
    js = get(KALSHI + f"/markets/{ticker}", quiet=True)
    m = (js or {}).get("market") or {}
    return num(m.get("yes_bid_dollars")), num(m.get("yes_ask_dollars")), m.get("floor_strike")


# ---------- international ----------
def intl_market(coin, ws):
    js = get(GAMMA + "/markets", params={"slug": f"{INTL[coin]}-updown-15m-{int(ws)}"}, quiet=True)
    return (js or [None])[0] if js else None


def intl_quote(token):
    js = get(CLOB + "/book", params={"token_id": token}, quiet=True)
    if not js:
        return None, None
    bids = [float(x["price"]) for x in js.get("bids") or []]
    asks = [float(x["price"]) for x in js.get("asks") or []]
    return (max(bids) if bids else None), (min(asks) if asks else None)


def main():
    global LOG
    out, minutes = sys.argv[1], float(sys.argv[2])
    every = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
    os.makedirs(out, exist_ok=True)
    LOG = open(os.path.join(out, "log.txt"), "a")
    qf = open(os.path.join(out, "quotes.csv"), "a", newline="")
    qw = csv.writer(qf)
    qw.writerow(["ts", "coin", "window_end", "k_ticker", "k_bid", "k_ask", "k_strike",
                 "us_slug", "us_bid", "us_ask", "us_bid_depth", "us_ask_depth",
                 "intl_up_bid", "intl_up_ask"])
    mf = open(os.path.join(out, "markets.jsonl"), "a")
    t0 = time.time()
    cur = {}       # coin -> dict(we, k, us, intl_token)
    pending = []   # windows to settle: (due, coin, info)
    last_disc = 0
    first = True
    n = 0
    while time.time() - t0 < minutes * 60:
        now = time.time()
        need = any(c not in cur or cur[c]["we"] <= now for c in KSERIES)
        if need or now - last_disc > 120:
            last_disc = now
            us = pmus_updown(now, probe=first)
            for coin, series in KSERIES.items():
                ks = sorted(kalshi_open(series, now), key=lambda m: m["_we"])
                if not ks:
                    continue
                k = ks[0]
                we = k["_we"]
                if coin in cur and cur[coin]["we"] == we and cur[coin].get("us"):
                    continue
                usm = next((m for m in us.get(coin, []) if abs(m["_we"] - we) < 5), None)
                im = intl_market(coin, we - 900)
                tok = None
                if im:
                    outs = im.get("outcomes")
                    outs = json.loads(outs) if isinstance(outs, str) else outs or []
                    toks = im.get("clobTokenIds")
                    toks = json.loads(toks) if isinstance(toks, str) else toks or []
                    if len(outs) == 2 and len(toks) == 2:
                        tok = toks[0] if str(outs[0]).lower() == "up" else toks[1]
                if coin not in cur or cur[coin]["we"] != we:
                    if coin in cur:
                        pending.append((cur[coin]["we"] + 150, coin, cur[coin]))
                    log("window", coin, k["ticker"], "us", usm and usm.get("slug"),
                        "intl", im and im.get("slug"))
                cur[coin] = {"we": we, "k": k["ticker"], "us": usm and usm.get("slug"),
                             "us_meta": usm, "intl": im and im.get("slug"), "tok": tok}
            first = False
        for coin, c in cur.items():
            if c["we"] <= now:
                continue
            kb, ka, ks = kalshi_quote(c["k"])
            ub = ua = ud = uad = None
            if c["us"]:
                ub, ua, ud, uad = pmus_quote(c["us"])
            ib = ia = None
            if c["tok"]:
                ib, ia = intl_quote(c["tok"])
            qw.writerow([round(now, 1), coin, int(c["we"]), c["k"], kb, ka, ks, c["us"], ub, ua,
                         ud, uad, ib, ia])
            n += 1
        qf.flush()
        for item in [p for p in pending if p[0] <= now]:
            pending.remove(item)
            _, coin, c = item
            rec = {"coin": coin, "window_end": c["we"], "kalshi": None, "us": None, "intl": None}
            js = get(KALSHI + f"/markets/{c['k']}", quiet=True)
            if js:
                m = js.get("market") or {}
                rec["kalshi"] = {x: m.get(x) for x in ("ticker", "result", "floor_strike",
                                                       "expiration_value", "status")}
            if c["us"]:
                js = get(PMUS + "/markets", params={"slug": c["us"]}, quiet=True)
                ms = (js or {}).get("markets") or []
                if ms:
                    rec["us"] = {x: ms[0].get(x) for x in ("slug", "assetPriceTerms", "marketSides",
                                                           "outcomes", "status")}
                book = get(PMUS + f"/markets/{c['us']}/bbo", quiet=True)
                rec["us_bbo"] = (book or {}).get("marketData")
            if c["intl"]:
                js = get(GAMMA + "/markets", params={"slug": c["intl"], "closed": "true"}, quiet=True)
                if js:
                    rec["intl"] = {x: js[0].get(x) for x in ("slug", "outcomes", "outcomePrices")}
            mf.write(json.dumps(rec, default=str) + "\n")
            mf.flush()
        if n and n % 2000 < 2:
            log("rows", n)
        time.sleep(max(0.0, every - (time.time() - now)))
    log("done", n, "rows")


if __name__ == "__main__":
    main()
