"""The current day-before recipe, replayed and sliced (where does it win or lose?).
usage: python research_wx/a4_current.py data.pkl.rows36.pkl"""
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
ok = [r for r in rows if not np.isnan(r["mu"])]
for name, a, b in (("Oct25-Aug26", date(2025, 10, 1), date(2026, 8, 31)), ("Sep-Nov26", date(2026, 9, 1), date(2026, 12, 1))):
    R = [r for r in ok if a <= r["d"]["day"] <= b]
    print(name, "n", len(R), "model MAE %.2f" % np.mean([abs(r["y"] - r["mu"]) for r in R]),
          "market MAE %.2f" % np.mean([abs(r["y"] - r["market"]) for r in R]),
          "avg model sd %.2f, market sd %.2f" % (np.mean([r["sd"] for r in R]), np.mean([r["market_sd"] for r in R])))
B = sim.brackets(rows, H)
tr, coefs = sim.walk(B, 0.06)
print("blend coefficients by month (const, market, model):")
for ym, w in coefs.items():
    print("  ", ym, np.round(w, 3))
SL = [("all", lambda t: True), ("YES", lambda t: t[1] == "yes"), ("NO", lambda t: t[1] == "no"),
      ("highs", lambda t: t[4]["metric"] == "high"), ("lows", lambda t: t[4]["metric"] == "low"),
      ("spread<=3c", lambda t: t[4]["a"] - t[4]["b"] <= 0.031), ("spread 4-7c", lambda t: 0.031 < t[4]["a"] - t[4]["b"] <= 0.071),
      ("spread>=8c", lambda t: t[4]["a"] - t[4]["b"] > 0.071),
      ("price<15c", lambda t: t[2] < 0.15), ("price 15-40c", lambda t: 0.15 <= t[2] < 0.40),
      ("price 40-70c", lambda t: 0.40 <= t[2] < 0.70), ("price>=70c", lambda t: t[2] >= 0.70)]
for name, a, b in (("Jan-Aug26", date(2026, 1, 1), date(2026, 8, 31)), ("Sep-Nov26", date(2026, 9, 1), date(2026, 12, 1))):
    print("--", name)
    for lab, f in SL:
        print("  ", sim.summary([t for t in tr if a <= t[0] <= b and f(t)], f"{lab:12s}"))
