"""Walk-forward study of the Mentions NO maker strategy (research only).

usage: python research_mn/analyze.py RUN_DIR [OUT.md]
RUN_DIR holds markets.csv.gz and trades_*.csv.gz from research_mn/pull.py.

A "fill" is a trade where the taker bought YES at yes price y, so a resting
NO bid at 1 - y was filled (the maker side we would be on). P&L per contract
= (1 if the market settled NO) - price paid. No maker fee on Mentions.
Edges are contract-weighted (what an average resting order at that price
earned); confidence intervals resample whole events (speeches), because the
words of one speech are correlated.
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from load import stype  # noqa: E402

# bucket i covers minutes-before-event-end in [LO[i], HI[i])
EDGES = [0, 5, 15, 30, 60, 120, 180, 360, 720, 1440, 2880, 4320, 10080, 10 ** 9]
BLABEL = ["<5m", "5-15m", "15-30m", "30-60m", "1-2h", "2-3h", "3-6h", "6-12h", "12-24h",
          "24-48h", "48-72h", "3-7d", ">7d"]
RNG = np.random.default_rng(7)


def load(run: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    m = pd.read_csv(run / "markets.csv.gz")
    m["month"] = m.close.str[:7]
    m["stype"] = m.series.map(stype)
    m["nwords"] = m.groupby("event").ticker.transform("size")
    t = pd.concat([pd.read_csv(p) for p in sorted(glob.glob(str(run / "trades_*.csv.gz")))])
    # volume traded before each bucket (ex-ante: buckets are counted down to the end)
    vb = t.groupby(["ticker", "bucket"])["count"].sum().unstack(fill_value=0)
    vb = vb[sorted(vb.columns, reverse=True)]
    before = vb.cumsum(axis=1) - vb
    before = before.stack().rename("vol_before").reset_index()
    f = t[t.taker_side == "yes"].copy()
    f["paid"] = 1 - f.yes_cents / 100.0
    f = f.merge(before, on=["ticker", "bucket"], how="left")
    f = f.merge(m[["ticker", "event", "series", "stype", "month", "result", "nwords", "yes_sub"]],
                on="ticker")
    f["won"] = (f.result == "no").astype(float)
    f["pnl"] = f["count"] * (f.won - f.paid)
    f["cap"] = f["count"] * f.paid
    return m, f


def edge_ci(d: pd.DataFrame, n=300):
    e = d.groupby("event").agg(c=("count", "sum"), p=("pnl", "sum"))
    if len(e) < 5 or e.c.sum() == 0:
        return np.nan, np.nan, np.nan, len(e)
    c, p = e.c.to_numpy(), e.p.to_numpy()
    bs = []
    for _ in range(n):
        i = RNG.integers(0, len(e), len(e))
        bs.append(p[i].sum() / c[i].sum())
    lo, hi = np.percentile(bs, [5, 95])
    return 100 * p.sum() / c.sum(), 100 * lo, 100 * hi, len(e)


def table(f: pd.DataFrame, rows: str, cols: str) -> pd.DataFrame:
    g = f.groupby([rows, cols], observed=True)
    return (100 * g.pnl.sum() / g["count"].sum()).unstack(cols).round(1)


def walk_forward(f: pd.DataFrame, rule, start="2025-10"):
    months = sorted(f.month.unique())
    out = []
    for mo in [x for x in months if x >= start]:
        te = f[f.month == mo]
        s = te[rule(f[f.month < mo], te)]
        out.append((mo, s["count"].sum(), s.pnl.sum(), s.cap.sum(), s.event.nunique()))
    return pd.DataFrame(out, columns=["month", "ctr", "pnl", "cap", "events"])


def wf_line(name, o: pd.DataFrame) -> str:
    r6 = o.iloc[-6:]
    return (f"| {name} | {o.ctr.sum() / 1e6:.1f}M | {100 * o.pnl.sum() / o.ctr.sum():.2f} | "
            f"{100 * o.pnl.sum() / o.cap.sum():.1f}% | {(o.pnl > 0).sum()}/{len(o)} | "
            f"{100 * r6.pnl.sum() / r6.ctr.sum():.2f} | {100 * r6.pnl.sum() / r6.cap.sum():.1f}% |")


def main():
    run = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else run / "analysis.md"
    m, f = load(run)
    f = f[(f.paid >= 0.30) & (f.paid < 0.99)].copy()
    f["band"] = pd.cut(f.paid, [.3, .5, .6, .7, .75, .8, .85, .9, .95, .99], right=False).astype(str)
    f["when"] = f.bucket.map(dict(enumerate(BLABEL)))
    f["half"] = np.where(f.month < "2026-04", "A: to Mar 2026", "B: Apr 2026 on")
    L = [f"# Mentions NO maker study\n\nmarkets {len(m)}, events {m.event.nunique()}, "
         f"months {f.month.min()} to {f.month.max()}, maker-NO contracts (30-99c) "
         f"{f['count'].sum() / 1e6:.1f}M\n"]

    L.append("## Edge (c/contract) by time before event end x price band\n")
    tb = table(f, "when", "band").reindex(BLABEL)
    L.append(tb.to_markdown())
    L.append("\n### Same, split in two halves (70-95c only)\n")
    L.append(table(f[(f.paid >= .7) & (f.paid < .95)], "when", "half").reindex(BLABEL).to_markdown())
    L.append("\n### Share of maker-NO contracts by time (70-95c)\n")
    x = f[(f.paid >= .7) & (f.paid < .95)]
    L.append((100 * x.groupby("when")["count"].sum() / x["count"].sum()).reindex(BLABEL).round(1)
             .to_markdown())

    L.append("\n## Edge with event-clustered 90% CI\n\n| slice | events | edge | lo | hi |\n|---|---|---|---|---|")
    for name, d in [("all 70-95", x), ("70-95, fills <30m before end", x[x.bucket <= 2]),
                    ("70-95, fills 30m-2h", x[(x.bucket >= 3) & (x.bucket <= 4)]),
                    ("70-95, fills 2-24h", x[(x.bucket >= 5) & (x.bucket <= 8)]),
                    ("70-95, fills >24h", x[x.bucket >= 9])]:
        e, lo, hi, n = edge_ci(d)
        L.append(f"| {name} | {n} | {e:.2f} | {lo:.2f} | {hi:.2f} |")

    L.append("\n## Ex-ante features (70-95c fills)\n")
    x = x.copy()
    x["vol_before_b"] = pd.cut(x.vol_before.fillna(0), [-1, 100, 500, 2000, 10000, 50000, 1e12]).astype(str)
    x["nwords_b"] = pd.cut(x.nwords, [0, 10, 20, 30, 50, 1000]).astype(str)
    sub = x.yes_sub.fillna("").astype(str)
    x["phrase"] = np.where(sub.str.contains(r"\s"), "multi-word", "one word")
    x["slash"] = np.where(sub.str.contains(r"/|\bor\b"), "has options", "single")
    for col in ["stype", "vol_before_b", "nwords_b", "phrase", "slash"]:
        L.append(f"\n### {col}\n")
        L.append(table(x, col, "half").to_markdown())

    L.append("\n## Walk-forward (test months Oct 2025 on; rules use only earlier data)\n")
    L.append("| rule | contracts | c/ctr | return per $ | months up | last 6 c/ctr | last 6 ret/$ |\n"
             "|---|---|---|---|---|---|---|")

    def rng(lo, hi, bmin=0, bmax=99, vmin=0):
        return lambda tr, te: ((te.paid >= lo) & (te.paid < hi) & (te.bucket >= bmin) &
                               (te.bucket <= bmax) & (te.vol_before.fillna(0) >= vmin))
    rules = [("current: 75-90c, all times", rng(.75, .90)),
             ("75-90c, no fills in last 30m", rng(.75, .90, 3)),
             ("75-90c, no fills in last 1h", rng(.75, .90, 4)),
             ("75-90c, no fills in last 2h", rng(.75, .90, 5)),
             ("75-90c, fills within 72h", rng(.75, .90, 0, 10)),
             ("75-90c, fills within 24h", rng(.75, .90, 0, 8)),
             ("75-90c, vol before >= 100", rng(.75, .90, 0, 99, 100)),
             ("75-90c, vol before >= 1000", rng(.75, .90, 0, 99, 1000)),
             ("70-95c", rng(.70, .95)),
             ("60-95c", rng(.60, .95)),
             ("50-90c", rng(.50, .90)),
             ("60-90c", rng(.60, .90)),
             ("60-90c, no fills in last 30m", rng(.60, .90, 3)),
             ("60-95c, no fills in last 30m", rng(.60, .95, 3))]

    def learned(tr, te, lo=.5, hi=.95, thr=0.02, minev=30):
        """Cells (band x time bucket) whose trailing edge was > thr."""
        tr = tr[(tr.paid >= lo) & (tr.paid < hi)]
        g = tr.groupby(["band", "bucket"])
        st = pd.DataFrame({"e": g.pnl.sum() / g["count"].sum(), "n": g.event.nunique()})
        good = set(st[(st.n >= minev) & (st.e > thr)].index)
        key = list(zip(te.band, te.bucket))
        return pd.Series([k in good for k in key], index=te.index) & (te.paid >= lo) & (te.paid < hi)
    rules.append(("learned band x time cells (50-95c, >2c)", learned))
    res = {}
    for name, rule in rules:
        o = walk_forward(f, rule)
        res[name] = o
        L.append(wf_line(name, o))

    L.append("\n## Capacity: maker-NO volume per day, last 3 months (75-90c, not last 30m)\n")
    last3 = sorted(f.month.unique())[-4:-1]
    y = f[f.month.isin(last3) & (f.paid >= .75) & (f.paid < .90) & (f.bucket >= 3)]
    days = 30 * len(last3)
    per_ev = y.groupby("event")["count"].sum()
    L.append(f"events {y.event.nunique()} (~{y.event.nunique() / days:.1f}/day), contracts/day "
             f"{y['count'].sum() / days:,.0f}, $ filled/day {y.cap.sum() / days:,.0f}, "
             f"median contracts per event {per_ev.median():,.0f}, p25 {per_ev.quantile(.25):,.0f}\n")
    per_mk = y.groupby("ticker")["count"].sum()
    L.append(f"markets {len(per_mk)}, median maker-NO contracts per market {per_mk.median():,.0f}, "
             f"p25 {per_mk.quantile(.25):,.0f}, p10 {per_mk.quantile(.1):,.0f}\n")
    out.write_text("\n".join(L) + "\n")
    print(out.read_text())


if __name__ == "__main__":
    main()
