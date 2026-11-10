"""Upstream-source research, stage 1: which Kalshi markets settle on public data,
how fast do their prices jump, and what do the raw sources look like.

Read-only public data; never trades, never alerts.
usage: python research_src/probe.py OUT_DIR

1. series.csv / sources.csv: settlement sources + rules text for the target series.
2. markets.csv.gz: settled markets (closed since START) of those series.
3. trades.csv.gz: full trade tapes (capped) for the busiest markets of recent events.
4. hf_commits.csv: commit timestamps of every lmarena-ai Hugging Face repo
   (the raw source behind the AI-leaderboard markets).
5. url_probe.csv: status, cache headers and size of each settlement-source URL
   as seen from GitHub Actions (can a logger read it, and how stale is it).
"""
from __future__ import annotations

import csv
import gzip
import json
import os
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
START = int(datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp())
EVENTS_PER_SERIES = int(os.environ.get("EVENTS_PER_SERIES", "40"))
MKTS_PER_EVENT = int(os.environ.get("MKTS_PER_EVENT", "5"))
TRADE_PAGES = int(os.environ.get("TRADE_PAGES", "25"))
BUDGET_S = float(os.environ.get("BUDGET_H", "3.5")) * 3600
SERIES = """KXTOPMODEL KXLLM1 KXCHINAAI KXTOPUSAGEAI KXSPEECHAI KXCODEAI KXOPENINTAI KXIMAGEAI
KXRT KXRTTV KXMC KXRTCOMPARE KXTRUMPAPPROVE KXAPRPOTUS KXAPRPOTUSD KXTRUMPVH
KXNETFLIXRANKSHOW KXNETFLIXRANKMOVIE KXNETFLIXRANKSHOWRUNNERUP KXNETFLIXRANKMOVIERUNNERUP
KXNETFLIXRANKMOVIEGLOBAL KXNETFLIXRANKSHOWGLOBAL KXNETFLIXTOPVIEWSMOVIE KXNETFLIXTOPVIEWSTV
KXRANKLISTSONGSPOTUSA KXTOPMONTHLY KXSPOTIFYNUM1USA KXTOPSONG KXTOPALBUM KXBILLBOARDRUNNERUPSONG
KXRANKLISTSONGTOP10 KXALBUMDEBUT KXDEBUTSONG KXTSAW KXTSAD KXYTDAILYTOPVIDEOG KXYTTOPVIDEOG2D
KXYTTOPVIDEO2D KXYTVIEWSW KXYTVIEWSHIGH KXYTVIEWSD KXYTTOPSONGW KXSTEAMTOPSELLER KXGOOGSHARE
KXMEASLES KXTVSTATSNFLX KXTVSTATSHBO KXTOPMOVIE KXPADATACENTERS""".split()
S = requests.Session()
S.headers["User-Agent"] = "kalshi-research/0.1"
_lock = threading.Lock()
_last = [0.0]
T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:6.0f}s]", *a, flush=True)


def get(path, params=None):
    for i in range(7):
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
                log("HTTP", r.status_code, path, params, r.text[:200])
            return None
        return r.json()
    return None


