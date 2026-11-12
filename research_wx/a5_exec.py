"""Execution: how long should a day-before order rest, and how deep inside the spread?
Same picks as the live recipe; only the order handling changes.
usage: python research_wx/a5_exec.py rows36.pkl"""
import pickle
import sys
from datetime import date

import numpy as np

sys.path.insert(0, "research_wx")
import sim  # noqa: E402

rows = pickle.load(open(sys.argv[1], "rb"))
H = int(sys.argv[2]) if len(sys.argv) > 2 else 36
KEYS = ["gfs_seamless.d1", "ecmwf_ifs025.d1", "icon_seamless.d1", "gem_seamless.d1"]
sim.combine_invmse(rows, KEYS)
B = sim.brackets(rows, H)
PER = (("Jan-Aug26", date(2026, 1, 1), date(2026, 8, 31)), ("Sep-Nov26", date(2026, 9, 1), date(2026, 12, 1)))
print("All orders the recipe would place (filled or not), value at the order price:")
tr, _ = sim.walk(B, 0.06, all_orders=True)
for name, a, b in PER:
    T = [t for t in tr if a <= t[0] <= b]
    print("  ", name, sim.summary(T, "every order "), "| fill rate %.0f%%" % (100 * np.mean([t[5] for t in T])))
    print("  ", name, sim.summary([t for t in T if t[5]], "filled      "))
    print("  ", name, sim.summary([t for t in T if not t[5]], "never filled"))
print("\nOrder lifetime (cancel if not filled after N hours):")
for life in (1, 2, 3, 4, 6, 9, 12, 18, 24, None):
    tr, _ = sim.walk(B, 0.06, life=life)
    print(f"  life {str(life):>4s}h  " + "  |  ".join(sim.summary([t for t in tr if a <= t[0] <= b], name) for name, a, b in PER))
print("\nHow far inside the spread (rest until close):")
for inside in (0.0, 0.01, 0.02, 0.03, 0.05):
    tr, _ = sim.walk(B, 0.06, inside=inside)
    print(f"  bid+{int(inside * 100)}c  " + "  |  ".join(sim.summary([t for t in tr if a <= t[0] <= b], name) for name, a, b in PER))
print("\nEdge threshold (rest until close):")
for thr in (0.03, 0.06, 0.09, 0.12, 0.16):
    tr, _ = sim.walk(B, thr)
    print(f"  thr {int(thr * 100):2d}c  " + "  |  ".join(sim.summary([t for t in tr if a <= t[0] <= b], name) for name, a, b in PER))
