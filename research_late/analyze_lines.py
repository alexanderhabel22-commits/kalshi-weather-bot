"""Kalshi spread/total markets vs the DraftKings closing line (offline).

For each game with a DraftKings close (ESPN odds, Jan-Nov 2026), find the Kalshi
market whose strike equals the DK line (fav wins by over |spread|, or total over
the DK total). A sportsbook line is set so each side is ~50%. If Kalshi prices
that market away from 50c, the gap is a potential edge vs the book.
Strategy: at the last real print before start, if the price is <= 50-G buy YES,
>= 50+G buy NO, paying that price + 1c (spread) + taker fee; hold.

usage: python research_late/analyze_lines.py <data root> <games.csv.gz> > out.md
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fetch as F  # noqa: E402
from common import fmt, rows, runs, stat, taker_fee  # noqa: E402

SER = {"nba": ("KXNBASPREAD", "KXNBATOTAL"), "nhl": ("KXNHLSPREAD", "KXNHLTOTAL"),
       "mlb": ("KXMLBSPREAD", "KXMLBTOTAL"), "nfl": ("KXNFLSPREAD", "KXNFLTOTAL")}


def main():
    root, gpath = sys.argv[1], sys.argv[2]
    dk = {}
    for r in rows(gpath):
        if r["provider"] == "Draft Kings" and r["spread"] and r["total"]:
            dk[(r["league"], r["event_id"])] = r
    games, mk, trades = {}, defaultdict(list), defaultdict(list)
    for run in runs(root, "props"):
        for g in rows(run / "espn_games.csv.gz"):
            games[(g["league"], g["event_id"])] = [g[c] for c in F.GCOLS]
        for m in rows(run / "markets.csv.gz"):
            if m["fetched"] == "1" and any(m["series"] in v for v in SER.values()):
                mk[m["event"]].append(m)
        for t in rows(run / "trades.csv.gz"):
            if any(t["ticker"].startswith(s + "-") for v in SER.values() for s in v):
                trades[t["ticker"]].append((int(t["t"]), float(t["yes_price"])))
    by = defaultdict(dict)
    for g in games.values():
        by[g[0]].setdefault(g[3], []).append(g)
    obs = defaultdict(list)  # (lg, kind) -> (price, yes_won, game)
    for ev, ms in mk.items():
        s = ms[0]["series"]
        lg = next(k for k, v in SER.items() if s in v)
        g = F.match(ev, by.get(lg, {}))
        if not g:
            continue
        d = dk.get((lg, g[1]))
        if not d:
            continue
        st = F.ts(g[2].replace("Z", ":00Z"))
        kind = "spread" if "SPREAD" in s else "total"
        if kind == "spread":
            sp = float(d["spread"])
            fav_loc = d["h_loc"] if sp < 0 else d["a_loc"]
            line = abs(sp)
            cand = [m for m in ms if m["strike"] and abs(float(m["strike"]) - line) < 1e-6
                    and fav_loc.lower() in m["sub"].lower()]
        else:
            line = float(d["total"])
            cand = [m for m in ms if m["strike"] and abs(float(m["strike"]) - line) < 1e-6]
        if len(cand) != 1:
            continue
        m = cand[0]
        pre = [p for t, p in sorted(trades.get(m["ticker"], [])) if t < st]
        if not pre:
            continue
        obs[(lg, kind)].append((pre[-1], m["result"] == "yes", ev))
    print("# Kalshi lines vs DraftKings closing line\n")
    print("| league | market | games | avg Kalshi price at DK line | YES rate | "
          "gap>=4c trades | gap>=8c trades |")
    print("|---|---|---|---|---|---|---|")
    for (lg, kind), xs in sorted(obs.items()):
        n = len(xs)
        avgp = sum(x[0] for x in xs) / n
        yr = sum(x[1] for x in xs) / n
        cells = []
        for gap in (0.04, 0.08):
            pn, cl = [], []
            for p, yw, ev in xs:
                if p <= 0.5 - gap:
                    c = p + 0.01
                    pn.append((1 if yw else 0) - c - taker_fee(c))
                elif p >= 0.5 + gap:
                    c = 1 - p + 0.01
                    pn.append((0 if yw else 1) - c - taker_fee(c))
                else:
                    continue
                cl.append(ev)
            cells.append(fmt(stat(pn, cl)) if pn else "—")
        print(f"| {lg} | {kind} | {n} | {avgp:.3f} | {yr:.3f} | {cells[0]} | {cells[1]} |")


if __name__ == "__main__":
    main()
