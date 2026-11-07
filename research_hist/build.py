"""Turn hist-research-data shards into one per-trade feature table (parquet).

usage: python research_hist/build.py <run dir> [<run dir> ...] <out.parquet>

Each row is one exact trade. Columns (taker view unless noted):
  q        price the taker paid for the side they bought (0-1)
  win      1 if the side the taker bought settled YES-for-them
  thr      1 if within the next hour the market printed beyond q in the taker's
           direction, i.e. a resting maker order at q (back of the queue) filled
  mom      1h price change in the taker's direction before the trade (c), NaN if none
  nq_same / nq_opp   q of the next trade within 15 min whose taker bought the
           same / the opposite side (a real later fill price), NaN if none
  fee_t / fee_m      taker / maker fee per contract at q (10-lot order rounding)
"""
from __future__ import annotations

import glob
import gzip
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

OOS_START = int(pd.Timestamp("2026-07-01", tz="UTC").timestamp())

PERIOD = re.compile(r"(1H|2H|1P|2P|3P|1Q|2Q|3Q|4Q|HALF|1ST|F5|INNING|SET\d|MAP\d)")
PROP = re.compile(r"(PTS|REB|AST|3PT|PRA|PASS|RUSH|REC|TD|YDS|HR|HIT|KS|STRIKEOUT|SOG|GOAL|"
                  r"SCORER|SHOT|LADDER|ACE|SACK|RBI|SAVE|KILLS|BLK|STL|DD|TD)")
FUT = re.compile(r"(CHAMP|MVP|WINS|PLAYOFF|SB$|WS$|CUP|AWARD|ROY|DRAFT|CONF|DIV|TITLE|"
                 r"ADVANCE|COACH|TRADE|JOIN|SEASON|LEADER|TOP)")


def sport_type(series: str) -> str:
    s = series[2:] if series.startswith("KX") else series
    if PERIOD.search(s):
        return "period"
    if "SPREAD" in s:
        return "spread"
    if "TOTAL" in s:
        return "total"
    if "BTTS" in s:
        return "btts"
    if FUT.search(s):
        return "futures"
    if re.search(r"(GAME|MATCH|FIGHT|BOUT|RACE|WIN$)", s):
        return "game"
    if PROP.search(s):
        return "prop"
    return "other"


def fee10(p, mult, rate):
    """Per-contract fee for a 10-lot order at price p (Kalshi rounds the order fee up)."""
    raw = rate * mult * 10 * p * (1 - p)
    return np.ceil(np.round(raw * 100, 6)) / 100 / 10


def sparse_table(a, op):
    tabs = [a]
    k = 1
    while (1 << k) <= len(a):
        prev = tabs[-1]
        h = 1 << (k - 1)
        nxt = op(prev[:-h], prev[h:])
        tabs.append(nxt)
        k += 1
    return tabs


def range_query(tabs, lo, hi, op, empty):
    """op over a[lo:hi] (hi exclusive) for arrays of bounds; empty where lo >= hi."""
    out = np.full(len(lo), empty, dtype=tabs[0].dtype)
    ok = hi > lo
    ln = (hi - lo)[ok]
    k = np.floor(np.log2(ln)).astype(int)
    l, h = lo[ok], hi[ok]
    res = np.empty(len(l), dtype=tabs[0].dtype)
    for kk in np.unique(k):
        m = k == kk
        t = tabs[kk]
        res[m] = op(t[l[m]], t[h[m] - (1 << kk)])
    out[ok] = res
    return out


def next_side(side_arr, mkt, t, which, start, horizon=900):
    """Index of the next row j>=start[i] (a later second) in the same market with
    side == which, within horizon seconds."""
    pos = np.flatnonzero(side_arr == which)
    j = np.searchsorted(pos, start)
    out = np.full(len(side_arr), -1)
    ok = j < len(pos)
    cand = np.where(ok, pos[np.minimum(j, len(pos) - 1)], 0)
    good = ok & (mkt[cand] == mkt) & (t[cand] - t <= horizon)
    out[good] = cand[good]
    return out


