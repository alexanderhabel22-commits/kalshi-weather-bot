"""Late-game fade backtest on NBA / NHL Kalshi markets (offline).

Clock: ESPN play-by-play maps wallclock -> regulation seconds left, so the
"last 10 minutes" trigger uses only what a live feed would show (plus a lag).
Entry: the first 1-min quote after the trigger where the underdog side is in the
band; buy the favourite side (NO on the dog, or YES on the fav, whichever is
cheaper) at the ask of the NEXT minute's quote (latency), hold to settlement.
Lines (spread/total markets): same, per market, cheap side = YES or NO.
Maker variant: rest a bid for the favourite side at (that minute's bid); it
fills only if a later taker print trades through it (back of queue).

usage: python research_late/analyze_late.py <data root> > out.md
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import OOS_TS, fmt, maker_fee, rows, runs, stat, taker_fee  # noqa: E402

LAG = 30  # seconds of feed delay assumed
BANDS = [(0.10, 0.40), (0.10, 0.20), (0.20, 0.30), (0.30, 0.40), (0.03, 0.10)]
TRIG = [600, 300, 120]  # regulation seconds left


def clock_s(txt):
    if not txt:
        return None
    try:
        if ":" in txt:
            a, b = txt.split(":")
            return int(a) * 60 + float(b)
        return float(txt)
    except ValueError:
        return None


def remaining(lg, period, clk):
    c = clock_s(clk)
    if c is None or period is None:
        return None
    if lg == "nba":
        if period <= 4:
            return (4 - period) * 720 + c
        return min(c, 300)  # OT: always "late"
    if period <= 3:
        return (3 - period) * 1200 + (1200 - c)
    return 0.0


def season(t):
    d = datetime.fromtimestamp(t, timezone.utc)
    y = d.year if d.month >= 8 else d.year - 1
    return f"{y}-{(y + 1) % 100:02d}"


def load(root):
    for run in runs(root, "late"):
        lg = None
        games, mk, plays, cand, trades = {}, defaultdict(list), defaultdict(list), \
            defaultdict(list), defaultdict(list)
        for r in rows(run / "games_matched.csv.gz"):
            games[r["kalshi_event"]] = r
            lg = r["league"]
        if not games:
            continue
        suf2ev = {e.split("-", 1)[-1]: e for e in games}
        for r in rows(run / "markets.csv.gz"):
            ev = suf2ev.get(r["event"].split("-", 1)[-1])
            if ev:
                mk[ev].append(r)
        for r in rows(run / "plays.csv.gz"):
            plays[r["event_id"]].append(r)
        for r in rows(run / "candles.csv.gz"):
            if r["yes_bid"] != "" and r["yes_ask"] != "":
                cand[r["ticker"]].append((int(r["ts"]), float(r["yes_bid"]), float(r["yes_ask"])))
        for r in rows(run / "trades.csv.gz"):
            trades[r["ticker"]].append((int(r["t"]), float(r["yes_price"]), r["taker_side"]))
        yield lg, games, mk, plays, cand, trades


def trigger_ts(lg, pl, secs):
    best = None
    for p in pl:
        rem = remaining(lg, int(p["period"] or 0), p["clock"])
        t = int(p["wall_ts"] or 0)
        if rem is not None and t and rem <= secs:
            best = t if best is None else min(best, t)
    return None if best is None else best + LAG


def maker_fill(trs, t0, side_yes, price, close):
    """Back-of-queue: our bid to buy `side` at `price` fills if a later print trades
    through it (taker sold to us at a strictly better level)."""
    for t, yp, ts_ in trs:
        if t <= t0 or t > close:
            continue
        if side_yes and ts_ == "no" and yp < price - 1e-9:
            return True
        if not side_yes and ts_ == "yes" and (1 - yp) < price - 1e-9:
            return True
    return False


def main():
    root = sys.argv[1]
    res = defaultdict(list)  # key -> [(pnl, cluster, ts, wp_gap)]
    cal = defaultdict(list)
    ngames = defaultdict(int)
    for lg, games, mk, plays, cand, trades in load(root):
        for ev, g in games.items():
            ms = mk.get(ev, [])
            gm = [m for m in ms if m["series"].endswith("GAME")]
            if len(gm) != 2:
                continue
            pl = plays.get(g["event_id"], [])
            if not pl:
                continue
            ngames[lg] += 1
            close = max(int(m["close_ts"]) for m in gm)
            for secs in TRIG:
                t0 = trigger_ts(lg, pl, secs)
                if t0 is None:
                    continue
                # game markets: dog = team with lower YES mid at the quote
                a, b = gm
                qa = {t: (bd, ak) for t, bd, ak in cand.get(a["ticker"], [])}
                qb = {t: (bd, ak) for t, bd, ak in cand.get(b["ticker"], [])}
                times = sorted(t for t in set(qa) & set(qb) if t0 <= t < close)
                for lo, hi in BANDS:
                    for i, t in enumerate(times[:-1]):
                        (ab, aa), (bb, ba) = qa[t], qb[t]
                        ma, mb = (ab + aa) / 2, (bb + ba) / 2
                        dog, fav, qd, qf = (a, b, qa, qb) if ma < mb else (b, a, qb, qa)
                        md = min(ma, mb)
                        if not lo <= md <= hi:
                            continue
                        tn = times[i + 1]
                        cost = min(qf[tn][1], 1 - qd[tn][0])
                        if not 0 < cost < 1:
                            break
                        win = fav["result"] == "yes"
                        pnl = (1 if win else 0) - cost - taker_fee(cost)
                        per = "OOS" if t >= OOS_TS else "IS"
                        res[(lg, "game", secs, (lo, hi), "taker", per)].append((pnl, ev))
                        res[(lg, "game", secs, (lo, hi), "taker", season(t))].append((pnl, ev))
                        # maker: rest at the fav bid (or 1 - dog ask)
                        bid = max(qf[t][0], 1 - qd[t][1])
                        if 0 < bid < 1:
                            filled = (maker_fill(trades.get(fav["ticker"], []), t, True, bid, close)
                                      or maker_fill(trades.get(dog["ticker"], []), t, False, bid,
                                                    close))
                            if filled:
                                pm = (1 if win else 0) - bid - maker_fee(bid)
                                res[(lg, "game", secs, (lo, hi), "maker", per)].append((pm, ev))
                        if secs == 600 and (lo, hi) == (0.10, 0.40):
                            cal[lg].append((md, 0 if win else 1))
                        break
                # lines: each spread/total market separately
                for m in ms:
                    if m["series"].endswith("GAME"):
                        continue
                    q = sorted((x for x in cand.get(m["ticker"], []) if t0 <= x[0] < close))
                    kind = "spread" if "SPREAD" in m["series"] else "total"
                    for lo, hi in BANDS[:1]:
                        for i, (t, bd, ak) in enumerate(q[:-1]):
                            mid = (bd + ak) / 2
                            if lo <= mid <= hi:
                                buy_yes = False
                            elif lo <= 1 - mid <= hi:
                                buy_yes = True
                            else:
                                continue
                            _, nb, na = q[i + 1]
                            cost = na if buy_yes else 1 - nb
                            if not 0 < cost < 1:
                                break
                            win = (m["result"] == "yes") == buy_yes
                            pnl = (1 if win else 0) - cost - taker_fee(cost)
                            per = "OOS" if t >= OOS_TS else "IS"
                            res[(lg, kind, secs, (lo, hi), "taker", per)].append((pnl, ev))
                            break
    print("# Late-game fade backtest (NBA, NHL)\n")
    print("Games with Kalshi + ESPN clock:", dict(ngames), "\n")
    print("Returns in cents per contract after fees (5-lot), buying the favourite side "
          "(fading the 10-40c side), held to settlement. ± is a 95% CI by game.\n")
    keys = sorted(res, key=lambda k: (k[0], k[1], -k[2], k[3], k[4], k[5]))
    print("| league | market | trigger (reg. sec left) | dog band | role | period | result |")
    print("|---|---|---|---|---|---|---|")
    for k in keys:
        xs = res[k]
        s = stat([x[0] for x in xs], [x[1] for x in xs])
        print(f"| {k[0]} | {k[1]} | {k[2]} | {k[3][0]:.2f}-{k[3][1]:.2f} | {k[4]} | {k[5]} | {fmt(s)} |")
    print("\n## Calibration: underdog price at first 10-40c quote in last 10 min vs win rate\n")
    print("| league | dog price | n | dog win rate | avg price |")
    print("|---|---|---|---|---|")
    for lg, xs in sorted(cal.items()):
        for lo, hi in ((0.10, 0.20), (0.20, 0.30), (0.30, 0.40)):
            sub = [x for x in xs if lo <= x[0] <= hi]
            if sub:
                print(f"| {lg} | {lo:.2f}-{hi:.2f} | {len(sub)} | "
                      f"{sum(w for _, w in sub) / len(sub):.3f} | "
                      f"{sum(p for p, _ in sub) / len(sub):.3f} |")


if __name__ == "__main__":
    main()
