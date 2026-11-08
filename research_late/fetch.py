"""Late-game sports + props/lines history pull (read-only public data; never trades,
never alerts). Research only.

Shard roles (by index):
  "late:<lg>"  NBA/NHL: ESPN finished games (scoreboard), match to Kalshi game markets,
               sample games, save ESPN play-by-play (game clock <-> wallclock, score,
               ESPN win prob) and Kalshi 1-min bid/ask candles + trade prints for the
               game/spread/total markets from the start of Q4 / P3 to close.
  "props:<i>"  props and line series: list settled markets, sample events, pull full
               trade tapes; plus ESPN scoreboards (start times) for matching.

usage: python research_late/fetch.py <shard> <out dir>
"""
from __future__ import annotations

import csv
import gzip
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

K = "https://api.elections.kalshi.com/trade-api/v2"
E = "https://site.api.espn.com/apis/site/v2/sports"
START = date(2025, 1, 1)
END = date(2026, 11, 7)
N_GAMES = int(os.environ.get("N_GAMES", "900"))
PER_SERIES_EV = int(os.environ.get("PER_SERIES_EV", "220"))
MIN_MKT_VOL = float(os.environ.get("MIN_MKT_VOL", "200"))
TRADE_PAGES = int(os.environ.get("TRADE_PAGES", "6"))
BUDGET_S = float(os.environ.get("BUDGET_H", "5.0")) * 3600

LEAGUES = {  # tag: (espn path, kalshi game, spread, total)
    "nba": ("basketball/nba", "KXNBAGAME", "KXNBASPREAD", "KXNBATOTAL"),
    "nhl": ("hockey/nhl", "KXNHLGAME", "KXNHLSPREAD", "KXNHLTOTAL"),
    "nfl": ("football/nfl", "KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL"),
    "mlb": ("baseball/mlb", "KXMLBGAME", "KXMLBSPREAD", "KXMLBTOTAL"),
}
PROP_SHARDS = [
    ["KXNBAPTS", "KXNBAREB", "KXNBAAST", "KXNBA3PT", "KXNBA2D", "KXNBASPREAD", "KXNBATOTAL"],
    ["KXNFLANYTD", "KXNFLTD", "KXNFLRECYDS", "KXNFLRSHYDS", "KXNFLPASSYDS", "KXNFLREC",
     "KXNFLSPREAD", "KXNFLTOTAL"],
    ["KXMLBHR", "KXMLBKS", "KXMLBHIT", "KXMLBTB", "KXMLBHRR", "KXMLBTOTAL", "KXMLBSPREAD"],
    ["KXNHLGOAL", "KXNHLSPREAD", "KXNHLTOTAL", "KXNBATEAMTOTAL", "KXNFLFIRSTTD",
     "KXNCAAFSPREAD", "KXNCAAFTOTAL"],
]
SHARDS = ["late:nba", "late:nhl"] + [f"props:{i}" for i in range(len(PROP_SHARDS))]

S = requests.Session()
S.headers["User-Agent"] = "kalshi-research/0.1"
ES = requests.Session()
ES.headers["User-Agent"] = "kalshi-research/0.1"
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


def eget(url, params=None):
    for i in range(5):
        try:
            r = ES.get(url, params=params, timeout=40)
            if r.status_code == 404:
                return None
            if r.ok:
                return r.json()
        except (requests.RequestException, ValueError):
            pass
        time.sleep(1.5 * (i + 1))
    return None


