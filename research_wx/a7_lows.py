"""Late-day lows: sell the brackets that need the temperature to fall below the low
already recorded today (resting NO buy 1c inside the offer). Slices and stress tests.
usage: python research_wx/a7_lows.py data.pkl [metric]"""
import pickle
import sys
from datetime import date

import numpy as np

sys.path.insert(0, "research_wx")
import lib  # noqa: E402
import sim  # noqa: E402

D = lib.load(sys.argv[1])
METRIC = sys.argv[2] if len(sys.argv) > 2 else "low"
SG = 1 if METRIC == "high" else -1
days = [d for d in D["days"] if d["day"] >= date(2025, 10, 1) and d["metric"] == METRIC]
HS = (14, 12, 10, 9, 8, 7, 6, 5, 4, 3)


def fc_rest(d, T):
    """Day-old hourly forecasts (already public a day earlier): most extreme value still
    to come before the climate day ends, averaged over models. nan if none."""
    out = []
    for model, rec in D["fc"].get(d["icao"], {}).items():
        a = rec.get("d1")
        if a is None:
            continue
        i0 = int(np.ceil((T - rec["t0"]) / 3600))
        i1 = int(round((d["w1"] - rec["t0"]) / 3600))
        v = a[max(i0, 0):i1 + 1]
        if len(v) and np.isfinite(v).any():
            out.append(np.nanmax(v) if METRIC == "high" else np.nanmin(v))
    return float(np.mean(out)) if out else np.nan


rec = []
for d in days:
    o = D["obs"].get(d["icao"])
    if o is None:
        continue
    for H in HS:
        T = lib.day_T(d, H)
        m = (o[:, 0] >= d["w0"]) & (o[:, 0] <= T)
        if m.sum() < 3 or T - o[m, 0][-1] > 2 * 3600:
            continue
        v = o[m, 1]
        ext = v.max() if METRIC == "high" else v.min()
        cur = v[-1]
        k = int(np.floor(ext + 0.5))
        rest = fc_rest(d, T)
        for lo, hi, b, a, tl, th, yes, tk, later in lib.quotes(d, T):
            if not (np.isfinite(b) and np.isfinite(a)) or not (0 < b < a < 1):
                continue
            need = (METRIC == "low" and hi is not None and hi < k) or (METRIC == "high" and lo is not None and lo > k)
            holds = (lo is None or k >= lo) and (hi is None or k <= hi)
            if not (need or holds):
                continue
            gap = ((k - hi) if METRIC == "low" else (lo - k)) if need else 0   # degrees still needed
            for side, px, win in (("yes", round(b + 0.01, 2), 1.0 if yes else 0.0),
                                  ("no", round(1 - (a - 0.01), 2), 0.0 if yes else 1.0)):
                other = a if side == "yes" else 1 - b
                if not (px < other - 1e-9 and 0.03 <= px <= 0.97):
                    continue
                L = later
                if L is None or not len(L):
                    continue
                col = L[:, 3] if side == "yes" else L[:, 4]
                lvl = px if side == "yes" else 1 - px
                hit = (col <= lvl + 1e-9) if side == "yes" else (col >= lvl - 1e-9)
                thru = (col < lvl - 1e-9) if side == "yes" else (col > lvl + 1e-9)
                hit, thru = hit & np.isfinite(col), thru & np.isfinite(col)
                if not hit.any():
                    continue
                first = int(np.argmax(hit))
                rec.append(dict(tk=tk, day=d["day"], series=d["series"], city=d["city"], H=H, side=side,
                                pos="need" if need else "holds", pnl=win - px, px=px, sp=a - b, gap=gap,
                                togo=SG * (ext - cur), fcgap=SG * (rest - ext) if not np.isnan(rest) else np.nan,
                                first=first, thru=bool(thru.any()), vol=float(np.nansum(L[:, 5]))))
pickle.dump(rec, open(sys.argv[1] + f".late_{METRIC}.pkl", "wb"))


def show(R, label):
    if len(R) < 15:
        return f"{label}: n={len(R)}"
    m, se = sim.cl([r["pnl"] for r in R], [r["day"] for r in R])
    return f"{label}: {100 * m:+5.1f}c +-{100 * se:.1f}  n={len(R):5d}  avg price {np.mean([r['px'] for r in R]):.2f}"


PER = [("Oct25-Aug26", date(2025, 10, 1), date(2026, 8, 31)), ("Sep-Nov26", date(2026, 9, 1), date(2026, 12, 31))]
for pos, side in (("need", "no"), ("holds", "yes"), ("need", "yes"), ("holds", "no")):
    print(f"\n##### {METRIC}s: bracket {'needs a further move' if pos == 'need' else 'holds the extreme so far'}, buy {side.upper()}")
    base = [r for r in rec if r["pos"] == pos and r["side"] == side]
    for H in HS:
        print(f"  {H:2d}h  " + "  |  ".join(show([r for r in base if r["H"] == H and a <= r["day"] <= b], n) for n, a, b in PER))
    if (pos, side) not in (("need", "no"), ("holds", "yes")):
        continue
    B9 = [r for r in base if r["H"] in (9, 6)]
    print("  -- slices at 9h and 6h before close (pooled)")
    SL = [("trade went through our price", lambda r: r["thru"]), ("filled within 1h", lambda r: r["first"] < 1),
          ("filled within 3h", lambda r: r["first"] < 3), ("filled after 3h+", lambda r: r["first"] >= 3),
          ("spread <=3c", lambda r: r["sp"] <= 0.031), ("spread 4-7c", lambda r: 0.031 < r["sp"] <= 0.071),
          ("spread >=8c", lambda r: r["sp"] > 0.071),
          ("price <30c", lambda r: r["px"] < 0.30), ("price 30-60c", lambda r: 0.30 <= r["px"] < 0.60),
          ("price 60-85c", lambda r: 0.60 <= r["px"] < 0.85), ("price 85-97c", lambda r: r["px"] >= 0.85),
          ("adjacent bracket", lambda r: r["gap"] <= 2 and r["pos"] == "need"), ("far bracket", lambda r: r["gap"] > 2),
          ("temp within 3F of extreme", lambda r: r["togo"] > -3), ("temp 3-8F away", lambda r: -8 < r["togo"] <= -3),
          ("temp 8F+ away", lambda r: r["togo"] <= -8),
          ("day-old forecast: no new extreme", lambda r: r["fcgap"] < -1), ("forecast: close call", lambda r: -1 <= r["fcgap"] <= 1),
          ("forecast: new extreme coming", lambda r: r["fcgap"] > 1)]
    for lab, f in SL:
        print(f"  {lab:34s} " + "  |  ".join(show([r for r in B9 if f(r) and a <= r["day"] <= b], n) for n, a, b in PER))
    print("  -- by city (9h and 6h pooled)")
    for c in sorted({r["city"] for r in B9}):
        print(f"  {c:34s} " + "  |  ".join(show([r for r in B9 if r["city"] == c and a <= r["day"] <= b], n) for n, a, b in PER))
    print("  -- by month (9h)")
    for ym in sorted({(r["day"].year, r["day"].month) for r in base}):
        print("  ", ym, show([r for r in base if r["H"] == 9 and (r["day"].year, r["day"].month) == ym], ""))
