"""Find sharp repricings ('jumps') in data-settled markets and when they happen (ET).

usage: python research_src/jumps.py RUN_DIR
A jump = a market's trade price moves >= 25c within 20 minutes and holds for 30 minutes.
For each jump: time (ET), size, volume traded at the stale side inside the jump window
(contracts that someone with the data first could have taken), and the dollars that
stale volume was worth (|new level - fill price| x contracts).
"""
import csv, gzip, sys, collections
from datetime import datetime
from zoneinfo import ZoneInfo
ET = ZoneInfo("America/New_York")
D = sys.argv[1]
mk = {r["ticker"]: r for r in csv.DictReader(gzip.open(f"{D}/markets.csv.gz", "rt"))}
tr = collections.defaultdict(list)
for r in csv.DictReader(gzip.open(f"{D}/trades.csv.gz", "rt")):
    tr[r["ticker"]].append((int(r["ts"]), float(r["yes_price"]), float(r["count"]), r["taker_side"]))
W, HOLD, MOVE = 1200, 1800, 0.25
out = []
for tk, t in tr.items():
    t.sort()
    m = mk.get(tk)
    if not m:
        continue
    i, n = 0, len(t)
    last_jump_end = 0
    for j in range(n):
        ts0, p0 = t[j][0], t[j][1]
        if ts0 < last_jump_end:
            continue
        # look for the first trade within W that is >= MOVE away
        k = j + 1
        while k < n and t[k][0] - ts0 <= W and abs(t[k][1] - p0) < MOVE:
            k += 1
        if k >= n or t[k][0] - ts0 > W:
            continue
        p1 = t[k][1]
        up = p1 > p0
        # hold: trades in the next HOLD seconds stay within 10c of p1 or beyond
        later = [x for x in t[k:] if x[0] <= t[k][0] + HOLD]
        hold = [x for x in t if t[k][0] + HOLD < x[0] <= t[k][0] + 3 * HOLD]
        lvl = sorted(x[1] for x in later)[len(later) // 2]
        if any((x[1] < lvl - 0.12) if up else (x[1] > lvl + 0.12) for x in hold):
            continue
        # stale volume: trades between j and k on the old side of the new level
        stale = [x for x in t[j:k + 1] if (x[1] < lvl - 0.05 if up else x[1] > lvl + 0.05)]
        sv = sum(x[2] for x in stale)
        sd = sum(x[2] * abs(lvl - x[1]) for x in stale)
        dt = datetime.fromtimestamp(t[k][0], ET)
        out.append([m["series"], tk, m["result"], dt.strftime("%Y-%m-%d %H:%M"), dt.strftime("%a"),
                    round(p0, 2), round(lvl, 2), t[k][0] - ts0, round(sv), round(sd, 2),
                    round((int(m["close_ts"]) - t[k][0]) / 3600, 1)])
        last_jump_end = t[k][0] + HOLD
with open(f"{D}/jumps.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["series", "ticker", "result", "time_et", "dow", "from", "to", "secs", "stale_ct", "stale_usd", "h_to_close"])
    w.writerows(out)
print(len(out), "jumps")