def ts(s):
    if not s:
        return 0
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def fl(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def dollars(v):
    v = fl(v)
    if v is not None and v > 1.5:
        v /= 100
    return v


# ---------------------------------------------------------------- Kalshi

def list_markets(series):
    lo = int(datetime(START.year, START.month, START.day, tzinfo=timezone.utc).timestamp())
    hi = int(datetime(END.year, END.month, END.day, tzinfo=timezone.utc).timestamp())
    out, seen = [], set()
    for path, extra in (("/markets", {"status": "settled"}), ("/historical/markets", {})):
        cur = None
        for _ in range(3000):
            p = {"series_ticker": series, "limit": 1000, "min_close_ts": lo, "max_close_ts": hi,
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
                seen.add(tk)
                out.append({
                    "series": series, "ticker": tk,
                    "event": m.get("event_ticker") or tk.rsplit("-", 1)[0],
                    "open_ts": ts(m.get("open_time")), "close_ts": ts(m.get("close_time")),
                    "occ_ts": ts(m.get("occurrence_datetime")),
                    "exp_ts": ts(m.get("expected_expiration_time")),
                    "result": m.get("result"),
                    "volume": fl(m.get("volume_fp", m.get("volume"))) or 0.0,
                    "hist": int(path.startswith("/historical")),
                    "strike": m.get("floor_strike", ""),
                    "strike_type": m.get("strike_type") or "",
                    "title": (m.get("title") or "")[:100].replace("\n", " "),
                    "sub": (m.get("yes_sub_title") or "")[:60].replace("\n", " "),
                })
            cur = js.get("cursor")
            if not cur:
                break
    return out


MCOLS = ["series", "ticker", "event", "open_ts", "close_ts", "occ_ts", "exp_ts", "result",
         "volume", "hist", "strike", "strike_type", "title", "sub"]


def tape(m, min_ts=None, max_ts=None, pages=TRADE_PAGES):
    out, cur, trunc = [], None, False
    path = "/historical/trades" if m["hist"] else "/markets/trades"
    for i in range(pages):
        p = {"ticker": m["ticker"], "limit": 1000}
        if min_ts:
            p["min_ts"] = min_ts
        if max_ts:
            p["max_ts"] = max_ts
        if cur:
            p["cursor"] = cur
        js = get(path, p)
        if not js and i == 0:
            path = "/markets/trades" if m["hist"] else "/historical/trades"
            js = get(path, p)
        if not js:
            break
        for t in js.get("trades") or []:
            yp, c = dollars(t.get("yes_price_dollars", t.get("yes_price"))), \
                fl(t.get("count_fp", t.get("count")))
            if yp is None or c is None:
                continue
            out.append((m["ticker"], ts(t.get("created_time")), round(yp, 4), c,
                        t.get("taker_side") or ""))
        cur = js.get("cursor")
        if not cur:
            break
        if i == pages - 1:
            trunc = True
    out.sort(key=lambda r: r[1])
    return out, trunc


def candles(m, start, end):
    rows = []
    hist = m["hist"]
    base = (f"/historical/markets/{m['ticker']}/candlesticks" if hist
            else f"/series/{m['series']}/markets/{m['ticker']}/candlesticks")
    a = start
    while a < end:
        b = min(end, a + 2000 * 60)
        js = get(base, {"start_ts": a, "end_ts": b, "period_interval": 1})
        if js is None and not hist:
            js = get(f"/historical/markets/{m['ticker']}/candlesticks",
                     {"start_ts": a, "end_ts": b, "period_interval": 1})
        for c in (js or {}).get("candlesticks") or []:
            yb, ya = c.get("yes_bid") or {}, c.get("yes_ask") or {}

            def d(o):
                v = dollars(o.get("close_dollars", o.get("close")))
                return "" if v is None else round(v, 4)
            rows.append((m["ticker"], c.get("end_period_ts"), d(yb), d(ya),
                         fl(c.get("volume_fp", c.get("volume"))) or 0))
        a = b
    return rows


# ---------------------------------------------------------------- ESPN

def scoreboard(job):
    tag, d = job
    path = LEAGUES[tag][0]
    js = eget(f"{E}/{path}/scoreboard", {"dates": d.strftime("%Y%m%d"), "limit": 400}) or {}
    out = []
    for ev in js.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        st = (comp.get("status") or {}).get("type") or {}
        if not st.get("completed"):
            continue
        teams = {c.get("homeAway"): c for c in comp.get("competitors") or []}
        h, a = teams.get("home") or {}, teams.get("away") or {}

        def t(c):
            tm = c.get("team") or {}
            return [tm.get("abbreviation") or "", tm.get("location") or "",
                    tm.get("shortDisplayName") or "", tm.get("displayName") or "",
                    c.get("score") or "", int(bool(c.get("winner")))]
        out.append([tag, ev.get("id"), ev.get("date"), d.isoformat()] + t(h) + t(a))
    return out


GCOLS = ["league", "event_id", "start", "sb_date",
         "h_abbr", "h_loc", "h_short", "h_name", "h_score", "h_win",
         "a_abbr", "a_loc", "a_short", "a_name", "a_score", "a_win"]


def espn_games(tags):
    jobs = []
    d = START - timedelta(days=1)
    while d <= END:
        jobs += [(t, d) for t in tags]
        d += timedelta(days=1)
    with ThreadPoolExecutor(8) as ex:
        rows = [g for gs in ex.map(scoreboard, jobs) for g in gs]
    uniq = {}
    for r in rows:
        uniq[(r[0], r[1])] = r
    log("espn games", tags, len(uniq))
    return list(uniq.values())


SUF = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})(\d{4})?([A-Z0-9]+)$")
MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


