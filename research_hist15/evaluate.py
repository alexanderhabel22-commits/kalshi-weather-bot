"""Judge setups on separate train/test years with a multiple-testing haircut.

usage: python evaluate.py DATA_DIR SYM [SYM...]
Train = through 2022, gap year 2023, test = 2024 onward. A setup survives only if it
passes Benjamini-Hochberg (5%) across EVERY test run on the train years AND its test-year
hit rate clears the Kalshi break-even (entry ~51c ask + ~2c taker fee => 53%).
In-window setups are judged against the hold model (the fair price Kalshi already shows),
needing +3 points over it out of sample.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from model import features, fit_table, load, table_prob  # noqa: E402
from setups import bars_from_windows, scan, scan_inside  # noqa: E402

BREAKEVEN = 0.53
INSIDE_MARGIN = 0.03


def pval(hits, n):
    if n == 0:
        return 1.0
    z = (hits - n / 2) / math.sqrt(n / 4)
    return math.erfc(abs(z) / math.sqrt(2))  # two-sided


def bh(ps, q=0.05):
    ps = np.asarray(ps)
    order = np.argsort(ps)
    m = len(ps)
    ok = np.zeros(m, bool)
    thr = q * (np.arange(1, m + 1) / m)
    passed = np.flatnonzero(ps[order] <= thr)
    if len(passed):
        ok[order[: passed.max() + 1]] = True
    return ok


def years(t0):
    return np.array([datetime.fromtimestamp(int(t), timezone.utc).year for t in t0])


def run(data, syms):
    rows = []
    for sym in syms:
        f = Path(data) / f"{sym}.npz"
        if not f.exists():
            continue
        w = load(f)
        yr = years(w["t0"])
        tr, te = yr <= 2022, yr >= 2024
        up = w["up"]
        bars = bars_from_windows(w)
        for name, s in scan(bars).items():
            for lab, m in (("train", tr), ("test", te)):
                pass
            res = {}
            for lab, m in (("train", tr), ("test", te)):
                sel = m & (s != 0) & np.isfinite(up)
                hit = ((s[sel] > 0) == (up[sel] > 0.5))
                res[lab] = (int(sel.sum()), float(hit.mean()) if sel.sum() else float("nan"))
            n, hr = res["train"]
            # the evaluation may flip a setup's direction based on TRAIN only
            flip = hr < 0.5
            ntr, htr = res["train"]
            nte, hte = res["test"]
            if flip:
                htr, hte = 1 - htr, 1 - hte
            rows.append(dict(sym=sym, kind="pre", setup=name + ("(flipped)" if flip else ""),
                             n_tr=ntr, hit_tr=htr, n_te=nte, hit_te=hte,
                             p=pval(htr * ntr, ntr)))
        tab = fit_table(w, tr)
        for k in (3, 5, 8, 10, 12):
            z, d, ok = features(w, k)
            pt = table_prob(tab, k, np.nan_to_num(z))
            lead_p = np.where(d >= 0, pt, 1 - pt)
            lead_win = np.where(d >= 0, up, 1 - up)
            for name, s in scan_inside(w, k).items():
                res = {}
                for lab, m in (("train", tr), ("test", te)):
                    sel = m & ok & (s != 0)
                    # backing side = leader if s>0 else trailer
                    p_side = np.where(s[sel] > 0, lead_p[sel], 1 - lead_p[sel])
                    win = np.where(s[sel] > 0, lead_win[sel], 1 - lead_win[sel])
                    res[lab] = (int(sel.sum()), float(np.mean(win - p_side)) if sel.sum() else
                                float("nan"), float(np.mean(p_side)) if sel.sum() else float("nan"))
                ntr, etr, ptr = res["train"]
                nte, ete, pte = res["test"]
                flip = etr < 0
                if flip:
                    etr, ete = -etr, -ete
                se = math.sqrt(max(ptr * (1 - ptr), 1e-4) / max(ntr, 1))
                p = math.erfc(abs(etr) / se / math.sqrt(2)) if ntr else 1.0
                rows.append(dict(sym=sym, kind=f"in@{k}m", setup=name + ("(flipped)" if flip else ""),
                                 n_tr=ntr, hit_tr=etr, n_te=nte, hit_te=ete, p=p, price=pte))
    ok = bh([r["p"] for r in rows])
    for r, b in zip(rows, ok):
        r["bh"] = bool(b)
        if r["kind"] == "pre":
            r["survives"] = bool(b and r["hit_te"] >= BREAKEVEN and r["n_te"] >= 300)
        else:
            r["survives"] = bool(b and r["hit_te"] >= INSIDE_MARGIN and r["n_te"] >= 300)
    return rows


def report(rows, top=40):
    print(f"tests run: {len(rows)}; pass BH on train: {sum(r['bh'] for r in rows)}; "
          f"survive out of sample: {sum(r['survives'] for r in rows)}")
    rows = sorted(rows, key=lambda r: -(r["hit_te"] if r["kind"] == "pre" else 0.5 + r["hit_te"]))
    print(f"{'sym':5} {'kind':7} {'setup':38} {'n_tr':>7} {'tr':>6} {'n_te':>7} {'te':>6} {'BH':>3} {'OK':>3}")
    for r in rows[:top]:
        fmt = (lambda x: f"{x:6.3f}") if r["kind"] == "pre" else (lambda x: f"{x:+6.3f}")
        print(f"{r['sym']:5} {r['kind']:7} {r['setup'][:38]:38} {r['n_tr']:7d} {fmt(r['hit_tr'])} "
              f"{r['n_te']:7d} {fmt(r['hit_te'])} {'y' if r['bh'] else '.':>3} "
              f"{'Y' if r['survives'] else '.':>3}")


if __name__ == "__main__":
    import csv
    rows = run(sys.argv[1], sys.argv[2:])
    report(rows)
    out = Path(sys.argv[1]) / "setups_results.csv"
    with open(out, "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=sorted({k for r in rows for k in r}))
        wr.writeheader()
        wr.writerows(rows)
