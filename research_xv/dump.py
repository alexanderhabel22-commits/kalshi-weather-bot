"""Research only: dump every open Polymarket US market (with best bid/offer) and every open Kalshi
market to JSONL, for an offline match-and-compare. Public read-only endpoints, no keys.

usage: python research_xv/dump.py OUTDIR
"""
import json
import os
import sys
import time

import requests

PMUS = "https://gateway.polymarket.us/v1"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
S = requests.Session()
S.headers["User-Agent"] = "research-dump/1.0"


def get(url, params=None):
    for i in range(4):
        try:
            r = S.get(url, params=params, timeout=20)
            if r.status_code in (429, 503):
                time.sleep(1 + 2 * i)
                continue
            if r.status_code >= 400:
                print("http", r.status_code, url, params, r.text[:200], flush=True)
                return None
            return r.json()
        except Exception as e:  # noqa: BLE001
            print("ERR", url, e, flush=True)
            time.sleep(1 + i)
    return None


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    now = time.time()
    # Polymarket US: page through open markets
    pm = []
    off = 0
    while off < 30000:
        js = get(PMUS + "/markets", params={"limit": 100, "offset": off, "active": "true",
                                            "closed": "false"})
        ms = (js or {}).get("markets") or []
        pm += ms
        if len(ms) < 100:
            break
        off += 100
    print("pmus markets", len(pm), flush=True)
    with open(os.path.join(out, "pmus.jsonl"), "w") as f:
        for i, m in enumerate(pm):
            if "updown" in str(m.get("slug")):
                continue
            try:
                end = time.mktime(time.strptime(str(m.get("endDate"))[:19], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                end = now + 1e9
            bbo = get(PMUS + f"/markets/{m.get('slug')}/bbo") or {} if end - now < 10 * 86400 else {}
            md = bbo.get("marketData") or {}
            keep = {k: m.get(k) for k in ("id", "slug", "question", "title", "subtitle", "category",
                                           "endDate", "gameStartTime", "marketType", "outcomes",
                                           "marketSides", "feeCoefficient", "status", "sportsMarketType",
                                           "line", "eventSlug")}
            keep["description"] = str(m.get("description") or "")[:600]
            keep["bbo"] = {k: md.get(k) for k in ("bestBid", "bestAsk", "bidShares", "askShares",
                                                    "lastTradePx", "sharesTraded", "state")}
            f.write(json.dumps(keep, default=str) + "\n")
            if i % 500 == 0:
                print("pmus bbo", i, round(time.time() - now), flush=True)
            if bbo:
                time.sleep(0.06)
    # Kalshi: all open markets
    n, cursor = 0, None
    with open(os.path.join(out, "kalshi.jsonl"), "w") as f:
        while True:
            params = {"status": "open", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            js = get(KALSHI + "/markets", params=params)
            if not js:
                break
            for m in js.get("markets") or []:
                if m.get("ticker", "").startswith(("KXMVE", "KXSPOTSTREAM")):
                    continue
                keep = {k: m.get(k) for k in ("ticker", "event_ticker", "title", "subtitle",
                                               "yes_sub_title", "no_sub_title", "close_time",
                                               "yes_bid_dollars", "yes_ask_dollars", "volume_24h_fp",
                                               "volume_24h", "liquidity_dollars", "open_interest")}
                keep["rules"] = str(m.get("rules_primary") or "")[:400]
                f.write(json.dumps(keep) + "\n")
                n += 1
            cursor = js.get("cursor")
            if not cursor:
                break
    print("kalshi markets", n, "in", round(time.time() - now), "s", flush=True)


if __name__ == "__main__":
    main()
