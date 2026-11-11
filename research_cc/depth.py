"""Research only: is Kalshi's global social trade feed a cached snapshot? How often does it
refresh and how many pages deep does it go? Read-only public data; never trades."""
import json
import sys
import time
from datetime import datetime

import requests

SOCIAL = "https://api.elections.kalshi.com/v1/social/trades"
RUN_S = int(sys.argv[1]) if len(sys.argv) > 1 else 120
s = requests.Session()
s.headers["User-Agent"] = "research-probe/1.0"


def ts(x):
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


changes, last_top, sweeps = [], None, []
end = time.time() + RUN_S
nxt_sweep = 0.0
while time.time() < end:
    r = s.get(SOCIAL, params={"limit": 100}, timeout=10).json()
    tr = r.get("trades") or []
    top = tr[0].get("trade_id") if tr else None
    now = time.time()
    if top != last_top:
        changes.append(round(now, 2))
        last_top = top
    if now >= nxt_sweep:                       # full-depth sweep right after a refresh
        nxt_sweep = now + 30
        cur, n, oldest, newest, pages, ids = r.get("cursor"), len(tr), now, 0, 1, set(t["trade_id"] for t in tr)
        for t in tr:
            c = ts(t.get("create_date")) or now
            oldest, newest = min(oldest, c), max(newest, c)
        a = time.time()
        while cur and pages < 200:
            j = s.get(SOCIAL, params={"limit": 100, "cursor": cur}, timeout=10).json()
            p = j.get("trades") or []
            if not p:
                break
            pages += 1
            for t in p:
                ids.add(t.get("trade_id"))
                c = ts(t.get("create_date")) or now
                oldest, newest = min(oldest, c), max(newest, c)
            cur = j.get("cursor")
        sweeps.append({"pages": pages, "unique": len(ids), "span_s": round(newest - oldest, 1),
                       "oldest_age_s": round(now - oldest, 1), "sweep_s": round(time.time() - a, 1),
                       "ended": not cur})
    time.sleep(0.25)
gaps = [round(b - a, 1) for a, b in zip(changes, changes[1:])]
print("RESULT " + json.dumps({"refresh_gaps_s": gaps, "sweeps": sweeps}))
