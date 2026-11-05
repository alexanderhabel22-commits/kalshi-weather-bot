"""Mentions history pull with trade timing (read-only public data; never trades).

Every settled Mentions-category market (live + /historical endpoints) with
its metadata, plus all trades aggregated by
(ticker, taker side, yes price in cents, time-before-close bucket).
The time buckets let us test timing and live-event adverse selection.
Writes into argv[1]: markets.csv.gz, trades_YYYYQn.csv.gz, log.txt.
"""
from __future__ import annotations

import csv
import gzip
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

K = "https://api.elections.kalshi.com/trade-api/v2"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
OUT.mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers["User-Agent"] = "kalshi-research/0.1"
_lock = threading.Lock()
_last = [0.0]
T0 = time.time()
LOG: list[str] = []
START = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
# minutes before close: bucket upper edges
EDGES = [5, 15, 30, 60, 120, 180, 360, 720, 1440, 2880, 4320, 10080]


def log(*a):
    msg = f"[{time.time() - T0:6.0f}s] " + " ".join(str(x) for x in a)
    print(msg, flush=True)
    LOG.append(msg)


def get(path, params=None):
    for i in range(7):
        with _lock:
            wait = _last[0] + 0.06 - time.time()
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            r = S.get(K + path, params=params, timeout=60)
        except requests.RequestException:
            time.sleep(2 * (i + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (i + 1))
            continue
        if not r.ok:
            if r.status_code != 404:
                log("HTTP", r.status_code, path, params, r.text[:200])
            return None
        return r.json()
    return None


def paginate(path, key, params, max_pages):
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
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def bucket(mins):
    for i, e in enumerate(EDGES):
        if mins < e:
            return i
    return len(EDGES)


MFIELDS = ["ticker", "event", "series", "hist", "open", "close", "occurrence", "expected_exp",
           "settled_at", "result", "volume", "title", "yes_sub", "can_close_early",
           "first_trade", "last_trade", "n_trades"]


def main():
    js = get("/series", {"category": "Mentions"}) or {}
    series = sorted(s["ticker"] for s in js.get("series") or [] if s.get("category") == "Mentions")
    log("mentions series", len(series))
    rows, seen = [], set()
    for s in series:
        for path, extra in (("/markets", {"status": "settled"}), ("/historical/markets", {})):
            for m in paginate(path, "markets", {"series_ticker": s, "limit": 1000,
                                                 "min_close_ts": START, **extra}, 60):
                tk = m.get("ticker")
                if not tk or tk in seen or m.get("result") not in ("yes", "no"):
                    continue
                if (f(m.get("volume_fp", m.get("volume"))) or 0) < 10:
                    continue
                seen.add(tk)
                rows.append({
                    "ticker": tk, "event": m.get("event_ticker") or tk.rsplit("-", 1)[0],
                    "series": s, "hist": int(path.startswith("/historical")),
                    "open": m.get("open_time"), "close": m.get("close_time"),
                    "occurrence": m.get("occurrence_datetime") or "",
                    "expected_exp": m.get("expected_expiration_time") or "",
                    "settled_at": m.get("settlement_ts") or "",
                    "result": m.get("result"),
                    "volume": f(m.get("volume_fp", m.get("volume"))) or 0,
                    "title": (m.get("title") or "")[:140],
                    "yes_sub": (m.get("yes_sub_title") or "")[:80],
                    "can_close_early": int(bool(m.get("can_close_early"))),
                })
    log("markets", len(rows), "historical", sum(r["hist"] for r in rows))

    agg: dict = {}
    alock = threading.Lock()

    def trades(r):
        path = "/historical/trades" if r["hist"] else "/markets/trades"
        close = ts(r["close"]) or 0
        local, first, last, n, cur = {}, None, None, 0, None
        for _ in range(40):
            p = {"ticker": r["ticker"], "limit": 1000}
            if cur:
                p["cursor"] = cur
            js = get(path, p)
            if not js:
                break
            for t in js.get("trades") or []:
                tt = ts(t.get("created_time"))
                yp = f(t.get("yes_price_dollars", t.get("yes_price")))
                c = f(t.get("count_fp", t.get("count")))
                side = t.get("taker_side")
                if tt is None or yp is None or c is None or side not in ("yes", "no"):
                    continue
                if yp > 1.0:
                    yp /= 100.0
                first = tt if first is None else min(first, tt)
                last = tt if last is None else max(last, tt)
                n += 1
                k = (r["ticker"], side, int(round(yp * 100)), bucket((close - tt) / 60.0))
                local[k] = local.get(k, 0.0) + c
            cur = js.get("cursor")
            if not cur:
                break
        r["first_trade"] = datetime.fromtimestamp(first, timezone.utc).isoformat()[:19] if first else ""
        r["last_trade"] = datetime.fromtimestamp(last, timezone.utc).isoformat()[:19] if last else ""
        r["n_trades"] = n
        with alock:
            for k, v in local.items():
                agg[k] = agg.get(k, 0.0) + v

    done = [0]

    def run(r):
        trades(r)
        done[0] += 1
        if done[0] % 5000 == 0:
            log("trades for", done[0], "markets")

    with ThreadPoolExecutor(6) as ex:
        list(ex.map(run, rows))

    with gzip.open(OUT / "markets.csv.gz", "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=MFIELDS)
        w.writeheader()
        w.writerows(rows)
    def quarter(c):
        c = c or "0000-01"
        return f"{c[:4]}Q{(int(c[5:7]) - 1) // 3 + 1}"
    year = {r["ticker"]: quarter(r["close"]) for r in rows}
    files: dict = {}
    for (tk, side, yp, b), c in agg.items():
        y = year.get(tk, "0000")
        if y not in files:
            fh = gzip.open(OUT / f"trades_{y}.csv.gz", "wt", newline="")
            w = csv.writer(fh)
            w.writerow(["ticker", "taker_side", "yes_cents", "bucket", "count"])
            files[y] = (fh, w)
        files[y][1].writerow([tk, side, yp, b, round(c, 2)])
    for fh, _ in files.values():
        fh.close()
    log("done: markets", len(rows), "agg rows", len(agg), "trades", sum(r["n_trades"] for r in rows))


if __name__ == "__main__":
    try:
        main()
    finally:
        (OUT / "log.txt").write_text("\n".join(LOG))