def ts(s):
    if not s:
        return 0
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def series_info():
    rows, srcs = [], []
    for s in SERIES:
        js = (get(f"/series/{s}") or {}).get("series") or {}
        rows.append([s, js.get("title"), js.get("category"), js.get("frequency"),
                     js.get("fee_type"), js.get("fee_multiplier"),
                     json.dumps(js.get("settlement_sources") or []),
                     (js.get("contract_url") or "")])
        for src in js.get("settlement_sources") or []:
            srcs.append([s, src.get("name"), src.get("url")])
    with open(OUT / "series.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["series", "title", "category", "frequency", "fee_type", "fee_mult",
                    "sources", "contract_url"])
        w.writerows(rows)
    with open(OUT / "sources.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["series", "name", "url"])
        w.writerows(srcs)
    return srcs


COLS = ["ticker", "series", "event", "open_ts", "close_ts", "exp_ts", "settle_ts", "result",
        "volume", "hist", "title", "subtitle", "rules"]


def list_markets(s):
    out, seen = [], set()
    for path, extra in (("/markets", {"status": "settled"}), ("/historical/markets", {})):
        cur = None
        for _ in range(200):
            p = {"series_ticker": s, "limit": 1000, "min_close_ts": START, **extra}
            if cur:
                p["cursor"] = cur
            js = get(path, p)
            if not js:
                break
            for m in js.get("markets") or []:
                tk = m.get("ticker")
                if tk in seen or m.get("result") not in ("yes", "no"):
                    continue
                seen.add(tk)
                out.append({"ticker": tk, "series": s,
                            "event": m.get("event_ticker") or tk.rsplit("-", 1)[0],
                            "open_ts": ts(m.get("open_time")), "close_ts": ts(m.get("close_time")),
                            "exp_ts": ts(m.get("expiration_time") or m.get("latest_expiration_time")),
                            "settle_ts": ts(m.get("settlement_ts") or m.get("settled_time")),
                            "result": m.get("result"),
                            "volume": f(m.get("volume_fp", m.get("volume"))) or 0.0,
                            "hist": int(path.startswith("/historical")),
                            "title": (m.get("title") or "")[:120],
                            "subtitle": (m.get("yes_sub_title") or m.get("subtitle") or "")[:80],
                            "rules": (m.get("rules_primary") or "")[:600]})
            cur = js.get("cursor")
            if not cur:
                break
    return out


def tape(m, deadline):
    out, cur, trunc = [], None, False
    if time.time() > deadline:
        return m["ticker"], out, True
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
    return m["ticker"], out, trunc


HF = "https://huggingface.co/api"


def hf_commits():
    rows, repos = [], []
    for kind in ("spaces", "datasets", "models"):
        try:
            r = requests.get(f"{HF}/{kind}", params={"author": "lmarena-ai", "limit": 200},
                             timeout=30)
            for it in r.json() if r.ok else []:
                repos.append((kind, it.get("id")))
        except Exception as e:  # noqa: BLE001
            log("hf list", kind, e)
    log("hf repos", len(repos), repos[:30])
    for kind, rid in repos:
        for page in range(60):
            try:
                r = requests.get(f"{HF}/{kind}/{rid}/commits/main", params={"p": page},
                                 timeout=30)
            except Exception:  # noqa: BLE001
                break
            if not r.ok:
                break
            js = r.json()
            if not js:
                break
            for c in js:
                rows.append([kind, rid, c.get("id"), c.get("date"),
                             (c.get("title") or "").replace("\n", " ")[:200],
                             "|".join(a.get("user", "") for a in c.get("authors") or [])])
            if len(js) < 50:
                break
            time.sleep(0.3)
    with open(OUT / "hf_commits.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["kind", "repo", "sha", "date", "title", "authors"])
        w.writerows(rows)
    log("hf commits", len(rows))


EXTRA_URLS = [
    "https://lmarena.ai/leaderboard", "https://lmarena.ai/leaderboard/text",
    "https://lmarena.ai/leaderboard/webdev", "https://lmarena.ai/leaderboard/text-to-image",
    "https://huggingface.co/spaces/lmarena-ai/chatbot-arena-leaderboard",
    "https://www.tsa.gov/travel/passenger-volumes", "https://www.tsa.gov/travel/passenger-volumes/2026",
    "https://charts.spotify.com/charts/view/regional-us-daily/latest",
    "https://charts-spotify-com-service.spotify.com/public/v0/charts",
    "https://kworb.net/spotify/country/us_daily.html", "https://kworb.net/youtube/",
    "https://www.netflix.com/tudum/top10", "https://www.netflix.com/tudum/top10/data/all-weeks-global.tsv",
    "https://www.billboard.com/charts/hot-100/", "https://www.rottentomatoes.com/",
    "https://charts.youtube.com/charts/TopVideos/global/daily",
    "https://store.steampowered.com/search/?filter=topsellers",
    "https://www.realclearpolling.com/polls/approval/donald-trump/approval-rating",
    "https://www.cdc.gov/measles/data-research/index.html",
    "https://artificialanalysis.ai/leaderboards/models", "https://www.swebench.com/",
]


def url_probe(srcs):
    urls = sorted({u for _, _, u in srcs if u} | set(EXTRA_URLS))
    rows = []
    for u in urls:
        try:
            r = requests.get(u, timeout=25, headers={"User-Agent": "Mozilla/5.0 (research)"})
            h = r.headers
            rows.append([u, r.status_code, len(r.content), h.get("content-type", "")[:40],
                         h.get("last-modified", ""), h.get("etag", "")[:40],
                         h.get("cache-control", "")[:60], h.get("age", ""),
                         h.get("cf-cache-status", "") or h.get("x-cache", ""),
                         r.text[:300].replace("\n", " ")])
        except Exception as e:  # noqa: BLE001
            rows.append([u, "ERR", 0, "", "", "", "", "", "", str(e)[:200]])
    with open(OUT / "url_probe.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["url", "status", "bytes", "ctype", "last_modified", "etag", "cache_control",
                    "age", "cache", "head"])
        w.writerows(rows)
    log("probed urls", len(rows))


def main():
    srcs = series_info()
    log("series info done", len(srcs), "sources")
    hf_commits()
    url_probe(srcs)
    with ThreadPoolExecutor(6) as ex:
        allm = [m for lst in ex.map(list_markets, SERIES) for m in lst]
    log("settled markets", len(allm))
    with gzip.open(OUT / "markets.csv.gz", "wt", newline="") as fh:
        w = csv.DictWriter(fh, COLS)
        w.writeheader()
        w.writerows(allm)
    # pick the latest EVENTS_PER_SERIES events per series; per event the winner(s) + busiest markets
    by_ev = {}
    for m in allm:
        by_ev.setdefault((m["series"], m["event"]), []).append(m)
    pick = []
    for s in SERIES:
        evs = sorted([(max(x["close_ts"] for x in v), k) for k, v in by_ev.items() if k[0] == s],
                     reverse=True)[:EVENTS_PER_SERIES]
        for _, k in evs:
            ms = sorted(by_ev[k], key=lambda x: -x["volume"])
            sel = [x for x in ms if x["result"] == "yes"][:2]
            sel += [x for x in ms if x not in sel][: max(0, MKTS_PER_EVENT - len(sel))]
            pick += [x for x in sel if x["volume"] >= 50]
    log("tapes to pull", len(pick))
    deadline = T0 + BUDGET_S
    n = 0
    with gzip.open(OUT / "trades.csv.gz", "wt", newline="") as fh, \
            ThreadPoolExecutor(6) as ex:
        w = csv.writer(fh)
        w.writerow(["ticker", "ts", "yes_price", "count", "taker_side", "truncated"])
        for tk, rows, trunc in ex.map(lambda m: tape(m, deadline), pick):
            n += 1
            for r in rows:
                w.writerow([tk, *r, int(trunc)])
            if n % 200 == 0:
                log("tapes", n, "/", len(pick))
    log("done")


if __name__ == "__main__":
    main()
