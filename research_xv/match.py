"""Match Polymarket US game-winner markets to Kalshi game markets and price the arbitrage.

usage: python research_xv/match.py pmus.jsonl kalshi.jsonl
Polymarket US sides: marketSides[].quote = that side's best ask (long ask; short ask = 1 - long bid).
Fees: Kalshi taker 0.07*p*(1-p); Polymarket US taker 0.095*p*(1-p).
"""
import json
import sys
from collections import defaultdict

LEAGUE = {"nfl": "KXNFLGAME", "nba": "KXNBAGAME", "nhl": "KXNHLGAME", "cbb": "KXNCAAMBGAME",
          "wcbb": "KXNCAAWBGAME", "cfb": "KXNCAAFGAME"}
MON = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()


def f(v):
    try:
        return float(v["value"] if isinstance(v, dict) else v)
    except (TypeError, ValueError, KeyError):
        return None


def fee_k(p):
    return 0.07 * p * (1 - p)


def fee_u(p):
    return 0.095 * p * (1 - p)


def main(pp, kp):
    kal = defaultdict(dict)  # (series, yymmmdd) -> {suffix: market}
    for line in open(kp):
        k = json.loads(line)
        parts = k["ticker"].split("-")
        if len(parts) != 3 or parts[0] not in LEAGUE.values():
            continue
        kal[(parts[0], parts[1][:7])][parts[2]] = k
    rows = []
    n_pm = 0
    for line in open(pp):
        m = json.loads(line)
        sl = m["slug"].split("-")
        if sl[0] != "aec" or len(sl) < 7 or sl[1] not in LEAGUE:
            continue
        n_pm += 1
        y, mo, d = sl[-3], sl[-2], sl[-1]
        key = (LEAGUE[sl[1]], f"{y[2:]}{MON[int(mo) - 1]}{d}")
        ev = kal.get(key)
        if not ev:
            continue
        sides = m.get("marketSides") or []
        if len(sides) != 2:
            continue
        for s in sides:
            t = s.get("team") or {}
            codes = {str(t.get(x) or "").upper() for x in ("abbreviation", "displayAbbreviation")}
            s["_k"] = next((ev[c] for c in codes if c in ev), None)
        long_s = next(s for s in sides if s.get("long"))
        short_s = next(s for s in sides if not s.get("long"))
        la, sa = f(long_s.get("quote")), f(short_s.get("quote"))
        if la is None or sa is None:
            continue
        for mine, other, my_ask, other_ask in ((long_s, short_s, la, sa), (short_s, long_s, sa, la)):
            k = mine.get("_k")
            if not k:
                continue
            kb, ka = f(k.get("yes_bid_dollars")), f(k.get("yes_ask_dollars"))
            if not kb or not ka or ka >= 1 or kb <= 0:
                continue
            # A: Kalshi YES(team) + Polymarket US other team
            a = 1 - ka - other_ask - fee_k(ka) - fee_u(other_ask)
            # B: Kalshi NO(team) at 1-kb + Polymarket US this team
            b = 1 - (1 - kb) - my_ask - fee_k(1 - kb) - fee_u(my_ask)
            rows.append((max(a, b), a, b, m["slug"], k["ticker"], kb, ka, my_ask, other_ask,
                         m.get("gameStartTime"), k.get("volume_24h_fp")))
    pairs = {r[3] for r in rows}
    print(f"Polymarket US game-winner markets in mapped leagues: {n_pm}; matched to Kalshi: {len(pairs)}")
    pos = sorted([r for r in rows if r[0] > 0], key=lambda r: -r[0])
    print(f"team-sides with a positive taker arbitrage after fees: {len(pos)} of {len(rows)}")
    for th in (0.01, 0.02, 0.05):
        print(f"  > {th * 100:.0f}c: {sum(r[0] > th for r in pos)}")
    gaps = sorted(abs((r[5] + r[6]) / 2 - r[7] + 0.0) for r in rows)
    if gaps:
        print(f"Kalshi mid vs Polymarket US ask, |diff| median {gaps[len(gaps) // 2] * 100:.1f}c")
    spreads = sorted(r[6] - r[5] for r in rows)
    print(f"Kalshi spread on these: median {spreads[len(spreads) // 2] * 100:.0f}c")
    pmsp = sorted(r[7] + r[8] - 1 for r in rows)
    print(f"Polymarket US spread (long ask + short ask - 1): median {pmsp[len(pmsp) // 2] * 100:.0f}c")
    print("\nbest 20:")
    for r in pos[:20]:
        print(f"  {r[0] * 100:+5.1f}c  {r[4]:34s} k {r[5]:.2f}/{r[6]:.2f}  pm this {r[7]:.2f} other {r[8]:.2f}"
              f"  start {r[9]}  k vol24h {r[10]}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