ALIAS = {"NY": {"NYK"}, "NO": {"NOP"}, "SA": {"SAS"}, "GS": {"GSW"}, "UTAH": {"UTA"},
         "CHW": {"CWS"}, "ARI": {"AZ"}, "WSH": {"WAS"}, "PHX": {"PHO"}, "TB": {"TBL", "TBR"},
         "NJ": {"NJD"}, "LA": {"LAK"}, "SJ": {"SJS"}, "KC": {"KCR"}, "SD": {"SDP"},
         "SF": {"SFG"}, "UTA": {"UTAH"}, "VGK": {"VGS", "VEG"}, "CBJ": {"CLB"},
         "MON": {"MTL"}, "LV": {"LVR"}, "JAX": {"JAC"}}


def codes(g, side):
    ab, loc, short = g[GCOLS.index(side + "_abbr")], g[GCOLS.index(side + "_loc")], \
        g[GCOLS.index(side + "_short")]
    c = {ab.upper()}
    if loc:
        c.add(re.sub(r"[^A-Z]", "", loc.upper())[:3])
    if short:
        c.add(re.sub(r"[^A-Z]", "", short.upper())[:3])
    ab = ab.upper()
    c |= {ab[:2], ab[:3]} | ALIAS.get(ab, set())
    return {x for x in c if x}


def match(event_ticker, by_date):
    """Kalshi event ticker -> ESPN game row (or None)."""
    suf = event_ticker.split("-", 1)[-1]
    mm = SUF.match(suf)
    if not mm:
        return None
    try:
        d = date(2000 + int(mm.group(1)), MON[mm.group(2)], int(mm.group(3)))
    except (KeyError, ValueError):
        return None
    tstr = mm.group(5)
    hits = []
    for dd in (d, d - timedelta(days=1), d + timedelta(days=1)):
        for g in by_date.get(dd.isoformat(), []):
            for a in codes(g, "a"):
                if tstr.startswith(a) and tstr[len(a):] in codes(g, "h"):
                    hits.append(g)
                    break
        if hits:
            break
    return hits[0] if len(hits) == 1 else None


def summary(tag, eid):
    js = eget(f"{E}/{LEAGUES[tag][0]}/summary", {"event": eid}) or {}
    wp = {w.get("playId"): w.get("homeWinPercentage") for w in js.get("winprobability") or []}
    plays = []
    for p in js.get("plays") or []:
        plays.append((eid, (p.get("period") or {}).get("number"),
                      (p.get("clock") or {}).get("displayValue"), ts(p.get("wallclock")),
                      p.get("homeScore"), p.get("awayScore"), wp.get(p.get("id"), ""),
                      int(bool(p.get("scoringPlay")))))
    return plays


# ---------------------------------------------------------------- roles

