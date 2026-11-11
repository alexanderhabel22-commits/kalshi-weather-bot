"""One-time speed test from the 15-min server (Alex OK'd, Nov 11 2026 12:25 AM ET).

Measures how fast our server talks to Kalshi: public order-book reads, signed reads, and the
round trip of placing and cancelling a real order. Each test order is a 1-contract post-only
YES bid at 1c (far below the market, so it cannot fill in practice), cancelled right away.
Test orders use client ids starting "latprobe-" and are NOT recorded in any bot log, ledger
or P&L (they never fill; if one ever did, the 1c is reported in the output file only).

Runs once from deploy/m15/update.sh (marker /var/lib/kalshi-latency-probe.<VERSION>).
Output: m15-data branch, m15/latency-probe.json.
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import time
import uuid

from .kalshi import BASE
from .kalshi_api import fill_qty
from .m15 import client_from_env, github_uploader
from .util import Http

log = logging.getLogger("wxbot")
VERSION = "v4"
ROUNDS = 8


def _ms(f):
    a = time.perf_counter()
    out = f()
    return (time.perf_counter() - a) * 1000, out


def summary(xs: list[float]) -> dict:
    if not xs:
        return {}
    xs = sorted(xs)
    return {"n": len(xs), "min": round(xs[0], 1), "p50": round(statistics.median(xs), 1),
            "p90": round(xs[int(0.9 * (len(xs) - 1))], 1), "max": round(xs[-1], 1)}


def _yes_mid(m: dict) -> float | None:
    try:
        b, a = float(m.get("yes_bid_dollars")), float(m.get("yes_ask_dollars"))
    except (TypeError, ValueError):
        return None
    return (a + b) / 2


def pick_markets(http: Http) -> list[str]:
    """Current 15-min BTC window (crypto shard) when its YES is 20-80c with 8+ minutes left,
    and one Fed-decision market (default shard) with YES between 10c and 90c."""
    out = []
    js = http.get_json(f"{BASE}/markets", params={"series_ticker": "KXBTC15M", "status": "open",
                                                  "limit": 200})
    def left(m):
        try:
            return time.mktime(time.strptime(m["close_time"][:19], "%Y-%m-%dT%H:%M:%S")) \
                - time.timezone - time.time()
        except (KeyError, ValueError):
            return -1.0
    # the open list also holds stale windows: take the one closing soonest in the future
    live = sorted((m for m in js.get("markets") or [] if left(m) > 0), key=left)
    for m in live[:1]:
        mid = _yes_mid(m)
        if mid is not None and 0.2 <= mid <= 0.8 and left(m) > 480:
            out.append(m["ticker"])
            break
    js = http.get_json(f"{BASE}/markets", params={"series_ticker": "KXFEDDECISION",
                                                  "status": "open", "limit": 50})
    for m in js.get("markets") or []:
        mid = _yes_mid(m)
        if mid is not None and 0.1 <= mid <= 0.9:
            out.append(m["ticker"])
            break
    return out


def run(http: Http, client, rounds: int = ROUNDS, pause=time.sleep) -> dict:
    res: dict = {"version": VERSION, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "public_book_ms": [], "signed_get_ms": [], "markets": {}}
    tks = pick_markets(http)
    probe_tk = tks[0] if tks else "KXFEDDECISION"
    # gentle: about one call per second, so the live bots sharing this key never hit
    # Kalshi's rate limit (v3 got HTTP 429 at 5 calls/s on top of them)
    for _ in range(15):
        ms, _ = _ms(lambda: http.get_json(f"{BASE}/markets/{probe_tk}/orderbook"))
        res["public_book_ms"].append(ms)
        pause(1)
    for _ in range(5):
        ms, _ = _ms(client.balance)
        res["signed_get_ms"].append(ms)
        pause(1)
    for tk in tks:
        r = {"place_ms": [], "cancel_ms": [], "status_ms": [], "filled": 0.0, "errors": []}
        for _ in range(rounds):
            cid = f"latprobe-{uuid.uuid4().hex[:16]}"
            try:
                ms, o = _ms(lambda: client.place_limit(tk, "yes", 0.01, 1, cid))
                r["place_ms"].append(ms)
                oid = o.get("order_id") or o.get("id")
                ms, _ = _ms(lambda: client.cancel(oid, tk))    # cancel at once, no pause
                r["cancel_ms"].append(ms)
                pause(1)
                ms, ko = _ms(lambda: client.get_order(oid))
                r["status_ms"].append(ms)
                r["filled"] += fill_qty(ko, 1)
            except Exception as exc:          # report, never retry into a loop
                r["errors"].append(str(exc)[:200])
            pause(3)
        res["markets"][tk] = r
    for k in ("public_book_ms", "signed_get_ms"):
        res[k] = summary(res[k])
    for r in res["markets"].values():
        for k in ("place_ms", "cancel_ms", "status_ms"):
            r[k] = summary(r[k])
    return res


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    try:
        http = Http("kalshi-m15-latency/1", min_interval=0.0, retries=4)
        client = client_from_env(http)
        if client is None:
            res = {"version": VERSION, "error": "no Kalshi key in the environment"}
        else:
            res = run(http, client)
            res["host_base"] = BASE
    except Exception:                     # report the failure instead of failing silently
        import traceback
        res = {"version": VERSION, "error": traceback.format_exc()[-2000:]}
    text = json.dumps(res, indent=1)
    print(text)
    if token:
        github_uploader(Http("kalshi-m15-latency/1"), token)({"latency-probe.json": text})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
