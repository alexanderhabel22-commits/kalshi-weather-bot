"""Do 15-min favourites flip more often in windows with a scheduled macro release?

usage: python release_test.py ECON_MARKETS_CSV DATA_DIR SYM [SYM...]
Release times come from Kalshi's own econ markets (they close a few minutes before the
print): CPI / jobs / PCE / GDP at 8:30 AM ET, Fed decision 2:00 PM ET (+ press conference
window 2:30 PM ET). Favourite = the side the hold model rates 71-95% at minutes 5-10
(5-10 minutes left). Control = the same clock time on weekdays with no release.
"""
from __future__ import annotations

import csv
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from model import features, fit_table, load, table_prob  # noqa: E402

SERIES = {"KXCPI": "cpi", "KXCPICORE": "cpi", "KXCPIYOY": "cpi", "KXPAYROLLS": "jobs",
          "KXU3": "jobs", "KXPCECORE": "pce", "KXGDP": "gdp", "KXFEDDECISION": "fomc",
          "KXFED": "fomc"}


def releases(path):
    out = {}
    for r in csv.DictReader(open(path)):
        kind = SERIES.get(r["series"])
        if not kind:
            continue
        try:
            c = datetime.fromisoformat(r["close"].replace("Z", "+00:00"))
        except ValueError:
            continue
        if c.minute not in (25, 29, 55, 59):
            continue
        ts = int(c.timestamp())
        ts = (ts // 1800 + 1) * 1800
        out[ts] = kind
        if kind == "fomc":
            out[ts + 1800] = "fomc_presser"
    return out


def main(econ, data, syms):
    rel = releases(econ)
    rel_days = {t // 86400 for t in rel}
    print(f"release windows: {len(rel)} ({sorted(set(rel.values()))}), "
          f"{datetime.fromtimestamp(min(rel), timezone.utc).date()} -> "
          f"{datetime.fromtimestamp(max(rel), timezone.utc).date()}")
    for sym in syms:
        f = Path(data) / f"{sym}.npz"
        if not f.exists():
            print(sym, "no data")
            continue
        w = load(f)
        t0 = w["t0"]
        tab = fit_table(w, np.ones(len(t0), bool))
        clock = t0 % 86400
        wkday = ((t0 // 86400) + 3) % 7 < 5
        rel_clocks = {t % 86400 for t in rel}
        is_rel = np.array([int(t) in rel for t in t0])
        kind = np.array([rel.get(int(t), "") for t in t0])
        ctrl = (~is_rel) & wkday & np.isin(clock, list(rel_clocks)) & \
            ~np.isin(t0 // 86400, list(rel_days)) & (t0 >= min(rel) - 86400 * 30)
        print(f"\n{sym}: release windows with data {is_rel.sum()}, control windows {ctrl.sum()}")
        groups = [("ALL releases", is_rel)] + [(k, kind == k) for k in sorted(set(rel.values()))] + \
                 [("control (same clock, no release)", ctrl)]
        for name, g in groups:
            fav_p, fav_win = [], []
            for k in range(5, 11):
                z, d, ok = features(w, k)
                p = table_prob(tab, k, np.nan_to_num(z))
                lp = np.where(d >= 0, p, 1 - p)
                lw = np.where(d >= 0, w["up"], 1 - w["up"])
                m = g & ok & (lp >= 0.71) & (lp <= 0.95)
                fav_p.extend(lp[m])
                fav_win.extend(lw[m])
            if not fav_p:
                continue
            a, b = np.array(fav_p), np.array(fav_win)
            se = math.sqrt(max(b.mean() * (1 - b.mean()), 1e-4) / len(b))
            print(f"  {name:34} favourite looks (window x minute) {len(a):5d}  model {a.mean():.3f}"
                  f"  actual hold {b.mean():.3f}  gap {100 * (b.mean() - a.mean()):+5.1f} pts ±{100 * se:.1f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
