"""Props / lines longshot-fade backtest (offline), bot-snapshot style.

At a decision time known in advance (game start from ESPN minus H hours, or
start + 1h for in-game), take the last real print. If one side is priced in the
band, fade it: buy the other side.
  taker: at the next real print where a taker bought that side within 10 min.
  maker: rest a bid 1c below the last print's price for that side; it fills only
         if a later print trades through it (back of queue). Two versions:
         cancel at game start, or leave it resting until close.
Hold to settlement. Fees: taker 0.07 p(1-p), maker 0.0175 p(1-p) only on series
that charge maker fees, 5-lot rounding.

usage: python research_late/analyze_props.py <data root> > out.md
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fetch as F  # noqa: E402  (ticker matcher)
from common import OOS_TS, fmt, maker_fee, rows, runs, stat, taker_fee  # noqa: E402

MAKER_FEE_SERIES = {"KXNBASPREAD", "KXNBATOTAL", "KXNFLSPREAD", "KXNFLTOTAL", "KXNCAAFSPREAD",
                    "KXNCAAFTOTAL", "KXNFLANYTD", "KXNFLFIRSTTD"}
LEAGUE = {"NBA": "nba", "NHL": "nhl", "NFL": "nfl", "MLB": "mlb"}
BANDS = [(0.03, 0.10), (0.10, 0.20), (0.20, 0.40)]
WHEN = {"pre6h": -6 * 3600, "pre2h": -2 * 3600, "pre15m": -900, "ingame1h": 3600}
LINES = ("SPREAD", "TOTAL")


def family(s):
    if any(x in s for x in LINES):
        return "lines"
    return "props"


def load(root):
    games, mk, trades = {}, [], defaultdict(list)
    for run in runs(root, "props"):
        for g in rows(run / "espn_games.csv.gz"):
            games[(g["league"], g["event_id"])] = [g[c] for c in F.GCOLS]
        for m in rows(run / "markets.csv.gz"):
            if m["fetched"] == "1":
                mk.append(m)
        for t in rows(run / "trades.csv.gz"):
            trades[t["ticker"]].append((int(t["t"]), float(t["yes_price"]), t["taker_side"],
                                        float(t["count"])))
    by = defaultdict(dict)
    for g in games.values():
        by[g[0]].setdefault(g[3], []).append(g)
    return by, mk, trades


def main():
    by, mk, trades = load(sys.argv[1])
    start_cache = {}
    res = defaultdict(list)
    cal = defaultdict(list)
    vol = defaultdict(float)
    nm = defaultdict(int)
    unmatched = defaultdict(int)
    for m in mk:
        s = m["series"]
        lg = LEAGUE.get(s[2:5])
        ev = m["event"]
        if ev not in start_cache:
            g = F.match(ev, by.get(lg, {})) if lg else None
            start_cache[ev] = F.ts(g[2].replace("Z", ":00Z")) if g else None
        st = start_cache[ev]
        if not st:
            unmatched[s] += 1
            continue
        nm[s] += 1
        trs = sorted(trades.get(m["ticker"], []))
        if not trs:
            continue
        close = int(m["close_ts"])
        yes_won = m["result"] == "yes"
        fam = family(s)
        per = "OOS" if close >= OOS_TS else "IS"
        pre = [x for x in trs if x[0] < st]
        if pre:
            p = pre[-1][1]
            cal[(fam, s)].append((p, int(yes_won)))
        for t, _, _, c in trs:
            if t < st:
                vol[(s, "pre")] += c
        for wname, off in WHEN.items():
            t0 = st + off
            last = [x for x in trs if x[0] <= t0]
            if not last or t0 >= close:
                continue
            p = last[-1][1]
            for lo, hi in BANDS:
                if lo <= p <= hi:
                    cheap_yes = True
                elif lo <= 1 - p <= hi:
                    cheap_yes = False
                else:
                    continue
                for dirn in ("fade", "follow"):
                    buy_yes = (not cheap_yes) if dirn == "fade" else cheap_yes
                    won = yes_won == buy_yes
                    key = (fam + ("|yes-cheap" if cheap_yes else "|no-cheap"), s, wname, (lo, hi))
                    for t, yp, side, _ in trs:
                        if t0 < t <= t0 + 600 and side == ("yes" if buy_yes else "no"):
                            cost = yp if buy_yes else 1 - yp
                            res[key + (dirn + " taker", per)].append(
                                ((1 if won else 0) - cost - taker_fee(cost), ev))
                            break
                    bid = (p if buy_yes else 1 - p) - 0.01
                    if bid <= 0:
                        continue
                    for cancel, lab in ((st, "maker-cancel@start"), (close, "maker-rest")):
                        if cancel <= t0:
                            continue
                        for t, yp, side, _ in trs:
                            if t <= t0 or t > cancel:
                                continue
                            through = (side == "no" and yp < bid - 1e-9) if buy_yes else \
                                (side == "yes" and 1 - yp < bid - 1e-9)
                            if through:
                                fee = maker_fee(bid) if s in MAKER_FEE_SERIES else 0.0
                                res[key + (dirn + " " + lab, per)].append(
                                    ((1 if won else 0) - bid - fee, ev))
                                break
                break
                break
    print("# Props and lines: longshot-fade backtest\n")
    print("Markets matched to a game start:", sum(nm.values()),
          " unmatched:", dict(unmatched), "\n")
    print("## Pooled by family\n")
    print("| family | when | band (cheap side) | role | IS | OOS |")
    print("|---|---|---|---|---|---|")
    pooled = defaultdict(list)
    for k, xs in res.items():
        fam, s, w, band, role, per = k
        pooled[(fam, w, band, role, per)] += xs
    ROLES = [d + " " + r for d in ("fade", "follow")
             for r in ("taker", "maker-cancel@start", "maker-rest")]
    for fam in sorted({k[0] for k in pooled}):
        for w in WHEN:
            for band in BANDS:
                for role in ROLES:
                    a = pooled.get((fam, w, band, role, "IS"), [])
                    b = pooled.get((fam, w, band, role, "OOS"), [])
                    if not a and not b:
                        continue
                    sa = stat([x[0] for x in a], [x[1] for x in a]) if a else None
                    sb = stat([x[0] for x in b], [x[1] for x in b]) if b else None
                    print(f"| {fam} | {w} | {band[0]:.2f}-{band[1]:.2f} | {role} | {fmt(sa)} | "
                          f"{fmt(sb)} |")
    print("\n## By series (pre2h, cheap side = YES, band 0.10-0.40 pooled)\n")
    print("| series | role | IS | OOS | pre-game volume (contracts, sampled mkts) |")
    print("|---|---|---|---|---|")
    ser = defaultdict(list)
    for k, xs in res.items():
        fam, s, w, band, role, per = k
        if w == "pre2h" and band != (0.03, 0.10) and fam.endswith("yes-cheap"):
            ser[(s, role, per)] += xs
    for s in sorted({k[0] for k in ser}):
        for role in ROLES:
            a, b = ser.get((s, role, "IS"), []), ser.get((s, role, "OOS"), [])
            if not a and not b:
                continue
            sa = stat([x[0] for x in a], [x[1] for x in a]) if a else None
            sb = stat([x[0] for x in b], [x[1] for x in b]) if b else None
            print(f"| {s} | {role} | {fmt(sa)} | {fmt(sb)} | {vol[(s, 'pre')]:,.0f} |")
    print("\n## Calibration: last pre-game print vs result\n")
    print("| family | YES price | n | YES win rate | avg price |")
    print("|---|---|---|---|---|")
    pc = defaultdict(list)
    for (fam, s), xs in cal.items():
        pc[fam] += xs
    for fam, xs in sorted(pc.items()):
        for lo, hi in ((0, .05), (.05, .1), (.1, .2), (.2, .3), (.3, .4), (.4, .6), (.6, .7),
                       (.7, .8), (.8, .9), (.9, .95), (.95, 1.01)):
            sub = [x for x in xs if lo <= x[0] < hi]
            if sub:
                print(f"| {fam} | {lo:.2f}-{hi:.2f} | {len(sub)} | "
                      f"{sum(w for _, w in sub) / len(sub):.3f} | "
                      f"{sum(p for p, _ in sub) / len(sub):.3f} |")


if __name__ == "__main__":
    main()