def main():
    runs, out = sys.argv[1:-1], sys.argv[-1]
    ser = pd.concat([pd.read_csv(Path(r) / "series.csv") for r in runs]).drop_duplicates("series")
    smp = pd.concat([pd.read_csv(Path(r) / "sample.csv.gz") for r in runs])
    smp = smp[smp.fetched == 1].drop_duplicates("ticker")
    files = [f for r in runs for f in sorted(glob.glob(str(Path(r) / "trades*.csv.gz")))]
    tr = pd.concat([pd.read_csv(gzip.open(f), dtype={"taker_side": "category"}) for f in files])
    tr = tr[tr.taker_side.isin(["yes", "no"]) & tr.ticker.isin(smp.ticker)]
    print("series", len(ser), "markets", len(smp), "trades", len(tr), flush=True)

    ser = ser.set_index("series")
    smp = smp.set_index("ticker")
    cat = ser["category"].fillna("?")
    tag0 = ser["tags"].fillna("").astype(str).str.split("|").str[0]
    mult = pd.to_numeric(ser["fee_multiplier"], errors="coerce").fillna(1.0)
    maker = ser["fee_type"].fillna("").astype(str).str.contains("maker")
    freq = ser["frequency"].fillna("?")

    meta = pd.DataFrame(index=smp.index)
    meta["series"] = smp["series"]
    meta["event"] = smp["event"]
    meta["close_ts"] = smp["close_ts"]
    meta["result"] = smp["result"]
    meta["mvol"] = smp["volume"]
    meta["trunc"] = smp["truncated"]
    meta["cat"] = meta.series.map(cat).fillna("?")
    meta["sport"] = meta.series.map(tag0).fillna("")
    meta["mult"] = meta.series.map(mult).fillna(1.0)
    meta["maker_fee"] = meta.series.map(maker).fillna(False)
    st = meta.series.map(sport_type)
    meta["grp"] = np.where(meta.cat == "Sports", "Sports|" + meta.sport + "|" + st,
                           meta.cat + "|" + meta.series.map(freq).fillna("?"))
    meta["stype"] = np.where(meta.cat == "Sports", st, "")

    tr = tr.sort_values(["ticker", "t"], kind="stable").reset_index(drop=True)
    codes, uniq = pd.factorize(tr.ticker)
    mk = codes.astype(np.int64)
    t = tr.t.to_numpy(np.int64)
    yp = tr.yes_price.to_numpy(float)
    yes = (tr.taker_side == "yes").to_numpy()
    m = meta.loc[uniq]
    close = m.close_ts.to_numpy(np.int64)[mk]
    resyes = (m.result.to_numpy() == "yes")[mk]

    q = np.where(yes, yp, 1 - yp)
    win = (yes == resyes).astype(np.int8)
    key = mk * 10 ** 11 + t
    # forward 1h extremes of the yes price (strictly after this trade)
    cents = np.round(yp * 100).astype(np.int16)
    hi_idx = np.searchsorted(key, key + 3600, side="right")
    lo_idx = np.searchsorted(key, key, side="right")  # strictly later seconds
    tmax = sparse_table(cents, np.maximum)
    fmax = range_query(tmax, lo_idx, hi_idx, np.maximum, -1)
    del tmax
    tmin = sparse_table(cents, np.minimum)
    fmin = range_query(tmin, lo_idx, hi_idx, np.minimum, 999)
    del tmin
    thr = np.where(yes, fmax > cents, fmin < cents).astype(np.int8)
    # 1h momentum in the taker's direction
    j = np.searchsorted(key, key - 3600, side="right") - 1
    okm = (j >= 0) & (mk[np.maximum(j, 0)] == mk)
    dy = np.where(okm, yp - yp[np.maximum(j, 0)], np.nan) * 100
    mom = np.where(yes, dy, -dy)
    # next same / opposite side taker trade within 15 min
    side = yes.astype(np.int8)
    ny = next_side(side, mk, t, 1, lo_idx)
    nn = next_side(side, mk, t, 0, lo_idx)
    nsame = np.where(yes, ny, nn)
    nopp = np.where(yes, nn, ny)
    nq_same = np.where(nsame >= 0, q[np.maximum(nsame, 0)], np.nan)
    nq_opp = np.where(nopp >= 0, q[np.maximum(nopp, 0)], np.nan)

    mlt = m["mult"].to_numpy(float)[mk]
    mfee = m["maker_fee"].to_numpy(bool)[mk]
    et = pd.to_datetime(t, unit="s", utc=True).tz_convert("America/New_York")
    df = pd.DataFrame({
        "mkt": mk.astype(np.int32), "t": t, "ttc": close - t, "q": q.astype(np.float32),
        "win": win, "count": tr["count"].to_numpy(np.float32), "thr": thr,
        "mom": mom.astype(np.float32), "nq_same": nq_same.astype(np.float32),
        "nq_opp": nq_opp.astype(np.float32),
        "fee_t": fee10(q, mlt, 0.07).astype(np.float32),
        "fee_m": np.where(mfee, fee10(1 - q, mlt, 0.0175), 0).astype(np.float32),
        "hour": et.hour.to_numpy(np.int8), "dow": et.dayofweek.to_numpy(np.int8),
        "oos": (close >= OOS_START).astype(np.int8),
    })
    # fees for the follow/fade entries at their own prices
    df["fee_ns"] = np.where(np.isnan(nq_same), np.nan, fee10(np.nan_to_num(nq_same), mlt, 0.07))
    df["fee_no"] = np.where(np.isnan(nq_opp), np.nan, fee10(np.nan_to_num(nq_opp), mlt, 0.07))
    mt = m.reset_index().rename(columns={"index": "ticker"})
    mt.to_parquet(out.replace(".parquet", "_markets.parquet"))
    df.to_parquet(out)
    print("wrote", out, len(df), "rows;", int(okm.sum()), "with momentum;",
          f"thr share {thr.mean():.2f}", flush=True)
    assert not math.isnan(float(df.q.mean()))


if __name__ == "__main__":
    main()
