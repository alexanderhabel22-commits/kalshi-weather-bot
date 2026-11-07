"""Whole-platform history pull for the edge mine (read-only public data; never
trades, never alerts).

For every series in this shard (all categories, MVE combos excluded) with
volume >= MIN_SERIES_VOL:
  1. list every settled market that closed START..END (live + /historical),
  2. keep a per (series, close month) volume index (capacity),
  3. sample up to PER_CELL markets per (series, month) with volume >= MIN_MKT_VOL
     (weight = eligible / sampled), at most PER_SERIES per series,
  4. pull each sampled market's full trade tape (exact trades with taker side).

Outputs (OUT dir): series.csv, volume.csv.gz, sample.csv.gz, trades_NN.csv.gz, log.txt
(tapes capped at TRADE_PAGES*1000 newest trades; `truncated` flags the cut)

usage: python research_hist/fetch.py <shard> <n_shards> <out dir>
"""
from __future__ import annotations

import csv
import gzip
import os
import random
import sys
import threading
import time
import zlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

K = "https://api.elections.kalshi.com/trade-api/v2"
START = datetime(2025, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 11, 7, tzinfo=timezone.utc)
MIN_SERIES_VOL = float(os.environ.get("MIN_SERIES_VOL", "50000"))
MIN_MKT_VOL = float(os.environ.get("MIN_MKT_VOL", "200"))
PER_CELL = int(os.environ.get("PER_CELL", "3"))
PER_SERIES = int(os.environ.get("PER_SERIES", "30"))
TRADE_PAGES = int(os.environ.get("TRADE_PAGES", "10"))
LIST_BUDGET_S = float(os.environ.get("LIST_H", "1.6")) * 3600
BUDGET_S = float(os.environ.get("BUDGET_H", "4.7")) * 3600
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


def get(path, params=None):
    for i in range(8):
        with _lock:
            wait = _last[0] + 0.065 - time.time()
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
                log("HTTP", r.status_code, path, str(params)[:120], r.text[:160])
            return None
        return r.json()
    return None


def ts(s):
    if not s:
        return 0
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def list_series(s, lo, hi):
    out, seen = [], set()
    for path, extra in (("/markets", {"status": "settled", "mve_filter": "exclude"}),
                        ("/historical/markets", {})):
        cur = None
        for _ in range(3000):
            p = {"series_ticker": s, "limit": 1000, "min_close_ts": lo, "max_close_ts": hi,
                 **extra}
            if cur:
                p["cursor"] = cur
            js = get(path, p)
            if not js:
                break
            for m in js.get("markets") or []:
                tk = m.get("ticker")
                if tk in seen or m.get("result") not in ("yes", "no"):
                    continue
                ct = ts(m.get("close_time"))
                if not lo <= ct < hi:
                    continue
                seen.add(tk)
                out.append({
                    "ticker": tk, "series": s,
                    "event": m.get("event_ticker") or tk.rsplit("-", 1)[0],
                    "open_ts": ts(m.get("open_time")), "close_ts": ct,
                    "occ_ts": ts(m.get("occurrence_datetime")),
                    "exp_ts": ts(m.get("expected_expiration_time")),
                    "result": m.get("result"),
                    "volume": f(m.get("volume_fp", m.get("volume"))) or 0.0,
                    "hist": int(path.startswith("/historical")),
                    "strike_type": m.get("strike_type") or "",
                    "early": int(bool(m.get("can_close_early"))),
                    "title": (m.get("title") or "")[:90].replace("\n", " "),
                    "sub": (m.get("yes_sub_title") or "")[:50].replace("\n", " "),
                })
            cur = js.get("cursor")
            if not cur:
                break
    return out


def tape(m):
    out, cur, trunc = [], None, False
    path = "/historical/trades" if m["hist"] else "/markets/trades"
    for i in range(TRADE_PAGES):
        p = {"ticker": m["ticker"], "limit": 1000}
        if cur:
            p["cursor"] = cur
        js = get(path, p)
        if not js and i == 0:
            path = "/markets/trades" if m["hist"] else "/historical/trades"
            js = get(path, p)
        if not js:
            break
        for t in js.get("trades") or []:
            yp = f(t.get("yes_price_dollars", t.get("yes_price")))
            c = f(t.get("count_fp", t.get("count")))
            if yp is None or c is None:
                continue
            if yp > 1.5:
                yp /= 100
            out.append((ts(t.get("created_time")), round(yp, 4), c, t.get("taker_side") or ""))
        cur = js.get("cursor")
        if not cur:
            break
        if i == TRADE_PAGES - 1:
            trunc = True
    out.sort()
    return out, trunc


