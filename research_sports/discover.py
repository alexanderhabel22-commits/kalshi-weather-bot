"""Discovery: dump raw samples of Kalshi game markets and ESPN's public odds.

Read-only public data; never trades. Writes into argv[1].
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import requests

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
OUT.mkdir(parents=True, exist_ok=True)
K = "https://api.elections.kalshi.com/trade-api/v2"
E = "https://site.api.espn.com/apis/site/v2/sports"
C = "https://sports.core.api.espn.com/v2/sports"
S = requests.Session()
S.headers["User-Agent"] = "kalshi-research/0.1"
LOG = []


def dump(name, url, params=None):
    try:
        r = S.get(url, params=params, timeout=40)
        LOG.append(f"{name}: {r.status_code} {len(r.content)}B {r.url}")
        js = r.json()
        (OUT / f"{name}.json").write_text(json.dumps(js, indent=1)[:3_000_000])
        return js
    except Exception as e:  # noqa: BLE001
        LOG.append(f"{name}: ERROR {e!r}")
        return None


def main():
    dump("k_cutoff", K + "/historical/cutoff")
    for s in ("KXNBAGAME", "KXNFLGAME", "KXNHLGAME", "KXMLBGAME", "KXNCAAFGAME"):
        js = dump(f"k_{s}", K + "/markets", {"series_ticker": s, "status": "settled", "limit": 4})
        dump(f"k_{s}_hist", K + "/historical/markets", {"series_ticker": s, "limit": 4})
        if js and js.get("markets"):
            m = js["markets"][0]
            ct = int(dt.datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")).timestamp())
            dump(f"k_{s}_candles", f"{K}/series/{s}/markets/{m['ticker']}/candlesticks",
                 {"start_ts": ct - 86400 * 3, "end_ts": ct, "period_interval": 60})
    sb = dump("espn_nba_sb", f"{E}/basketball/nba/scoreboard", {"dates": "20261103"})
    dump("espn_nba_sb_old", f"{E}/basketball/nba/scoreboard", {"dates": "20260115"})
    dump("espn_nfl_sb", f"{E}/football/nfl/scoreboard", {"dates": "20261101"})
    dump("espn_nhl_sb", f"{E}/hockey/nhl/scoreboard", {"dates": "20261103"})
    dump("espn_mlb_sb", f"{E}/baseball/mlb/scoreboard", {"dates": "20260715"})
    dump("espn_cfb_sb", f"{E}/football/college-football/scoreboard", {"dates": "20261101", "groups": 80})
    if sb and sb.get("events"):
        ev = sb["events"][0]["id"]
        dump("espn_nba_summary", f"{E}/basketball/nba/summary", {"event": ev})
        dump("espn_nba_core_odds", f"{C}/basketball/leagues/nba/events/{ev}/competitions/{ev}/odds")


if __name__ == "__main__":
    try:
        main()
    finally:
        (OUT / "log.txt").write_text("\n".join(LOG))
        print("\n".join(LOG))
