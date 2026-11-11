"""Setups on Kalshi's OWN settlement prices (floor strike = 60 s average at open,
expiration_value = 60 s average at close), traded at the real minute-1 ask + taker fee.
Works for any 15-min series, no outside price feed needed (e.g. silver).

usage: python kalshi_bars.py DATA_DIR SERIES [SERIES...]
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from kalshi_compare import fee, kalshi_book  # noqa: E402
from setups import scan  # noqa: E402


def main(data, series):
    ms, q = kalshi_book(data, series)
    rows = []
    for tk, m in ms.items():
        try:
            o, c = float(m["floor"]), float(m["expiration_value"])
        except (TypeError, ValueError):
            continue
        rows.append((int(m["close_ts"]) - 900, o, c, 1.0 if m["result"] == "yes" else 0.0, tk))
    rows.sort()
    t0 = np.array([r[0] for r in rows])
    o = np.array([r[1] for r in rows])
    c = np.array([r[2] for r in rows])
    res = np.array([r[3] for r in rows])
    bars = dict(t0=t0, o=o, c=c, h=np.maximum(o, c), l=np.minimum(o, c), n=np.ones(len(t0)))
    sig = scan(bars)
    have = [i for i, r in enumerate(rows) if r[4] in q and q[r[4]].get(r[0] + 60)]
    split = t0[have[len(have) // 2]] if have else 0
    print(f"{series}: {len(rows)} windows, {len(have)} with a minute-1 quote; "
          f"halves split {split}")
    print(f"  {'setup':30} {'A n':>5} {'A win':>6} {'A px':>6} {'A c/ct':>8}   "
          f"{'B n':>5} {'B win':>6} {'B px':>6} {'B c/ct':>8}")
    for name, s in sig.items():
        if name.startswith(("hour_", "weekday_")):
            continue
        out = []
        for half in (0, 1):
            pnl, wins, pxs = [], [], []
            for i in have:
                if s[i] == 0 or (t0[i] >= split) != bool(half):
                    continue
                bid, ask = q[rows[i][4]][t0[i] + 60]
                px, win = (ask, res[i]) if s[i] > 0 else (1 - bid, 1 - res[i])
                if not 0.02 <= px <= 0.98:
                    continue
                pnl.append(win - px - fee(px))
                wins.append(win)
                pxs.append(px)
            if pnl:
                out.append(f"{len(pnl):5d} {np.mean(wins):6.3f} {np.mean(pxs):6.3f} "
                           f"{100 * np.mean(pnl):+5.1f}±{100 * np.std(pnl) / math.sqrt(len(pnl)):3.1f}")
            else:
                out.append(" " * 31)
        print(f"  {name:30} {out[0]}   {out[1]}")


if __name__ == "__main__":
    for s in sys.argv[2:]:
        main(sys.argv[1], s)
