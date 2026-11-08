"""Gap test A: 15-minute markets (commodities, FX, rates; crypto as control).

usage: python research_gaps/an_m15.py RUN_DIR
Per series, one row per method, cents per contract after fees, 95% CI over markets
(each 15-minute market is its own event):
  fade_bq   maker owns the favourite after a taker buys a 5-29c longshot; counted only
            if a later print goes through that price within the market (back of queue).
            Maker fee charged at a quarter of the taker formula (conservative).
  fav_tk    bot snapshot: every 5 min, first time a side's last print is 85-95c, buy that
            side at its next taker print within 2 min (taker fee 0.07 p(1-p)).
Split: Aug-Sep vs Oct-Nov 2026 closes.
"""
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

RUN = sys.argv[1]
sm = pd.read_csv(f"{RUN}/sample.csv.gz")
tr = pd.read_csv(f"{RUN}/trades.csv.gz")
res = dict(zip(sm.ticker, sm.result == "yes"))
ser = dict(zip(sm.ticker, sm.series))
per = dict(zip(sm.ticker, np.where(sm.close_ts < 1790812800, "Aug-Sep", "Oct-Nov")))
openx = dict(zip(sm.ticker, sm.open_ts))


def tfee(p):
    return 0.07 * p * (1 - p)


out = defaultdict(list)  # (series, method, period) -> per-market cents
for tk, g in tr.groupby("ticker"):
    if tk not in res:
        continue
    g = g.sort_values("t")
    t, y, c, side = g.t.values, g.yes_price.values, g["count"].values, g.taker_side.values
    yes_won = res[tk]
    # side price paid by the taker
    sp = np.where(side == "yes", y, 1 - y)
    # --- maker fade, back of queue
    pnl, w = 0.0, 0.0
    suf = {}
    for s_name in ("yes", "no"):
        a = np.where(side == s_name, sp, -1.0)
        m = np.maximum.accumulate(a[::-1])[::-1]
        suf[s_name] = np.append(m[1:], -1.0)  # max later price on that side
    for i in range(len(t)):
        p = sp[i]
        if not 0.05 <= p < 0.30 or side[i] not in suf:
            continue
        if suf[side[i]][i] <= p + 1e-9:
            continue
        fav_won = (not yes_won) if side[i] == "yes" else yes_won
        cost = 1 - p
        pnl += c[i] * ((1.0 if fav_won else 0.0) - cost - tfee(cost) / 4)
        w += c[i]
    if w:
        out[(ser[tk], "fade_bq", per[tk])].append(100 * pnl / w)
    # --- taker favourite snapshot
    grid = np.arange(openx[tk] + 300, t[-1], 300)
    done = False
    for gt in grid:
        j = np.searchsorted(t, gt, side="right") - 1
        if j < 0:
            continue
        for s_name, last in (("yes", y[j]), ("no", 1 - y[j])):
            if 0.85 <= last < 0.95:
                k = np.where((t > gt) & (t <= gt + 120) & (side == s_name))[0]
                if len(k):
                    p = sp[k[0]]
                    won = yes_won if s_name == "yes" else not yes_won
                    out[(ser[tk], "fav_tk", per[tk])].append(
                        100 * ((1.0 if won else 0.0) - p - tfee(p)))
                done = True
                break
        if done:
            break

rows = []
for (s, m, pr), v in out.items():
    v = np.array(v)
    rows.append((s, m, pr, len(v), v.mean(), 1.96 * v.std(ddof=1) / np.sqrt(len(v)) if len(v) > 1 else np.nan))
df = pd.DataFrame(rows, columns=["series", "method", "period", "n", "c", "ci"])
piv = df.pivot_table(index=["series", "method"], columns="period", values=["n", "c", "ci"])
pd.set_option("display.width", 200)
print(piv.round(2).to_string())
