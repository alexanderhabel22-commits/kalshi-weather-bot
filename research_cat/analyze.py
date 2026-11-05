"""Score longshot fading and favorite buying on the pulls from research_cat/fetch.py
(also works on research_fin runs).

usage: python research_cat/analyze.py RUN_DIR [RUN_DIR ...] [--out report.md] [--csv cells.csv]

Strategies (every one scored per filled contract, after fees):
  fade      resting NO bid, filled when a taker buys YES under 30c (the Mentions edge)
  fade_any  resting bid on the expensive side, filled when a taker buys EITHER side
            under 30c (we hold the 70-99c side)
  fav_mkr   resting bid on a favorite at 85-97c (either side) - a slice of fade_any
  fav_tkr   buying a favorite at 85-97c by taking the ask (taker fee charged)
Fill models
  all       every taker trade at our price fills us (front of queue, optimistic)
  through   only trades after which the level was used up within 1h (back of
            queue: pessimistic, this is where adverse selection bites)
Fees: taker 0.07*mult*P*(1-P) + 0.3c rounding allowance. Maker fee only where
fee_type is quadratic_with_maker_fees: 0.0175*mult*P*(1-P) + 0.1c.
Contract counts are scaled by each market's sampling weight.
"""
from __future__ import annotations

import csv
import gzip
import random
import sys
from collections import defaultdict
from pathlib import Path

ARGV = sys.argv[1:]


def _opt(name):
    if name in ARGV:
        i = ARGV.index(name)
        v = ARGV[i + 1]
        del ARGV[i:i + 2]
        return v
    return None


OUTF, CSVF = _opt("--out"), _opt("--csv")
ARGS = ARGV
TTC_MID_DAYS = {"a<15m": .005, "b15-60m": .025, "c1-4h": .1, "d4-24h": .5, "e1-7d": 3.5,
                "f7-30d": 15, "g>30d": 60}
STRATS = {
    "fade": "Fade longshots: resting NO, filled vs YES buyers under 30c",
    "fade_any": "Fade longshots, either side: resting bid on the 70-99c side",
    "fav_mkr": "Buy favorites 85-97c with resting bids (maker)",
    "fav_tkr": "Buy favorites 85-97c at the ask (taker)",
}


def band(p, edges):
    for lo, hi in edges:
        if lo <= p < hi + 1e-9:
            return f"{round(lo * 100):02d}-{round(hi * 100):02d}c"
    return None


LONG_BANDS = [(.01, .04), (.05, .09), (.10, .19), (.20, .29)]   # taker's longshot price
FAV_BANDS = [(.85, .89), (.90, .93), (.94, .97)]                 # our favorite price


def subcat(cat, tags, series):
    t = (tags or "").split("|")[0].strip() or "untagged"
    if cat in ("Elections", "Politics", "Entertainment", "Science and Technology"):
        return f"{cat}: {t}"
    return cat


def load(run_dirs):
    series, markets, fills = {}, {}, []
    for d in map(Path, run_dirs):
        for s in csv.DictReader(open(d / "series.csv")):
            series[s["series"]] = s
        for m in csv.DictReader(gzip.open(d / "sample.csv.gz", "rt")):
            if m["fetched"] == "1":
                markets[m["ticker"]] = m
        for t in csv.DictReader(gzip.open(d / "trades_agg.csv.gz", "rt")):
            m = markets.get(t["ticker"])
            if not m:
                continue
            yp = float(t["yes_price"])
            if not .01 <= yp <= .99:
                continue
            s = series.get(m["series"], {})
            mult = float(s.get("fee_multiplier") or 1)
            maker_fee_on = "maker" in (s.get("fee_type") or "")
            side = t["taker_side"]
            tp = yp if side == "yes" else round(1 - yp, 2)      # taker's price for his side
            mp = round(1 - tp, 2)                                # maker's price for his side
            taker_won = m["result"] == side
            w = float(m["weight"])
            c = float(t["count"]) * w
            base = {
                "cat": s.get("category", ""), "sub": subcat(s.get("category", ""), s.get("tags"),
                                                            m["series"]),
                "series": m["series"], "event": m["event"], "month": m["close"][:7],
                "ttc": t["ttc"], "size": t["size"], "through": t["through"] == "1",
                "trunc": m["truncated"] == "1", "c": c,
            }
            mfee = (0.0175 * mult * mp * (1 - mp) + 0.001) if maker_fee_on else 0.0
            maker_pnl = (0.0 if taker_won else 1.0) - mp - mfee
            if tp < .30:
                b = band(tp, LONG_BANDS)
                if b:
                    row = dict(base, band=b, pnl=c * maker_pnl, cost=c * mp)
                    if side == "yes":
                        fills.append(dict(row, strat="fade"))
                    fills.append(dict(row, strat="fade_any"))
            if .85 <= mp <= .97:
                fills.append(dict(base, strat="fav_mkr", band=band(mp, FAV_BANDS),
                                  pnl=c * maker_pnl, cost=c * mp))
            if .85 <= tp <= .97:
                tfee = 0.07 * mult * tp * (1 - tp) + 0.003
                fills.append(dict(base, strat="fav_tkr", band=band(tp, FAV_BANDS),
                                  pnl=c * ((1.0 if taker_won else 0.0) - tp - tfee),
                                  cost=c * tp))
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
    months = {r["month"] for r in rows}
    return {"events": len(evs), "pos": 100 * sum(v[1] > 0 for v in evs) / len(evs), "c": n,
            "cpc": sum(v[1] for v in evs) / n * 100, "lo": b[int(.05 * boots)],
            "hi": b[int(.95 * boots) - 1], "months": len(months)}


