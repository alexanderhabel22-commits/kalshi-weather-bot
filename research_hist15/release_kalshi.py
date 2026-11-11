"""Release windows judged on Kalshi's own quotes (no outside price feed needed).

usage: python release_kalshi.py ECON_MARKETS_CSV DATA_DIR SERIES [SERIES...]
Favourite = side whose bid is 71-95c at minutes 5-10 of the window. Result per look:
actual (1/0) minus that bid, i.e. what a resting bid earned if it filled every time.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from kalshi_compare import kalshi_book  # noqa: E402
from release_test import releases  # noqa: E402


def main(econ, data, series):
    rel = releases(econ)
    for s in series:
        ms, q = kalshi_book(data, s)
        acc = {"release": [], "other": []}
        wins = {"release": set(), "other": set()}
        for tk, m in ms.items():
            if tk not in q:
                continue
            t0 = int(m["close_ts"]) - 900
            res = 1.0 if m["result"] == "yes" else 0.0
            g = "release" if t0 in rel else "other"
            for k in range(5, 11):
                bq = q[tk].get(t0 + 60 * k)
                if not bq:
                    continue
                bid, ask = bq
                if (bid + ask) / 2 >= 0.5:
                    px, win = bid, res
                else:
                    px, win = 1 - ask, 1 - res
                if 0.71 <= px <= 0.95:
                    acc[g].append((px, win))
                    wins[g].add(tk)
        line = [s]
        for g in ("release", "other"):
            a = np.array(acc[g]) if acc[g] else np.zeros((0, 2))
            if len(a):
                d = a[:, 1] - a[:, 0]
                line.append(f"{g}: {len(wins[g])} windows, held {a[:, 1].mean():.3f} at avg bid "
                            f"{a[:, 0].mean():.3f}, {100 * d.mean():+.1f}c ±{100 * d.std() / math.sqrt(max(1, len(wins[g]))):.1f}")
        print("  ".join(line))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
