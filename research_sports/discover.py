"""Discovery: dump raw samples of Kalshi game markets and ESPN's public odds.

Read-only public data; never trades. Writes into argv[1].
"""
from __future__ import annotations

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
    for lg, ev in (("football/nfl", "401873026"), ("hockey/nhl", "401892633"),
                   ("basketball/nba", "401810433")):
        tag = lg.split("/")[1]
        sport, league = lg.split("/")
        dump(f"espn_{tag}_summary", f"{E}/{lg}/summary", {"event": ev})
        o = dump(f"espn_{tag}_core_odds", f"{C}/{sport}/leagues/{league}/events/{ev}/competitions/{ev}/odds")
        for i, it in enumerate((o or {}).get("items") or []):
            ref = (it.get("$ref") or "")
            if ref:
                dump(f"espn_{tag}_odds_item{i}", ref.replace("http://", "https://"))
            pid = ((it.get("provider") or {}).get("id"))
            if pid and i < 3:
                dump(f"espn_{tag}_odds_hist{i}", f"{C}/{sport}/leagues/{league}/events/{ev}/competitions/{ev}/odds/{pid}/history/0/movement", {"limit": 200})
    dump("espn_nba_sb_nov", f"{E}/basketball/nba/scoreboard", {"dates": "20261102"})
    dump("espn_cfb_sb_sat", f"{E}/football/college-football/scoreboard", {"dates": "20261031", "groups": 80, "limit": 300})


if __name__ == "__main__":
    try:
        main()
    finally:
        (OUT / "log.txt").write_text("\n".join(LOG))
        print("\n".join(LOG))
