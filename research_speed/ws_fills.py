"""Research: read the websocket recorder's hourly files (branch wsfeed-data, folder wsfeed/) and,
for every one of our real fills, ask: could a watcher that reacts in ~0.1 s have pulled the order
first? For each fill: seconds since the market's last 5c jump (from the recorder's "jump" lines)
and since the last trade before ours, plus the mid 1 s / 10 s / 60 s / 5 min later (our side).

    git clone --depth 1 --branch wsfeed-data <repo> wsd
    python research_speed/ws_fills.py wsd/wsfeed

A pull guard only helps on fills that came >= 1 s after a jump; fills inside the same sweep
(0 s) can't be saved by reacting to Kalshi's own prices. Read-only; never trades."""
import bisect
import collections
import glob
import gzip
import json
import sys


def load(folder):
    out = []
    for fn in sorted(glob.glob(f"{folder}/wsfeed-*.jsonl*")):
        op = gzip.open if fn.endswith(".gz") else open
        with op(fn, "rt") as f:
            out += [json.loads(x) for x in f if x.strip()]
    return out


def px(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v / 100 if v > 1 else v


def main(folder):
    rows = load(folder)
    quotes = collections.defaultdict(list)       # ticker -> [(t, mid)]
    trades = collections.defaultdict(list)       # ticker -> [t]
    jumps = collections.defaultdict(list)
    fills = []
    for r in rows:
        m = r.get("msg") or {}
        tk = m.get("market_ticker")
        if r.get("type") == "ticker" and tk:
            b, a = px(m.get("yes_bid_dollars") or m.get("yes_bid")), px(m.get("yes_ask_dollars") or m.get("yes_ask"))
            if b and a and 0 < a < 1:
                quotes[tk].append((r["t"], (a + b) / 2))
        elif r.get("type") == "trade" and tk:
            trades[tk].append(r["t"])
        elif r.get("kind") == "jump":
            jumps[r["ticker"]].append(r["t"])
        elif r.get("type") == "fill":
            fills.append(r)
    print(f"{len(fills)} fills, {sum(map(len, jumps.values()))} jumps, {len(quotes)} quoted markets")
    for f in fills:
        m = f["msg"]
        tk, side, t = m.get("market_ticker"), m.get("side"), f["t"]
        price = px(m.get("yes_price_dollars") or m.get("yes_price"))
        if side == "no" and price is not None:
            price = 1 - price
        q = quotes.get(tk, [])
        ts = [x[0] for x in q]

        def mid_at(s):
            i = bisect.bisect_right(ts, s) - 1
            if i < 0 or price is None:
                return None
            v = q[i][1] if side == "yes" else 1 - q[i][1]
            return round(100 * (v - price), 1)
        j = [x for x in jumps.get(tk, []) if x <= t]
        tr = [x for x in trades.get(tk, []) if x < t - 0.05]
        print(tk, side, price, m.get("action"), "count", m.get("count_fp") or m.get("count"),
              "since_jump_s", round(t - j[-1], 1) if j else None,
              "since_trade_s", round(t - tr[-1], 1) if tr else None,
              "mid+1s", mid_at(t + 1), "+10s", mid_at(t + 10), "+60s", mid_at(t + 60), "+5m", mid_at(t + 300))


if __name__ == "__main__":
    main(sys.argv[1])
