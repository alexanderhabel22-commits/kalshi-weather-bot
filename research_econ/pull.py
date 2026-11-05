"""Economic-release Kalshi markets vs public nowcasts: data pull (read-only).

Never trades, never alerts. Pulls:
  1. every settled market in the main US econ-release series (CPI, core CPI,
     payrolls, unemployment, GDP, Fed decision / Fed funds rate, jobless claims,
     core PCE) from the live and /historical endpoints;
  2. hourly candlesticks (bid/ask/last) for the 21 days before each market closes;
  3. raw nowcast files (Cleveland Fed inflation nowcast, Atlanta Fed GDPNow)
     and FRED actuals, saved as-is for offline analysis.
Writes into argv[1].
"""
from __future__ import annotations

import csv
import gzip
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import requests

K = "https://api.elections.kalshi.com/trade-api/v2"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
OUT.mkdir(parents=True, exist_ok=True)
SERIES = ["KXCPI", "KXCPIYOY", "KXCPICORE", "KXCPICOREYOY", "KXPAYROLLS", "KXU3", "KXGDP",
          "KXFEDDECISION", "KXFED", "KXJOBLESSCLAIMS", "KXPCECORE",
          "CPI", "CPIYOY", "CPICORE", "CPICOREYOY", "PAYROLLS", "U3", "GDP", "FEDDECISION", "FED"]
LOOKBACK_H = 21 * 24
MIN_VOL = 50
S = requests.Session()
S.headers["User-Agent"] = "kalshi-research/0.1"
_lock = threading.Lock()
_last = [0.0]
T0 = time.time()
LOG: list[str] = []


def log(*a):
    msg = f"[{time.time() - T0:6.0f}s] " + " ".join(str(x) for x in a)
    print(msg, flush=True)
    LOG.append(msg)


def get(path, params=None, base=K):
    for i in range(7):
        with _lock:
            wait = _last[0] + 0.07 - time.time()
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            r = S.get(base + path, params=params, timeout=60)
        except requests.RequestException:
            time.sleep(2 * (i + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (i + 1))
            continue
        if not r.ok:
            log("HTTP", r.status_code, path, str(params)[:120], r.text[:160])
            return None
        return r.json()
    return None


def paginate(path, key, params, max_pages=50):
    out, cur = [], None
    for _ in range(max_pages):
        p = dict(params)
        if cur:
            p["cursor"] = cur
        js = get(path, p)
        if not js:
            break
        out += js.get(key) or []
        cur = js.get("cursor")
        if not cur:
            break
    return out


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def ts(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()) if s else 0


def pick(d, *names):
    """Candle sub-objects use either 'close' (cents) or 'close_dollars'."""
    if not isinstance(d, dict):
        return None
    for n in names:
        if d.get(n) is not None:
            v = f(d[n])
            if v is not None and not n.endswith("_dollars"):
                v = v / 100.0
            return v
    return None


def main():
    rows, seen, examples = [], set(), {}
    for s in SERIES:
        n0 = len(rows)
        for path, extra in (("/markets", {"status": "settled"}), ("/historical/markets", {})):
            for m in paginate(path, "markets", {"series_ticker": s, "limit": 1000, **extra}):
                tk = m.get("ticker")
                if tk in seen or m.get("result") not in ("yes", "no"):
                    continue
                seen.add(tk)
                examples.setdefault(s, m)
                rows.append({
                    "ticker": tk, "series": s, "event": m.get("event_ticker"),
                    "open": m.get("open_time"), "close": m.get("close_time"),
                    "result": m.get("result"),
                    "volume": f(m.get("volume_fp", m.get("volume"))) or 0,
                    "strike_type": m.get("strike_type"), "floor": m.get("floor_strike"),
                    "cap": m.get("cap_strike"), "exp_value": m.get("expiration_value"),
                    "title": (m.get("title") or "")[:140], "sub": (m.get("yes_sub_title") or "")[:80],
                    "hist": int(path.startswith("/historical")),
                })
        log(s, "markets", len(rows) - n0)
    (OUT / "market_examples.json").write_text(json.dumps(examples, indent=1))
    with open(OUT / "markets.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    todo = [r for r in rows if r["volume"] >= MIN_VOL and r["close"]]
    log("candle markets", len(todo))
    ex_candle = {}

    def candles(r):
        end = ts(r["close"])
        start = max(ts(r["open"]) if r["open"] else 0, end - LOOKBACK_H * 3600)
        p = {"start_ts": start, "end_ts": end, "period_interval": 60}
        paths = ([f"/historical/markets/{r['ticker']}/candlesticks"] if r["hist"] else []) + \
            [f"/series/{r['series']}/markets/{r['ticker']}/candlesticks",
             f"/historical/markets/{r['ticker']}/candlesticks"]
        for path in paths:
            js = get(path, p)
            if js and js.get("candlesticks"):
                ex_candle.setdefault("x", js["candlesticks"][:3])
                out = []
                for c in js["candlesticks"]:
                    out.append([r["ticker"], c.get("end_period_ts"),
                                pick(c.get("yes_bid"), "close_dollars", "close"),
                                pick(c.get("yes_ask"), "close_dollars", "close"),
                                pick(c.get("price"), "close_dollars", "close"),
                                pick(c.get("price"), "mean_dollars", "mean"),
                                f(c.get("volume_fp", c.get("volume"))),
                                f(c.get("open_interest_fp", c.get("open_interest")))])
                return out
        return []

    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(candles, todo))
    (OUT / "candle_example.json").write_text(json.dumps(ex_candle, indent=1))
    with gzip.open(OUT / "candles.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "ts", "bid", "ask", "last", "mean", "volume", "oi"])
        n = 0
        for x in res:
            w.writerows(x)
            n += len(x)
    log("candles", n, "markets with candles", sum(1 for x in res if x))

    raw = OUT / "raw"
    raw.mkdir(exist_ok=True)
    urls = json.loads((Path(__file__).parent / "sources.json").read_text())
    for name, url in urls.items():
        try:
            r = S.get(url, timeout=90, headers={"User-Agent": "Mozilla/5.0 (research)"})
            log("source", name, r.status_code, len(r.content))
            if r.ok:
                (raw / name).write_bytes(r.content)
        except requests.RequestException as e:
            log("source", name, "ERR", str(e)[:120])
    (OUT / "log.txt").write_text("\n".join(LOG))


if __name__ == "__main__":
    try:
        main()
    finally:
        (OUT / "log.txt").write_text("\n".join(LOG))