def main():
    shard, n, out = int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    js = get("/series", {"include_volume": "true"}) or {}
    allser = js.get("series") or []
    log("series total", len(allser))
    mine = [s for s in allser
            if zlib.crc32((s.get("ticker") or "").encode()) % n == shard
            and not (s.get("ticker") or "").startswith("KXMVE")]
    with open(out / "series.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["series", "category", "title", "frequency", "fee_type", "fee_multiplier",
                    "volume", "tags"])
        for s in mine:
            w.writerow([s.get("ticker"), s.get("category"), (s.get("title") or "")[:90],
                        s.get("frequency"), s.get("fee_type"), s.get("fee_multiplier"),
                        s.get("volume_fp", s.get("volume")), "|".join(s.get("tags") or [])])
    keep = [s["ticker"] for s in mine
            if (f(s.get("volume_fp", s.get("volume"))) or 0) >= MIN_SERIES_VOL]
    rnd = random.Random(1000 + shard)
    rnd.shuffle(keep)
    log("shard", shard, "series", len(mine), "kept", len(keep))
    lo, hi = int(START.timestamp()), int(END.timestamp())
    list_deadline = T0 + LIST_BUDGET_S

    def lw(s):
        if time.time() > list_deadline:
            return s, None
        return s, list_series(s, lo, hi)

    vol = defaultdict(lambda: [0, 0.0])
    sample = []
    listed = 0
    with ThreadPoolExecutor(6) as ex:
        for s, ms in ex.map(lw, keep):
            if ms is None:
                continue
            listed += 1
            cell = defaultdict(list)
            for m in ms:
                mo = datetime.fromtimestamp(m["close_ts"], timezone.utc).strftime("%Y-%m")
                vol[(s, mo)][0] += 1
                vol[(s, mo)][1] += m["volume"]
                if m["volume"] >= MIN_MKT_VOL:
                    cell[mo].append(m)
            picks = []
            for mo, lst in cell.items():
                pk = lst if len(lst) <= PER_CELL else rnd.sample(lst, PER_CELL)
                picks += [(m, len(lst) / len(pk)) for m in pk]
            if len(picks) > PER_SERIES:
                scale = len(picks) / PER_SERIES
                picks = [(m, w * scale) for m, w in rnd.sample(picks, PER_SERIES)]
            sample += picks
    log("listed series", listed, "of", len(keep), "sampled markets", len(sample))
    with gzip.open(out / "volume.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["series", "month", "markets", "volume"])
        for (s, mo), (k, v) in sorted(vol.items()):
            w.writerow([s, mo, k, round(v)])

    rnd.shuffle(sample)
    deadline = T0 + BUDGET_S
    done = [0]

    def tw(item):
        if time.time() > deadline:
            return None, False
        r = tape(item[0])
        done[0] += 1
        if done[0] % 1000 == 0:
            log("tapes", done[0])
        return r

    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(tw, sample))

    cols = ["ticker", "series", "event", "open_ts", "close_ts", "occ_ts", "exp_ts", "result",
            "volume", "hist", "strike_type", "early", "title", "sub"]
    part, rows, ft, wt = -1, 0, None, None
    with gzip.open(out / "sample.csv.gz", "wt", newline="") as fs:
        ws = csv.writer(fs)
        ws.writerow(cols + ["weight", "fetched", "truncated", "n_trades"])
        for (m, wgt), (tr, trunc) in zip(sample, res):
            ws.writerow([m[c] for c in cols] + [round(wgt, 3), int(tr is not None), int(trunc),
                                                 len(tr or [])])
            if not tr:
                continue
            if ft is None or rows > 2_500_000:  # keep each file well under 100 MB
                if ft:
                    ft.close()
                part += 1
                rows = 0
                ft = gzip.open(out / f"trades_{part:02d}.csv.gz", "wt", newline="")
                wt = csv.writer(ft)
                wt.writerow(["ticker", "t", "yes_price", "count", "taker_side"])
            for t in tr:
                wt.writerow([m["ticker"], *t])
            rows += len(tr)
    if ft:
        ft.close()
    log("fetched", sum(1 for r in res if r[0] is not None), "of", len(sample),
        "truncated", sum(1 for r in res if r[1]), "trades", sum(len(r[0] or []) for r in res))


if __name__ == "__main__":
    try:
        main()
    finally:
        Path(sys.argv[3]).mkdir(parents=True, exist_ok=True)
        (Path(sys.argv[3]) / "log.txt").write_text("\n".join(LOG) + "\n")
