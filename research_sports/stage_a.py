"""Stage A: ESPN games + DraftKings open/close lines, and Kalshi game markets.

Read-only public data; never trades. Writes into argv[1]:
  games.csv.gz    one row per final ESPN game per odds provider
  kalshi.csv.gz   every settled Kalshi game-winner market since START
"""
from __future__ import annotations

import csv
import gzip
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from kalshi_api import LOG, f, log, paginate  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
OUT.mkdir(parents=True, exist_ok=True)
E = "https://site.api.espn.com/apis/site/v2/sports"
C = "https://sports.core.api.espn.com/v2/sports"
START = date(2026, 1, 1)
END = date(2026, 11, 4)
LEAGUES = {  # tag: (espn path, scoreboard extra params, kalshi series)
    "nfl": ("football/nfl", {}, "KXNFLGAME"),
    "nba": ("basketball/nba", {}, "KXNBAGAME"),
    "nhl": ("hockey/nhl", {}, "KXNHLGAME"),
    "mlb": ("baseball/mlb", {}, "KXMLBGAME"),
    "wnba": ("basketball/wnba", {}, "KXWNBAGAME"),
    "ncaaf": ("football/college-football", {"groups": 80, "limit": 400}, "KXNCAAFGAME"),
    "ncaab": ("basketball/mens-college-basketball", {"groups": 50, "limit": 400}, "KXNCAAMBGAME"),
}
ES = requests.Session()
ES.headers["User-Agent"] = "kalshi-research/0.1"


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


def scoreboard(job):
    tag, d = job
    path, extra, _ = LEAGUES[tag]
    js = eget(f"{E}/{path}/scoreboard", {"dates": d.strftime("%Y%m%d"), **extra}) or {}
    out = []
    for ev in js.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        if not ((comp.get("status") or {}).get("type") or {}).get("completed"):
            continue
        teams = {c.get("homeAway"): c for c in comp.get("competitors") or []}
        h, a = teams.get("home") or {}, teams.get("away") or {}

        def t(c):
            tm = c.get("team") or {}
            return [tm.get("abbreviation"), tm.get("location"), tm.get("shortDisplayName"),
                    tm.get("displayName"), c.get("score"), int(bool(c.get("winner")))]
        out.append([tag, ev.get("id"), ev.get("date"), int(bool(comp.get("neutralSite")))] + t(h) + t(a))
    return out


def ml(side, key):
    x = ((side or {}).get(key) or {}).get("moneyLine") or {}
    return x.get("american") or x.get("alternateDisplayValue")


def odds(row):
    tag, eid = row[0], row[1]
    path = LEAGUES[tag][0]
    sport, league = path.split("/")
    js = eget(f"{C}/{sport}/leagues/{league}/events/{eid}/competitions/{eid}/odds") or {}
    out = []
    for it in js.get("items") or []:
        p = it.get("provider") or {}
        h, a = it.get("homeTeamOdds") or {}, it.get("awayTeamOdds") or {}
        out.append(row + [p.get("name"), p.get("id"), ml(h, "open"), ml(a, "open"),
                          ml(h, "close"), ml(a, "close"), h.get("moneyLine"), a.get("moneyLine"),
                          it.get("spread"), it.get("overUnder")])
    if not out:
        out.append(row + [""] * 10)
    return out


HDR = ["league", "event_id", "start", "neutral",
       "h_abbr", "h_loc", "h_short", "h_name", "h_score", "h_win",
       "a_abbr", "a_loc", "a_short", "a_name", "a_score", "a_win",
       "provider", "provider_id", "h_ml_open", "a_ml_open", "h_ml_close", "a_ml_close",
       "h_ml", "a_ml", "spread", "total"]


def espn():
    jobs = []
    d = START
    while d <= END:
        jobs += [(tag, d) for tag in LEAGUES]
        d += timedelta(days=1)
    with ThreadPoolExecutor(8) as ex:
        games = [g for gs in ex.map(scoreboard, jobs) for g in gs]
    log("espn final games", len(games))
    with ThreadPoolExecutor(8) as ex:
        rows = [r for rs in ex.map(odds, games) for r in rs]
    log("espn odds rows", len(rows), "with close ML", sum(1 for r in rows if r[20]))
    with gzip.open(OUT / "games.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HDR)
        w.writerows(rows)


def kalshi():
    st = int(datetime(START.year, START.month, START.day, tzinfo=timezone.utc).timestamp())
    rows, seen = [], set()
    for tag, (_, _, s) in LEAGUES.items():
        n0 = len(rows)
        for path, extra in (("/markets", {"status": "settled"}), ("/historical/markets", {})):
            for m in paginate(path, "markets", {"series_ticker": s, "limit": 1000, "min_close_ts": st,
                                                **extra}, 200):
                tk = m.get("ticker")
                if tk in seen or m.get("result") not in ("yes", "no"):
                    continue
                seen.add(tk)
                rows.append([tag, tk, m.get("event_ticker"), m.get("yes_sub_title"), m.get("open_time"),
                             m.get("close_time"), m.get("occurrence_datetime"),
                             m.get("expected_expiration_time"), m.get("result"),
                             f(m.get("volume_fp", m.get("volume"))) or 0,
                             int(path.startswith("/historical"))])
        log("kalshi", s, len(rows) - n0)
    with gzip.open(OUT / "kalshi.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["league", "ticker", "event", "team", "open", "close", "occurrence", "expected_exp",
                    "result", "volume", "hist"])
        w.writerows(rows)


def main():
    kalshi()
    espn()


if __name__ == "__main__":
    try:
        main()
    finally:
        (OUT / "log.txt").write_text("\n".join(LOG))
