"""Options vs Kalshi index brackets: snapshot logger (research only, added Nov 20, 2026).

Question (Alex, Nov 20): do S&P 500 / Nasdaq-100 options imply different odds than Kalshi's
KXINX / KXINXU / KXNASDAQ100 / KXNASDAQ100U markets for the same 4 PM ET close?
Each run saves (1) CBOE's free 15-min-delayed SPX and NDX option chains for the nearest
expiries (bid/ask/iv per strike, CBOE's own timestamp) and (2) Kalshi's open bracket and
above/below markets for those closes (bid/ask). The analysis later lines the options snapshot
up with Kalshi 1-minute candles AT THE OPTIONS' TIMESTAMP, so the 15-minute delay does not
bias it. Never trades, needs no secrets.

Usage: python research_opt/opt_log.py <out_dir>
"""
from __future__ import annotations

import gzip
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
CBOE = "https://cdn.cboe.com/api/global/delayed_quotes/options/_{}.json"
SERIES = ["KXINX", "KXINXU", "KXNASDAQ100", "KXNASDAQ100U"]
UA = {"User-Agent": "Mozilla/5.0 (research logger)", "Accept": "application/json"}


def fetch(url: str, tries: int = 4):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (403, 404):
                break
        except Exception as e:  # noqa: BLE001
            last = repr(e)
        time.sleep(3 * (i + 1))
    print("fetch failed", url, last)
    return None


def expiry(sym: str) -> str:
    """SPXW261120C06700000 -> 261120 (yymmdd)."""
    i = next(k for k, ch in enumerate(sym) if ch.isdigit())
    return sym[i:i + 6]


def options(root: str, keep_expiries: int = 3) -> dict | None:
    d = fetch(CBOE.format(root))
    if not d or "data" not in d:
        return None
    opts = d["data"].get("options") or []
    exps = sorted({expiry(o["option"]) for o in opts})[:keep_expiries]
    rows = []
    for o in opts:
        if expiry(o["option"]) in exps:
            rows.append([o["option"], o.get("bid"), o.get("ask"), o.get("iv"), o.get("open_interest"),
                         o.get("volume")])
    return {"timestamp": d.get("timestamp"), "spot": d["data"].get("current_price"),
            "expiries": exps, "rows": rows}


def kalshi() -> dict:
    out = {}
    for s in SERIES:
        js = fetch(f"{KALSHI}/markets?series_ticker={s}&status=open&limit=1000") or {}
        out[s] = [[m["ticker"], m.get("event_ticker"), m.get("strike_type"), m.get("floor_strike"),
                   m.get("cap_strike"), m.get("yes_bid_dollars"), m.get("yes_ask_dollars"),
                   m.get("close_time")] for m in js.get("markets") or []]
    return out


def main(out_dir: str) -> int:
    now = datetime.now(timezone.utc)
    snap = {"taken": now.isoformat(timespec="seconds"), "kalshi": kalshi(),
            "SPX": options("SPX"), "NDX": options("NDX")}
    day = Path(out_dir) / now.strftime("%Y%m%d")
    day.mkdir(parents=True, exist_ok=True)
    with gzip.open(day / f"{now:%H%M}.json.gz", "wt") as f:
        json.dump(snap, f)
    for k in ("SPX", "NDX"):
        v = snap[k]
        print(k, "missing" if not v else f"{len(v['rows'])} rows, ts {v['timestamp']}, exps {v['expiries']}")
    print("kalshi", {k: len(v) for k, v in snap["kalshi"].items()})
    return 0 if (snap["SPX"] or snap["NDX"]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "opt_log"))
