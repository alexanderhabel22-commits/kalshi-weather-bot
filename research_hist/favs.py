"""Snapshot test of the favourite side, as a 5-minute bot would trade it.

For each market and time-to-close window, walk a 5-minute clock grid. At the
first grid time where the last print puts one side in the price band:
  taker: buy that side at the next real print where a taker bought it, within
         120 s and still in the band (that ask existed); pay the taker fee.
  maker: rest a bid 1c under the last print for that side; filled only if within
         1h a trade prints strictly below the bid on that side (back of queue,
         maker_r); touch_r is the optimistic version (any print at the bid fills);
         maker fee where the series has one. Expires after 1h.
One entry per market per window (no stacking).

usage: python research_hist/favs.py feats.parquet out.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

BANDS = [(.65, .85), (.85, .95), (.95, .99)]
WINS = [("<1h", 0, 3600), ("1-6h", 3600, 6 * 3600), ("6-24h", 6 * 3600, 86400),
        ("1-7d", 86400, 7 * 86400),
        # whole life: the only window with no look-ahead for markets that can close
        # early (sports end, Mentions word said), since "time to close" is unknown there
        ("any", 0, 10 ** 10)]
GRID = 300


def fee(p, mult, rate):
    return np.ceil(np.round(rate * mult * 10 * p * (1 - p) * 100, 6)) / 1000


def main():
    df = pd.read_parquet(sys.argv[1], columns=["mkt", "t", "ttc", "q", "win", "oos"])
    mt = pd.read_parquet(sys.argv[1].replace(".parquet", "_markets.parquet"))
    # rebuild yes price / taker side from q and win: win = (taker side == result)
    resyes = (mt.result.to_numpy() == "yes")
    mk = df.mkt.to_numpy()
    q = df.q.to_numpy(float)
    win = df.win.to_numpy()
    tyes = (win == 1) == resyes[mk]  # taker bought yes?
    yp = np.where(tyes, q, 1 - q)
    t = df.t.to_numpy()
    starts = np.flatnonzero(np.r_[True, mk[1:] != mk[:-1]])
    ends = np.r_[starts[1:], len(mk)]
    rows = []
    for a, b in zip(starts, ends):
        m = mk[a]
        info = mt.iloc[m]
        close = int(info.close_ts)
        mult = float(info.mult)
        mfee = bool(info.maker_fee)
        ry = bool(resyes[m])
        tt, yy, sy = t[a:b], yp[a:b], tyes[a:b]
        if b - a < 3:
            continue
        for wname, lo_s, hi_s in WINS:
            g0 = max(close - hi_s, tt[0])
            g1 = close - lo_s
            if g0 >= g1:
                continue
            grid = np.arange(g0 - g0 % GRID + GRID, g1, GRID)
            if len(grid) == 0:
                continue
            idx = np.searchsorted(tt, grid, side="right") - 1
            ok = idx >= 0
            if not ok.any():
                continue
            last = np.where(ok, yy[np.maximum(idx, 0)], np.nan)
            for blo, bhi in BANDS:
                yes_in = (last >= blo) & (last < bhi)
                no_in = (1 - last >= blo) & (1 - last < bhi)
                hit = np.flatnonzero(yes_in | no_in)
                if len(hit) == 0:
                    continue
                k = hit[0]
                gt = grid[k]
                side_yes = bool(yes_in[k])
                pside = last[k] if side_yes else 1 - last[k]
                won = side_yes == ry
                # taker: next print where a taker bought this side, within 120 s
                j0 = np.searchsorted(tt, gt, side="right")
                tk = np.nan
                for j in range(j0, b - a):
                    if tt[j] > gt + 120:
                        break
                    if sy[j] == side_yes:
                        p = yy[j] if side_yes else 1 - yy[j]
                        if blo <= p < bhi:
                            tk = p
                        break
                # maker: bid 1c under, filled if a later print goes strictly below within 1h
                bid = round(pside - 0.01, 2)
                mf = np.nan
                mt_ = np.nan
                j1 = np.searchsorted(tt, gt + 3600, side="right")
                seg = yy[j0:j1]
                if len(seg):
                    sp = seg if side_yes else 1 - seg
                    if (np.round(sp, 4) < bid - 1e-9).any():
                        mf = bid
                    if (np.round(sp, 4) <= bid + 1e-9).any():  # optimistic: any touch fills
                        mt_ = bid
                rows.append((m, info['event'], info['cat'], info['grp'], info['series'], wname,
                             f"{blo:.2f}-{bhi:.2f}", int(close >= 1782864000), gt, int(won),
                             tk, (won - tk - fee(tk, mult, 0.07)) if tk == tk else np.nan,
                             mf, (won - mf - (fee(1 - mf, mult, 0.0175) if mfee else 0))
                             if mf == mf else np.nan,
                             (won - mt_ - (fee(1 - mt_, mult, 0.0175) if mfee else 0))
                             if mt_ == mt_ else np.nan))
    out = pd.DataFrame(rows, columns=["mkt", "event", "cat", "grp", "series", "win_ttc", "band",
                                      "oos", "t", "won", "taker_p", "taker_r", "maker_p",
                                      "maker_r", "touch_r"])
    out.to_csv(sys.argv[2], index=False)
    print("entries", len(out), "taker fills", out.taker_r.notna().sum(), "maker fills",
          out.maker_r.notna().sum())


if __name__ == "__main__":
    main()
