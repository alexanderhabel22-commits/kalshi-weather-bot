"""Judge the hold model and the chart setups against REAL Kalshi 15-min prices.

usage: python kalshi_compare.py DATA_DIR SERIES SYM
  SERIES  e.g. KXBTC15M (needs <SERIES>_markets.csv.gz + <SERIES>_candles.csv.gz)
  SYM     price file, e.g. BTC (needs <SYM>.npz)

Entry = the quoted ask on the side we buy (YES ask, or 1 - YES bid for NO) at the end of
the given minute, plus the Kalshi taker fee ceil(7 * p * (1 - p)) cents per contract.
Profit is scored on Kalshi's actual result. Split: first half of the dates = train,
second half = test.
"""
from __future__ import annotations

import csv
import io
import math
import sys
import zlib
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from model import features, fit_table, load, table_prob  # noqa: E402
from setups import bars_from_windows, scan  # noqa: E402


def read_gz(path):
    raw = open(path, "rb").read()
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        txt = d.decompress(raw)
    except zlib.error:
        txt = b""
    return list(csv.DictReader(io.StringIO(txt.decode(errors="ignore"))))


def fee(p):
    return math.ceil(round(7 * p * (1 - p), 6)) / 100


def kalshi_book(data, series):
    ms = {m["ticker"]: m for m in read_gz(Path(data) / f"{series}_markets.csv.gz")}
    q = defaultdict(dict)
    for r in read_gz(Path(data) / f"{series}_candles.csv.gz"):
        try:
            q[r["ticker"]][int(r["t"])] = (float(r["bid"]), float(r["ask"]))
        except (ValueError, TypeError, KeyError):
            continue
    return ms, q


def main(data, series, sym):
    ms, q = kalshi_book(data, series)
    w = load(Path(data) / f"{sym}.npz")
    t_idx = {int(t): i for i, t in enumerate(w["t0"])}
    yrs = np.array([int(t) for t in w["t0"]])
    tab = fit_table(w, yrs < 1735689600)  # hold model fit on pre-2025 only
    sig = scan(bars_from_windows(w))
    rows = []
    for tk, m in ms.items():
        if tk not in q:
            continue
        close = int(m["close_ts"])
        t0 = close - 900
        i = t_idx.get(t0)
        res = 1.0 if m["result"] == "yes" else 0.0
        rows.append((tk, t0, i, res))
    rows.sort(key=lambda r: r[1])
    print(f"{series}: {len(ms)} markets listed, {len(rows)} with Kalshi prices, "
          f"{sum(r[2] is not None for r in rows)} matched to {sym} minutes")
    if not rows:
        return
    split = rows[len(rows) // 2][1]
    # feed agreement: does our feed's settle-vs-strike match Kalshi's result?
    agree = [((w["up"][i] > 0.5) == (res > 0.5)) for _, _, i, res in rows
             if i is not None and np.isfinite(w["up"][i])]
    print(f"  our feed agrees with Kalshi's result {np.mean(agree):.3f} of the time (n={len(agree)})")

    # 1) pre-window setups traded at the minute-1 ask
    print("  -- setups, buy at the minute-1 ask, taker fee, Kalshi result --")
    print(f"  {'setup':34} {'half':5} {'n':>5} {'win':>6} {'avg px':>7} {'c/ct':>7}")
    out = {}
    for name, s in sig.items():
        if name.startswith(("hour_", "weekday_")):
            continue
        for half in ("train", "test"):
            pnl, wins, pxs = [], [], []
            for tk, t0, i, res in rows:
                if i is None or s[i] == 0 or (half == "train") != (t0 < split):
                    continue
                bq = q[tk].get(t0 + 60)
                if not bq:
                    continue
                bid, ask = bq
                if s[i] > 0:
                    px, win = ask, res
                else:
                    px, win = 1 - bid, 1 - res
                if not 0.02 <= px <= 0.98:
                    continue
                pnl.append(win - px - fee(px))
                wins.append(win)
                pxs.append(px)
            out[(name, half)] = (len(pnl), np.mean(wins) if wins else np.nan,
                                 np.mean(pxs) if pxs else np.nan,
                                 100 * np.mean(pnl) if pnl else np.nan,
                                 100 * np.std(pnl) / math.sqrt(len(pnl)) if len(pnl) > 1 else np.nan)
    keyed = sorted({k[0] for k in out}, key=lambda n: -np.nan_to_num(out[(n, "train")][3], nan=-99))
    for name in keyed:
        for half in ("train", "test"):
            n, wr, px, c, se = out[(name, half)]
            if n:
                print(f"  {name:34} {half:5} {n:5d} {wr:6.3f} {px:7.3f} {c:+7.2f} ±{se:4.2f}")

    # 2) hold model vs Kalshi asks inside the window
    print("  -- hold model vs Kalshi: buy the side the model rates >= ask + fee + margin --")
    for margin in (0.0, 0.03, 0.06):
        for k in (3, 5, 8, 10, 12, 13):
            z, d, ok = features(w, k)
            for half in ("train", "test"):
                pnl = []
                for tk, t0, i, res in rows:
                    if i is None or not ok[i] or (half == "train") != (t0 < split):
                        continue
                    bq = q[tk].get(t0 + 60 * k)
                    if not bq:
                        continue
                    bid, ask = bq
                    p = float(table_prob(tab, k, np.array([z[i]]))[0])
                    if p - ask - fee(ask) > margin and 0.02 <= ask <= 0.98:
                        pnl.append(res - ask - fee(ask))
                    elif (1 - p) - (1 - bid) - fee(1 - bid) > margin and 0.02 <= 1 - bid <= 0.98:
                        pnl.append((1 - res) - (1 - bid) - fee(1 - bid))
                if pnl:
                    print(f"  margin {margin:.2f} minute {k:2d} {half:5} n={len(pnl):5d} "
                          f"{100 * np.mean(pnl):+6.2f}c ±{100 * np.std(pnl) / math.sqrt(len(pnl)):.2f}")

    # 3) the live fade bot's zone: favourite quoted 71-95c in the last 10 minutes
    print("  -- favourite 71-95c (live bot zone): model vs price vs actual --")
    for k in (5, 8, 10, 12, 13):
        z, d, ok = features(w, k)
        acc = defaultdict(list)
        for tk, t0, i, res in rows:
            if i is None or not ok[i]:
                continue
            bq = q[tk].get(t0 + 60 * k)
            if not bq:
                continue
            bid, ask = bq
            p = float(table_prob(tab, k, np.array([z[i]]))[0])
            mid = (bid + ask) / 2
            if mid >= 0.5:
                fav_px, fav_p, fav_win = bid, p, res
            else:
                fav_px, fav_p, fav_win = 1 - ask, 1 - p, 1 - res
            if 0.71 <= fav_px <= 0.95:
                gap = fav_p - fav_px
                b = "model>px+5" if gap > 0.05 else ("model<px-5" if gap < -0.05 else "close")
                acc[b].append((fav_px, fav_p, fav_win))
        for b, v in sorted(acc.items()):
            a = np.array(v)
            print(f"  minute {k:2d} {b:11} n={len(a):5d} bid {a[:, 0].mean():.3f} "
                  f"model {a[:, 1].mean():.3f} actual {a[:, 2].mean():.3f} "
                  f"bid-maker c/ct {100 * (a[:, 2] - a[:, 0]).mean():+.2f}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
