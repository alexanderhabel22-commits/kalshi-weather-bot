"""Score fade-longshots (resting NO filled when a taker buys YES under 30c) on
the full-history pulls from research_fin/fetch.py.

usage: python research_fin/analyze.py RUN_DIR [RUN_DIR ...] [--out report.md]

Fill models
  all      every YES-taker trade at our price fills us (front of queue, optimistic)
  through  only trades after which the level was used up within 1h (back of
           queue, pessimistic: this is where adverse selection bites)
Maker fees are charged where the series has fee_type quadratic_with_maker_fees
(0.0175 * mult * P * (1-P) per contract, plus 0.1c rounding allowance).
All contract counts are scaled by the market's sampling weight.
"""
from __future__ import annotations

import csv
import gzip
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
OUTF = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
if OUTF in ARGS:
    ARGS.remove(OUTF)
BANDS = [(0.0, .05, "01-04c"), (.05, .10, "05-09c"), (.10, .20, "10-19c"), (.20, .30, "20-29c")]
TTC_MID_DAYS = {"a<15m": .005, "b15-60m": .025, "c1-4h": .1, "d4-24h": .5, "e1-7d": 3.5,
                "f7-30d": 15, "g>30d": 60}


def subcat(series: str, title: str, freq: str, cat: str) -> str:
    s, t = series.upper(), (title or "").lower()
    if cat == "Crypto":
        if "15M" in s:
            return "Crypto 15-min up/down"
        if s.endswith("D") or "above" in t or "daily" in t and "range" not in t:
            return "Crypto hourly/daily above-below"
        if re.search(r"KX(BTC|ETH|SOL|XRP|DOGE|BNB|HYPE|ZEC|LTC|ADA)$", s) or "range" in t \
                or "price" in t:
            return "Crypto price ranges"
        if "MAX" in s or "MIN" in s or "Y" == s[-1:]:
            return "Crypto max/min/yearly"
        return "Crypto other"
    if cat == "Financials":
        if re.match(r"KX(INX|NASDAQ100|DJI)", s):
            if s.endswith("U") or "above" in t or "hourly" in t:
                return "Index hourly above-below"
            if s.endswith("Y") or "MAXY" in s or "yearly" in t:
                return "Index yearly/max"
            return "Index daily range"
        if re.match(r"KX(EUR|GBP|USD|AUD|JPY|CAD|CHF)", s):
            return "FX 15-min" if "15M" in s else "FX hourly/daily"
        if "TNOTE" in s or "UST" in s or "treasury" in t:
            return "Treasury yields"
        return "Corporate/IPO one-offs"
    if cat == "Economics":
        if re.match(r"KX(FED|RATECUT|FEDHIKE|CBDECISION)", s) or "fed " in t:
            return "Central banks"
        if "CPI" in s or "PCE" in s or "inflation" in t or "TRUF" in s:
            return "Inflation"
        if re.match(r"KX(PAYROLL|U3|JOBLESS|ADP|JOLTS)", s) or "jobs" in t or "unemploy" in t:
            return "Jobs"
        if "GDP" in s or "RECSS" in s:
            return "GDP/recession"
        if "GAS" in s or "DIESEL" in s:
            return "Gas/diesel prices" + (" daily" if freq == "daily" or s.endswith("D") or
                                          re.search(r"GASD[A-Z]{2}$", s) else " weekly/monthly")
        return "Econ other"
    return cat


def band_of(p):
    return next((lab for lo, hi, lab in BANDS if lo <= p < hi), None)