def table(title, groups, rnd, min_events=8):
    lines = [f"\n#### {title}\n",
             "| slice | events | % events up | contracts | c/contract | 90% CI |",
             "|---|---:|---:|---:|---:|---|"]
    for k in sorted(groups):
        s = summarize(groups[k], rnd)
        if not s or s["events"] < min_events:
            continue
        lines.append(f"| {k} | {s['events']} | {s['pos']:.0f} | {s['c']:,.0f} | "
                     f"{s['cpc']:+.2f} | {s['lo']:+.1f} to {s['hi']:+.1f} |")
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


def verdict_rows(rows, rnd):
    """One line per subcategory with the numbers a GO/NO-GO needs."""
    out = []
    per_sub = defaultdict(list)
    for r in rows:
        per_sub[r["sub"]].append(r)
    all_months = sorted({r["month"] for r in rows})
    last6 = set(all_months[-6:])
    split = all_months[len(all_months) // 2] if all_months else ""
    for sub, rr in sorted(per_sub.items()):
        s = summarize(rr, rnd)
        if not s or s["events"] < 8:
            continue
        early = summarize([r for r in rr if r["month"] < split], rnd, 50)
        late = summarize([r for r in rr if r["month"] >= split], rnd, 50)
        wf = [x for x in walk_forward(rr, lambda r: (r["band"], r["ttc"])) if x[2] > 0]
        wfc, wfp = sum(x[2] for x in wf), sum(x[3] for x in wf)
        mo = defaultdict(float)
        for r in rr:
            mo[r["month"]] += r["pnl"]
        cost6 = sum(r["cost"] for r in rr if r["month"] in last6)
        ctr6 = sum(r["c"] for r in rr if r["month"] in last6)
        cd6 = sum(r["cost"] * TTC_MID_DAYS[r["ttc"]] for r in rr if r["month"] in last6)
        days = cd6 / cost6 if cost6 else 0
        per_m = cost6 / max(len(last6), 1)
        bank = 0.05 * per_m * max(days, 1) / 30
        out.append({"sub": sub, **s, "early": early["cpc"] if early else None,
                    "late": late["cpc"] if late else None,
                    "wf_cpc": wfp / wfc * 100 if wfc else None, "wf_months": len(wf),
                    "wf_up": sum(x[3] > 0 for x in wf),
                    "months_up": sum(v > 0 for v in mo.values()), "n_months": len(mo),
                    "flow_m": per_m, "ctr_m": ctr6 / max(len(last6), 1), "days": days,
                    "bank": bank})
    return out


def ci(t):
    return f"{t['lo']:+.1f} to {t['hi']:+.1f}" if t else "n/a"


def go(v, thru):
    """GO if both fill models agree, CI is above zero on the pessimistic model,
    it held in both halves and walk-forward, and enough events."""
    if thru is None:
        return "NO-GO"
    ok = (v["events"] >= 40 and thru["lo"] > 0 and thru["cpc"] >= 1.5 and v["cpc"] > 0
          and (thru["early"] or 0) > 0 and (thru["late"] or 0) > 0
          and (thru["wf_cpc"] or 0) > 0 and thru["months_up"] >= 0.6 * thru["n_months"])
    if ok:
        return "GO"
    if thru["cpc"] > 1 and thru["events"] >= 20 and (thru["late"] or 0) > 0:
        return "WATCH"
    return "NO-GO"


def main():
    series, markets, fills = load(ARGS)
    rnd = random.Random(5)
    L = [f"# Longshots and favorites, full history ({len(markets):,} sampled markets, "
         f"{len(fills):,} fill cells)", ""]
    summary_rows = []
    for strat, desc in STRATS.items():
        sr = [r for r in fills if r["strat"] == strat]
        if not sr:
            continue
        L.append(f"\n## {desc}")
        vall = {v["sub"]: v for v in verdict_rows(sr, rnd)}
        vthr = {v["sub"]: v for v in verdict_rows([r for r in sr if r["through"]], rnd)}
        L.append("\n### Verdict per subcategory (c/contract after fees; 'thru' = back-of-queue "
                 "fills only)\n")
        L.append("| subcategory | verdict | events | c/ctr all | c/ctr thru | thru 90% CI | "
                 "1st half | 2nd half | walk-fwd | months up | $ flow/mo | days held | "
                 "bankroll it absorbs | $/mo at 5% of flow |")
        L.append("|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for sub, v in sorted(vall.items(), key=lambda kv: -kv[1]["c"]):
            t = vthr.get(sub)
            g = go(v, t)

            def fmt(x):
                return "n/a" if x is None else f"{x:+.1f}"
            L.append(f"| {sub} | **{g}** | {v['events']} | {v['cpc']:+.2f} | "
                     f"{fmt(t and t['cpc'])} | "
                     f"{ci(t)} | "
                     f"{fmt(t and t['early'])} | {fmt(t and t['late'])} | "
                     f"{fmt(t and t['wf_cpc'])} | "
                     f"{(str(t['months_up']) + '/' + str(t['n_months'])) if t else 'n/a'} | "
                     f"${v['flow_m']:,.0f} | {v['days']:.1f} | ${max(v['bank'], 1):,.0f} | "
                     f"${0.05 * v['ctr_m'] * (t['cpc'] if t else v['cpc']) / 100:,.0f} |")
            summary_rows.append({"strat": strat, "sub": sub, "verdict": g, "events": v["events"],
                                 "cpc_all": round(v["cpc"], 2),
                                 "cpc_thru": round(t["cpc"], 2) if t else "",
                                 "thru_lo": round(t["lo"], 2) if t else "",
                                 "late_thru": round(t["late"], 2) if t and t["late"] is not None else "",
                                 "wf_thru": round(t["wf_cpc"], 2) if t and t["wf_cpc"] is not None else "",
                                 "flow_m": round(v["flow_m"]), "bank": round(v["bank"]),
                                 "profit_m_5pct": round(0.05 * v["ctr_m"] * (
                                     t["cpc"] if t else v["cpc"]) / 100, 2)})
        for model, sel in (("all fills", lambda r: True),
                           ("back-of-queue fills", lambda r: r["through"])):
            rows = [r for r in sr if sel(r)]
            L.append(f"\n### Detail, {model}")
            g = defaultdict(list)
            for r in rows:
                g[f"{r['cat']} / ALL"].append(r)
            L += table("By category", g, rnd)
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
                g[f"{r['cat']} / {r['month']}"].append(r)
            L += table("By category and month", g, rnd, min_events=5)
            g = defaultdict(list)
            for r in rows:
                if not r["trunc"]:
                    g[f"{r['cat']} (untruncated markets)"].append(r)
            L += table("Robustness: markets whose trade history was complete", g, rnd)
            L.append("\n#### Walk-forward, all subcategories (cell = sub x band x time to close)\n")
            L.append("| month | cells | contracts | c/contract |")
            L.append("|---|---:|---:|---:|")
            tc = tp = 0.0
            for mo, n, c, p in walk_forward(rows, lambda r: (r["sub"], r["band"], r["ttc"])):
                tc += c
                tp += p
                L.append(f"| {mo} | {n} | {c:,.0f} | {p / c * 100 if c else 0:+.2f} |")
            L.append(f"| **total** | | {tc:,.0f} | {tp / tc * 100 if tc else 0:+.2f} |")
    txt = "\n".join(L) + "\n"
    if OUTF:
        Path(OUTF).write_text(txt)
    else:
        print(txt)
    if CSVF and summary_rows:
        with open(CSVF, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(summary_rows[0]))
            w.writeheader()
            w.writerows(summary_rows)


if __name__ == "__main__":
    main()
