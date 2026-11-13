"""Download Kalshi's own minute Weather Index history (public live_data endpoint).

GET /live_data/weather/{city}?from=<ms>&to=<ms>. Read-only. Saves raw JSON
per city-day into argv[1]/index/<city>/<date>.json plus a probe log.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASES = ["https://external-api.kalshi.com/trade-api/v2",
         "https://api.elections.kalshi.com/trade-api/v2"]
CITY_NAMES = {"miami": ["miami"], "nyc": ["nyc", "new_york", "new-york", "newyork"],
              "chicago": ["chicago", "chicago_metro", "chicago-metro"],
              "la": ["la-coastal"]}
START = datetime(2026, 11, 5, tzinfo=timezone.utc)
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
S = requests.Session()
S.headers["User-Agent"] = "kalshi-weather-research/0.1"
log = []


def get(url, params):
    for i in range(5):
        try:
            r = S.get(url, params=params, timeout=60)
        except requests.RequestException as e:
            log.append(f"ERR {url} {e}")
            time.sleep(3)
            continue
        if r.status_code == 429:
            time.sleep(3 * (i + 1))
            continue
        time.sleep(0.1)
        return r
    return None


def find_city(key):
    t1 = int(datetime.now(timezone.utc).timestamp() * 1000)
    for base in BASES:
        for name in CITY_NAMES[key]:
            r = get(f"{base}/live_data/weather/{name}", {"from": t1 - 3600_000, "to": t1})
            log.append(f"probe {base} {name}: {r.status_code if r is not None else None} "
                       f"{r.text[:300] if r is not None else ''}")
            if r is not None and r.ok:
                return base, name
    return None, None


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for key in CITY_NAMES:
        base, name = find_city(key)
        if not base:
            continue
        d = START
        end = datetime.now(timezone.utc)
        first = True
        while d < end:
            e = d + timedelta(days=1)
            p = {"from": int(d.timestamp() * 1000), "to": int(e.timestamp() * 1000)}
            if first:
                r = get(f"{base}/live_data/weather/{name}", dict(p, detailed="true"))
                if r is not None:
                    (OUT / f"detailed_sample_{key}.json").write_text(r.text[:200000])
                first = False
            r = get(f"{base}/live_data/weather/{name}", p)
            if r is not None and r.ok:
                (OUT / "index" / key).mkdir(parents=True, exist_ok=True)
                (OUT / "index" / key / f"{d:%Y-%m-%d}.json").write_text(r.text)
            else:
                log.append(f"{key} {d:%F}: {r.status_code if r is not None else None} "
                           f"{r.text[:200] if r is not None else ''}")
            d = e
        log.append(f"done {key} via {base} {name}")
    (OUT / "index_log.txt").write_text("\n".join(log))
    print("\n".join(log))