def load(run_dirs):
    series, markets, fills = {}, {}, []
    for d in map(Path, run_dirs):
        for s in csv.DictReader(open(d / "series.csv")):
            series[s["series"]] = s
        for m in csv.DictReader(gzip.open(d / "sample.csv.gz", "rt")):
            if m["fetched"] == "1":
                markets[m["ticker"]] = m
        for t in csv.DictReader(gzip.open(d / "trades_agg.csv.gz", "rt")):
            if t["taker_side"] != "yes":
                continue
            p = float(t["yes_price"])
            b = band_of(p)
            m = markets.get(t["ticker"])
            if not b or not m or p < .01:
                continue
            s = series.get(m["series"], {})
            fee = 0.0
            if "maker" in (s.get("fee_type") or ""):
                fee = 0.0175 * float(s.get("fee_multiplier") or 1) * p * (1 - p) + 0.001
            w = float(m["weight"])
            c = float(t["count"]) * w
            pnl_ctr = (1.0 if m["result"] == "no" else 0.0) - (1 - p) - fee
            fills.append({
                "sub": subcat(m["series"], s.get("title", ""), s.get("frequency", ""),
                              s.get("category", "")),
                "cat": s.get("category", ""), "series": m["series"], "event": m["event"],
                "month": m["close"][:7], "band": b, "ttc": t["ttc"], "size": t["size"],
                "through": t["through"] == "1", "trunc": m["truncated"] == "1",
                "c": c, "pnl": c * pnl_ctr, "cost": c * (1 - p), "p": p,
            })
    return series, markets, fills


def summarize(rows, rnd, boots=300):
    ev = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        e = ev[r["event"]]
        e[0] += r["c"]
        e[1] += r["pnl"]
    evs = list(ev.values())
    n = sum(v[0] for v in evs)
    if not evs or n <= 0:
        return None
    b = []
    for _ in range(boots):
        x = [evs[rnd.randrange(len(evs))] for _ in evs]
        xn = sum(v[0] for v in x)
        b.append(sum(v[1] for v in x) / xn * 100 if xn else 0)
    b.sort()
    return {"events": len(evs), "pos": 100 * sum(v[1] > 0 for v in evs) / len(evs), "c": n,
            "cpc": sum(v[1] for v in evs) / n * 100, "lo": b[int(.05 * boots)],
            "hi": b[int(.95 * boots) - 1]}


def table(title, groups, rnd, min_events=8):
    lines = [f"\n### {title}\n", "| slice | events | % events up | contracts | c/contract | 90% CI |",
             "|---|---:|---:|---:|---:|---|"]
    for k in sorted(groups):
        s = summarize(groups[k], rnd)
        if not s or s["events"] < min_events:
            continue
        lines.append(f"| {k} | {s['events']} | {s['pos']:.0f} | {s['c']:,.0f} | {s['cpc']:+.2f} | "
                     f"{s['lo']:+.1f} to {s['hi']:+.1f} |")
    return lines


def walk_forward(rows, key_fn, min_prior_events=15, min_prior_cpc=1.0, warmup=3):
    """Month by month: trade a cell only if its record in all earlier months was good."""
    months = sorted({r["month"] for r in rows})
    by = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, set()]))
    for r in rows:
        a = by[r["month"]][key_fn(r)]
        a[0] += r["c"]
        a[1] += r["pnl"]
        a[2].add(r["event"])
    prior = defaultdict(lambda: [0.0, 0.0, 0])
    out = []
    for i, mo in enumerate(months):
        if i >= warmup:
            chosen = [k for k, v in prior.items() if v[2] >= min_prior_events and v[0] > 0
                      and v[1] / v[0] * 100 >= min_prior_cpc]
            c = sum(by[mo][k][0] for k in chosen if k in by[mo])
            p = sum(by[mo][k][1] for k in chosen if k in by[mo])
            out.append((mo, len(chosen), c, p))
        for k, v in by[mo].items():
            prior[k][0] += v[0]
            prior[k][1] += v[1]
            prior[k][2] += len(v[2])
    return out