def write_csv(path, header, rows):
    with gzip.open(path, "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def late(tag, out):
    _, gser, sser, tser = LEAGUES[tag]
    games = espn_games([tag])
    write_csv(out / "espn_games.csv.gz", GCOLS, games)
    by_date = {}
    for g in games:
        by_date.setdefault(g[3], []).append(g)
    mk = {s: list_markets(s) for s in (gser, sser, tser)}
    for s, v in mk.items():
        log("kalshi", s, len(v))
    ev_g = {}
    for m in mk[gser]:
        ev_g.setdefault(m["event"], []).append(m)
    suf_lines = {}
    for s in (sser, tser):
        for m in mk[s]:
            suf_lines.setdefault(m["event"].split("-", 1)[-1], []).append(m)
    matched = []
    for ev, ms in ev_g.items():
        g = match(ev, by_date)
        if g is not None and len(ms) == 2:
            matched.append((ev, g, ms))
    log("matched games", len(matched), "of", len(ev_g))
    rnd = random.Random(11)
    pick = matched if len(matched) <= N_GAMES else rnd.sample(matched, N_GAMES)
    deadline = T0 + BUDGET_S

    def work(item):
        ev, g, ms = item
        if time.time() > deadline:
            return None
        plays = summary(tag, g[1])
        if not plays:
            return None
        lastp = 4 if tag == "nba" else 3
        p_start = [p[3] for p in plays if p[1] == lastp and p[3]]
        close = max(m["close_ts"] for m in ms)
        st = (min(p_start) if p_start else close - 3600) - 600
        st -= st % 60
        cand, trades = [], []
        for m in ms:
            cand += candles(m, st, close + 60)
            t, _ = tape(m, st, close + 60, pages=30)
            trades += t
        lines = [m for m in suf_lines.get(ev.split("-", 1)[-1], [])
                 if m["volume"] >= MIN_MKT_VOL]
        for m in lines:
            cand += candles(m, st, close + 60)
        return ev, g, ms + lines, plays, cand, trades

    with ThreadPoolExecutor(6) as ex:
        res = [r for r in ex.map(work, pick) if r]
    log("games fetched", len(res), "of", len(pick))
    write_csv(out / "games_matched.csv.gz", ["kalshi_event"] + GCOLS,
              [[r[0]] + list(r[1]) for r in res])
    write_csv(out / "markets.csv.gz", MCOLS, [[m[c] for c in MCOLS] for r in res for m in r[2]])
    write_csv(out / "plays.csv.gz", ["event_id", "period", "clock", "wall_ts", "h_score",
                                     "a_score", "h_wp", "scoring"],
              [p for r in res for p in r[3]])
    write_csv(out / "candles.csv.gz", ["ticker", "ts", "yes_bid", "yes_ask", "volume"],
              [c for r in res for c in r[4]])
    write_csv(out / "trades.csv.gz", ["ticker", "t", "yes_price", "count", "taker_side"],
              [t for r in res for t in r[5]])


def props(i, out):
    series = PROP_SHARDS[i]
    tags = sorted({s[2:5].lower() for s in series if s[2:5] in ("NBA", "NHL", "NFL", "MLB")})
    write_csv(out / "espn_games.csv.gz", GCOLS, espn_games(tags))
    rnd = random.Random(100 + i)
    deadline = T0 + BUDGET_S
    allm, sampled = [], []
    for s in series:
        ms = list_markets(s)
        evs = {}
        for m in ms:
            evs.setdefault(m["event"], []).append(m)
        keys = sorted(evs)
        pick = keys if len(keys) <= PER_SERIES_EV else rnd.sample(keys, PER_SERIES_EV)
        n0 = len(sampled)
        for k in pick:
            for m in evs[k]:
                allm.append(m)
                if m["volume"] >= MIN_MKT_VOL:
                    sampled.append(m)
        log("series", s, "markets", len(ms), "events", len(keys), "sampled mkts",
            len(sampled) - n0)
    rnd.shuffle(sampled)

    def work(m):
        if time.time() > deadline:
            return None, False
        return tape(m)

    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(work, sampled))
    fetched = {m["ticker"]: (r is not None, tr) for m, (r, tr) in zip(sampled, res)}
    write_csv(out / "markets.csv.gz", MCOLS + ["fetched", "truncated"],
              [[m[c] for c in MCOLS] + [int(fetched.get(m["ticker"], (0, 0))[0]),
                                        int(fetched.get(m["ticker"], (0, 0))[1])]
               for m in allm])
    write_csv(out / "trades.csv.gz", ["ticker", "t", "yes_price", "count", "taker_side"],
              [t for r, _ in res if r for t in r])
    log("tapes", sum(1 for r, _ in res if r is not None), "of", len(sampled))


def main():
    shard, out = int(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    role = SHARDS[shard]
    log("role", role)
    kind, arg = role.split(":")
    if kind == "late":
        late(arg, out)
    else:
        props(int(arg), out)


if __name__ == "__main__":
    try:
        main()
    finally:
        Path(sys.argv[2]).mkdir(parents=True, exist_ok=True)
        (Path(sys.argv[2]) / "log.txt").write_text("\n".join(LOG) + "\n")
