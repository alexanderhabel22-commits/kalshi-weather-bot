"""Research only: how late do trades reach Kalshi's public social trade feed?
Polls the global social feed (3 pages/s) and the public market trades API side by side for
a few minutes and prints delay stats. Read-only public data; never trades."""
import json
import sys
import time
from datetime import datetime

import requests

SOCIAL = "https://api.elections.kalshi.com/v1/social/trades"
V2 = "https://api.elections.kalshi.com/trade-api/v2/markets/trades"
RUN_S = int(sys.argv[1]) if len(sys.argv) > 1 else 240
PAGES = int(sys.argv[2]) if len(sys.argv) > 2 else 3
s = requests.Session()
s.headers["User-Agent"] = "research-probe/1.0"


def ts(x):
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def pct(v, q):
    v = sorted(v)
    return round(v[min(len(v) - 1, int(len(v) * q))], 1) if v else None


ages, sizes = {}, {}
soc, v2 = {}, {}            # trade_id -> (first_seen, create_ts, page, named)
newest_prev = 0.0
late_insert, unsorted_pages, polls, errs, v2_polls = 0, 0, 0, 0, 0
call_ms = []
end = time.time() + RUN_S
nxt_v2 = 0.0
while time.time() < end:
    t0 = time.time()
    cursor, newest_now = None, newest_prev
    for page in range(PAGES):
        try:
            a = time.time()
            r = s.get(SOCIAL, params={"limit": 100, **({"cursor": cursor} if cursor else {})}, timeout=10)
            call_ms.append((time.time() - a) * 1000)
            j = r.json()
        except Exception:  # noqa: BLE001
            errs += 1
            break
        tr = j.get("trades") or []
        cts = [ts(t.get("create_date")) for t in tr]
        ages.setdefault(page, []).extend(time.time() - c for c in cts if c)
        sizes.setdefault(page, []).append(len(tr))
        if any(x is not None and y is not None and x < y for x, y in zip(cts, cts[1:])):
            unsorted_pages += 1
        now = time.time()
        for t, c in zip(tr, cts):
            tid = t.get("trade_id")
            if tid and tid not in soc and c:
                soc[tid] = (now, c, page, bool(t.get("taker_nickname") or t.get("maker_nickname")))
                if polls and c < newest_prev - 5:
                    late_insert += 1
                newest_now = max(newest_now, c)
        cursor = j.get("cursor")
        if not cursor:
            break
    newest_prev = newest_now
    polls += 1
    if time.time() >= nxt_v2:
        nxt_v2 = time.time() + 2
        try:
            j = s.get(V2, params={"limit": 1000}, timeout=10).json()
            v2_polls += 1
            now = time.time()
            for t in j.get("trades") or []:
                tid, c = t.get("trade_id"), ts(t.get("created_time"))
                if tid and tid not in v2 and c:
                    v2[tid] = (now, c)
        except Exception:  # noqa: BLE001
            errs += 1
    time.sleep(max(0, 1 - (time.time() - t0)))

skip = 15  # first seconds see the backlog, not fresh trades
t_start = min([v[0] for v in soc.values()] or [0]) + skip
sl = [v[0] - v[1] for v in soc.values() if v[0] > t_start]
named = [v[0] - v[1] for v in soc.values() if v[0] > t_start and v[3]]
vl = [v[0] - v[1] for v in v2.values() if v[0] > t_start]
both = [soc[k][0] - v2[k][0] for k in soc if k in v2 and soc[k][0] > t_start and v2[k][0] > t_start]
pages = [v[2] for v in soc.values() if v[0] > t_start]
rate = len(sl) / max(1, RUN_S - skip)
out = {"social_trades": len(sl), "per_s": round(rate, 1), "polls": polls, "errors": errs,
       "social_lag": {q: pct(sl, q) for q in (0.1, 0.5, 0.75, 0.9, 0.99)},
       "named_lag": {q: pct(named, q) for q in (0.1, 0.5, 0.9)}, "named_n": len(named),
       "first_seen_page": {p: pages.count(p) for p in range(PAGES)},
       "page_age_p50": {p: pct(ages.get(p, []), 0.5) for p in range(PAGES)},
       "page_age_max": {p: pct(ages.get(p, []), 0.99) for p in range(PAGES)},
       "page_size": {p: pct(sizes.get(p, []), 0.5) for p in range(PAGES)},
       "late_inserted_gt5s": late_insert, "unsorted_pages": unsorted_pages,
       "call_ms": {q: pct(call_ms, q) for q in (0.5, 0.9)},
       "v2_trades": len(vl), "v2_lag": {q: pct(vl, q) for q in (0.1, 0.5, 0.9)},
       "matched_ids": len(both), "social_minus_v2_s": {q: pct(both, q) for q in (0.1, 0.5, 0.9)}}
print("RESULT " + json.dumps(out))