def main():
    series, markets, fills = load(ARGS)
    rnd = random.Random(5)
    L = [f"# Fade longshots, full history ({len(markets):,} sampled markets, "
         f"{len(fills):,} fill cells)"]
    for model, sel in (("all fills (front of queue)", lambda r: True),
                       ("back-of-queue fills only (level traded through)", lambda r: r["through"])):
        rows = [r for r in fills if sel(r)]
        L.append(f"\n## Fill model: {model}")
        g = defaultdict(list)
        for r in rows:
            g[f"{r['cat']} / ALL"].append(r)
            g[f"{r['cat']} / {r['sub']}"].append(r)
        L += table("By subcategory (YES under 30c)", g, rnd)
        g = defaultdict(list)
        for r in rows:
            g[f"{r['sub']} / {r['band']}"].append(r)
        L += table("By subcategory and price band", g, rnd)
        g = defaultdict(list)
        for r in rows:
            g[f"{r['cat']} / {r['ttc']}"].append(r)
            g[f"{r['sub']} / {r['ttc']}"].append(r)
        L += table("By time to close", g, rnd)
        g = defaultdict(list)
        for r in rows:
            g[f"{r['cat']} / size {r['size']}"].append(r)
        L += table("By taker trade size (adverse selection check)", g, rnd)
        g = defaultdict(list)
        for r in rows:
            g[f"{r['sub']} / {r['month']}"].append(r)
        L += table("By subcategory and month", g, rnd, min_events=5)
        g = defaultdict(list)
        for r in rows:
            if not r["trunc"]:
                g[f"{r['sub']} (untruncated markets)"].append(r)
        L += table("Robustness: markets whose trade history was complete", g, rnd)

        L.append("\n### Walk-forward (cell = subcategory x band x time-to-close; trade a cell "
                 "in month M only if months before M had >=15 events and >= +1c/contract)\n")
        L.append("| month | cells traded | contracts | c/contract |")
        L.append("|---|---:|---:|---:|")
        tot_c = tot_p = 0.0
        for mo, n, c, p in walk_forward(rows, lambda r: (r["sub"], r["band"], r["ttc"])):
            tot_c += c
            tot_p += p
            L.append(f"| {mo} | {n} | {c:,.0f} | {p / c * 100 if c else 0:+.2f} |")
        L.append(f"| **total** | | {tot_c:,.0f} | {tot_p / tot_c * 100 if tot_c else 0:+.2f} |")
        per_sub = defaultdict(list)
        for r in rows:
            per_sub[r["sub"]].append(r)
        L.append("\nWalk-forward per subcategory (cells = band x time-to-close inside it):\n")
        L.append("| subcategory | months traded | contracts | c/contract | months up |")
        L.append("|---|---:|---:|---:|---:|")
        for sub, rr in sorted(per_sub.items()):
            wf = [x for x in walk_forward(rr, lambda r: (r["band"], r["ttc"])) if x[2] > 0]
            c = sum(x[2] for x in wf)
            p = sum(x[3] for x in wf)
            if c:
                L.append(f"| {sub} | {len(wf)} | {c:,.0f} | {p / c * 100:+.2f} | "
                         f"{sum(x[3] > 0 for x in wf)} |")

        # capacity: last 6 months, per subcategory
        months = sorted({r["month"] for r in rows})[-6:]
        L.append("\n### Capacity, last 6 months (taker YES flow under 30c, scaled to all markets)\n")
        L.append("| subcategory | contracts/month | $ at risk/month | avg days held | "
                 "bankroll if we take 5% of flow |")
        L.append("|---|---:|---:|---:|---:|")
        cap = defaultdict(lambda: [0.0, 0.0, 0.0])
        for r in rows:
            if r["month"] in months:
                a = cap[r["sub"]]
                a[0] += r["c"]
                a[1] += r["cost"]
                a[2] += r["cost"] * TTC_MID_DAYS[r["ttc"]]
        for sub, (c, cost, cd) in sorted(cap.items()):
            days = cd / cost if cost else 0
            per_m = cost / len(months)
            bank = 0.05 * per_m * max(days, 1) / 30
            L.append(f"| {sub} | {c / len(months):,.0f} | ${per_m:,.0f} | {days:.1f} | "
                     f"${bank:,.0f} |")
    txt = "\n".join(L) + "\n"
    if OUTF:
        Path(OUTF).write_text(txt)
    print(txt)


if __name__ == "__main__":
    main()
