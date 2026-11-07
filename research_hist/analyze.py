"""Edge mine over the per-trade table from build.py. Prints markdown tables.

usage: python research_hist/analyze.py feats.parquet [volume run dirs ...] > RESULTS_raw.md

Returns are cents per contract, after fees, at exact traded prices.
  taker = buy the side the taker bought, at their price (a real ask at that second)
  maker = own the other side at 1-q, counted only when the level was traded
          through within 1h (back-of-queue fill), maker fee where the series has one
CIs: +-1.96 event-clustered SE. IS = markets closing before 2026-07-01, OOS = after.
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

QB = [0, .05, .10, .20, .35, .50, .65, .80, .90, .95, 1.001]
QB5 = [0, .15, .35, .65, .85, 1.001]
TB = [-1, 3600, 6 * 3600, 86400, 7 * 86400, 10 ** 12]
TBL = ["<1h", "1-6h", "6-24h", "1-7d", ">7d"]


def stat(d, r):
    """count-weighted mean of r (dollars) clustered by event -> (c, se c, contracts, events)."""
    if len(d) == 0:
        return np.nan, np.nan, 0.0, 0
    w = d["count"].to_numpy(float)
    g = pd.DataFrame({"e": d["event"].to_numpy(), "s": r * w, "n": w}).groupby("e").sum()
    N = g.n.sum()
    if N <= 0:
        return np.nan, np.nan, 0.0, 0
    mu = g.s.sum() / N
    se = np.sqrt(((g.s - mu * g.n) ** 2).sum()) / N
    return mu * 100, se * 100, N, len(g)


def fmt(s):
    mu, se, n, e = s
    if not e:
        return "—"
    return f"{mu:+.1f} ±{1.96 * se:.1f} ({n / 1000:.0f}k, {e}ev)"


def ret_taker(d):
    return d.win.to_numpy(float) - d.q.to_numpy(float) - d.fee_t.to_numpy(float)


def ret_maker(d):
    return d.q.to_numpy(float) - d.win.to_numpy(float) - d.fee_m.to_numpy(float)


def table(df, by, title, roles=("taker", "maker")):
    print(f"\n### {title}\n")
    print("| " + " | ".join(by) + " | " + " | ".join(f"{r} IS | {r} OOS" for r in roles) + " |")
    print("|" + "---|" * (len(by) + 2 * len(roles)))
    for key, d in df.groupby(by, observed=True):
        key = key if isinstance(key, tuple) else (key,)
        cells = []
        for r in roles:
            dd = d[d.thr == 1] if r == "maker" else d
            f = ret_maker if r == "maker" else ret_taker
            for o in (0, 1):
                x = dd[dd.oos == o]
                cells.append(fmt(stat(x, f(x))))
        print("| " + " | ".join(str(k) for k in key) + " | " + " | ".join(cells) + " |")


def main():
    df = pd.read_parquet(sys.argv[1])
    mt = pd.read_parquet(sys.argv[1].replace(".parquet", "_markets.parquet"))
    for c in ("event", "cat", "grp", "series", "stype", "trunc", "mvol"):
        df[c] = mt[c].to_numpy()[df.mkt.to_numpy()]
    df["qb"] = pd.cut(df.q, QB, right=False)
    df["qb5"] = pd.cut(df.q, QB5, right=False)
    df["tb"] = pd.cut(df.ttc, TB, labels=TBL)
    print(f"# Edge mine raw results\n\ntrades {len(df):,}, markets {len(mt):,}, events "
          f"{mt.event.nunique():,}; truncated tapes {mt.trunc.mean():.1%}; "
          f"contracts {df['count'].sum() / 1e6:.0f}M; OOS share {df.oos.mean():.0%}")

    big = df.groupby("cat")["count"].sum().sort_values(ascending=False)
    cats = list(big[big > 2e5].index)
    dc = df[df.cat.isin(cats)]
    table(dc, ["cat"], "1. All trades by category")
    table(dc, ["cat", "qb"], "2. By category x price paid by the taker")
    sp = df[df.cat == "Sports"]
    table(sp, ["stype"], "3a. Sports by market type")
    table(sp, ["stype", "tb"], "3b. Sports type x time to close")
    table(sp, ["stype", "qb5"], "3c. Sports type x price")
    top_sports = sp.groupby("grp")["count"].sum().sort_values(ascending=False).head(25).index
    table(sp[sp.grp.isin(top_sports)], ["grp"], "3d. Top 25 sports groups by volume")
    table(dc, ["cat", "tb"], "4. Category x time to close")

    # 5. hour of day / weekend
    dc = dc.assign(hb=pd.cut(dc.hour, [-1, 5, 11, 17, 23], labels=["0-6", "6-12", "12-18",
                                                                     "18-24"]),
                   wk=np.where(dc.dow >= 5, "wkend", "wkday"))
    table(dc, ["cat", "hb"], "5a. Category x hour of day (ET)")
    table(dc, ["cat", "wk"], "5b. Category x weekday/weekend")

    # 6. momentum: taker buying with (+) or against (-) the last hour's move
    dm = dc[dc.mom.notna() & (dc.q > .05) & (dc.q < .95)]
    dm = dm.assign(mb=pd.cut(dm.mom, [-101, -10, -3, -0.5, 0.5, 3, 10, 101],
                             labels=["<=-10", "-10..-3", "-3..-1", "0", "1..3", "3..10", ">=10"]))
    table(dm, ["cat", "mb"], "6. Momentum: taker side vs its own 1h move (c)", roles=("taker",))

    # 7. big taker trades: follow (next same-side trade) / fade (next opposite trade)
    print("\n### 7. After a big taker trade: follow = buy same side at the next real "
          "same-side print within 15 min; fade = buy the other side at the next opposite print\n")
    print("| cat | min size | follow IS | follow OOS | fade IS | fade OOS |\n|---|---|---|---|---|---|")
    for size in (1000, 5000):
        b = dc[(dc["count"] >= size) & (dc.q > .05) & (dc.q < .95)]
        for c, d in list(b.groupby("cat")) + [("ALL", b)]:
            row = []
            for kind in ("follow", "fade"):
                if kind == "follow":
                    x = d[d.nq_same.notna()]
                    r = x.win.to_numpy(float) - x.nq_same.to_numpy(float) - x.fee_ns.to_numpy(float)
                else:
                    x = d[d.nq_opp.notna()]
                    r = (1 - x.win.to_numpy(float)) - x.nq_opp.to_numpy(float) - x.fee_no.to_numpy(float)
                xx = x.assign(count=1.0)  # one 1-unit trade per signal
                for o in (0, 1):
                    m = (xx.oos == o).to_numpy()
                    row.append(fmt(stat(xx[m], r[m])))
            print(f"| {c} | {size} | " + " | ".join(row) + " |")

    # 8. resolution quirks: favorites 95c+ in the last 24h that lose, per series
    print("\n### 8. Series where 95c+ favourites (last 24h) lose more than priced (taker)\n")
    fav = df[(df.q >= .95) & (df.ttc < 86400)]
    rows = []
    for s, d in fav.groupby("series"):
        mu, se, n, e = stat(d, ret_taker(d))
        if e >= 15:
            rows.append((mu, se, n, e, s, d.cat.iloc[0], d.oos.mean()))
    rows.sort()
    print("| series | cat | c/contract | ±1.96se | contracts | events | OOS share |\n|---|---|---|---|---|---|---|")
    for mu, se, n, e, s, c, o in rows[:20]:
        print(f"| {s} | {c} | {mu:+.1f} | {1.96 * se:.1f} | {n:,.0f} | {e} | {o:.0%} |")

    # 9. grid search with walk-forward check
    print("\n### 9. Grid search (group x price x time-to-close x role): cells with IS t>=3 and "
          ">=1c, then their OOS\n")
    res = []
    for key, d in df.groupby(["grp", "qb5", "tb"], observed=True):
        for role in ("taker", "maker"):
            dd = d[d.thr == 1] if role == "maker" else d
            f = ret_maker if role == "maker" else ret_taker
            a, b = dd[dd.oos == 0], dd[dd.oos == 1]
            si = stat(a, f(a))
            if si[3] < 40 or si[2] < 20000:
                continue
            so = stat(b, f(b))
            res.append((*key, role, *si, *so))
    g = pd.DataFrame(res, columns=["grp", "qb", "tb", "role", "is_c", "is_se", "is_n", "is_e",
                                   "oos_c", "oos_se", "oos_n", "oos_e"])
    g["is_t"] = g.is_c / g.is_se
    sel = g[(g.is_t >= 3) & (g.is_c >= 1)].sort_values("is_t", ascending=False)
    print(f"cells tested {len(g)}; passing IS {len(sel)}; expected false positives at t>=3 "
          f"~{len(g) * 0.00135:.1f}\n")
    print("| group | price | ttc | role | IS c (±1.96se, ev) | OOS c (±1.96se, ev) | OOS contracts |"
          "\n|---|---|---|---|---|---|---|")
    for _, r in sel.head(60).iterrows():
        oo = (f"{r.oos_c:+.1f} ±{1.96 * r.oos_se:.1f} ({r.oos_e}ev)" if r.oos_e else "—")
        print(f"| {r.grp} | {r.qb} | {r.tb} | {r.role} | {r.is_c:+.1f} ±{1.96 * r.is_se:.1f} "
              f"({r.is_e}ev) | {oo} | {r.oos_n:,.0f} |")
    g.to_csv(Path(sys.argv[1]).with_name("grid.csv"), index=False)

    # 10. capacity: monthly volume per group, Jul-Oct 2026, from the full market index
    if len(sys.argv) > 2:
        vol = pd.concat([pd.read_csv(f) for r in sys.argv[2:]
                         for f in glob.glob(str(Path(r) / "volume.csv.gz"))])
        v = vol[vol.month.between("2026-07", "2026-10")].groupby("series").volume.sum() / 4
        sg = mt.drop_duplicates("series").set_index("series").grp
        cap = v.groupby(v.index.map(sg)).sum().sort_values(ascending=False)
        print("\n### 10. Contracts traded per month by group (Jul-Oct 2026 avg, all markets)\n")
        print("| group | contracts/month |\n|---|---|")
        for k, x in cap.head(40).items():
            print(f"| {k} | {x:,.0f} |")


if __name__ == "__main__":
    main()
