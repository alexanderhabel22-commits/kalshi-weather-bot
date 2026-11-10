"""Research only: replay the 15-min favourite bid on Polymarket's 15-min crypto markets.

Read-only public data (gamma-api + data-api). Never trades, needs no keys.

For a sample of past 15-min "Up or Down" windows per coin it pulls the trade tape for
the last 10 minutes and simulates our Kalshi rule: a resting maker BUY on the side
priced 71-95c (the favourite), filled only when a taker sells that side BELOW our
bid (back of queue, "traded through"). Also logs an at-price fill and a taker entry.
One CSV row per window; analysis happens in analyze.py.

usage: python research_poly/fetch.py OUTDIR [days] [minutes_budget]
"""
import csv
import json
import os
import random
import sys
import time

import requests

GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"
COINS = ["btc", "eth", "sol", "xrp"]
MIN_FAV, MAX_FAV = 0.71, 0.95
LOOKBACKS = (600, 300)  # bid during the last 10 min (BTC rule) / last 5 min (alt rule)

S = requests.Session()
S.headers["User-Agent"] = "research-replay/1.0"
LOG = None
PROBE = [0]


def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg, flush=True)
    LOG.write(msg + "\n")
    LOG.flush()


def get(url, params=None, tries=5):
    for i in range(tries):
        try:
            r = S.get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 + 3 * i)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 - research script, retry anything
            if i == tries - 1:
                log("ERR", url, params, e)
                return None
            time.sleep(1 + 2 * i)
    return None


def markets_for(slugs):
    out = {}
    js = get(GAMMA + "/markets", params=[("slug", s) for s in slugs]
             + [("closed", "true"), ("limit", len(slugs))])
    for m in js or []:
        out[m.get("slug")] = m
    return out


def parse_list(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return []
    return v or []


def discover():
    """Coin prefixes of the 15-min up/down markets that exist right now."""
    found = set()
    for off in (0, 100, 200):
        js = get(GAMMA + "/markets", params={"tag_id": 102467, "closed": "true", "limit": 100,
                                             "offset": off, "order": "endDate",
                                             "ascending": "false"})
        for m in js or []:
            sl = m.get("slug") or ""
            if "-updown-15m-" in sl:
                found.add(sl.split("-updown-15m-")[0])
    log("15-min coins found:", sorted(found))
    return sorted(found) or COINS


def tape(cond, since):
    """Taker trades for a market, oldest first, from `since` (unix) on."""
    rows, off = [], 0
    while off <= 3000:
        js = get(DATA + "/trades", params={"market": cond, "limit": 500, "offset": off,
                                            "takerOnly": "true"})
        if not js:
            break
        if PROBE[0] < 6:
            PROBE[0] += 1
            ts = [int(t["timestamp"]) for t in js]
            log("probe trades", cond[:12], "off", off, "n", len(js), "ts", min(ts), max(ts),
                "since", since, "cond_match", sum(t.get("conditionId") == cond for t in js))
        rows.extend(js)
        if len(js) < 500 or min(int(t["timestamp"]) for t in js) < since:
            break
        off += 500
    rows = [t for t in rows if int(t["timestamp"]) >= since]
    rows.sort(key=lambda t: int(t["timestamp"]))
    return rows


def up_view(t, up_name):
    """(implied Up price, +1 if taker pushes Up up / -1 if taker sells Up, shares)."""
    p = float(t["price"])
    is_up = t.get("outcome") == up_name
    u = p if is_up else 1 - p
    buy = t.get("side") == "BUY"
    push = 1 if (buy == is_up) else -1
    return u, push, float(t["size"])


def simulate(trades, up_name, end, lookback):
    """Rejoin rule: after each trade, bid 1c under the favourite's last price if it is in
    band; a later taker selling that side below the bid fills it (back of queue)."""
    res = {"strict": None, "touch": None, "taker": None}
    bid = None  # (side, price)
    start = end - lookback
    for t in trades:
        ts = int(t["timestamp"])
        if ts < start:
            continue
        u, push, _ = up_view(t, up_name)
        if bid is not None:
            side, b = bid
            side_px = u if side == "up" else 1 - u
            selling_side = (push == -1) if side == "up" else (push == 1)
            if selling_side and side_px <= b + 1e-9 and res["touch"] is None:
                res["touch"] = (side, b, ts)
            if selling_side and side_px < b - 1e-9 and res["strict"] is None:
                res["strict"] = (side, b, ts)
            if res["strict"] is not None:
                break
        fav, fp = ("up", u) if u >= 0.5 else ("down", 1 - u)
        if MIN_FAV <= fp <= MAX_FAV:
            b = round(int(fp * 100 + 1e-6) / 100 - 0.01, 2)
            bid = (fav, max(b, MIN_FAV))
            if res["taker"] is None:
                res["taker"] = (fav, round(min(0.99, int(fp * 100 + 1e-6) / 100 + 0.01), 2), ts)
        else:
            bid = None
    return res


def main():
    global LOG
    out = sys.argv[1]
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    budget = float(sys.argv[3]) if len(sys.argv) > 3 else 95
    os.makedirs(out, exist_ok=True)
    LOG = open(os.path.join(out, "log.txt"), "a")
    t0 = time.time()
    now = int(time.time()) // 900 * 900 - 1800
    starts = [now - 900 * k for k in range(days * 96)]
    coins = discover()
    random.seed(17)
    random.shuffle(starts)  # random sample across the whole period, so a cut-off run is unbiased
    f = open(os.path.join(out, "rows.csv"), "w", newline="")
    w = csv.writer(f)
    hdr = ["coin", "start", "end", "winner", "n_trades", "shares_10m", "usd_10m"]
    for lb in LOOKBACKS:
        for k in ("strict", "touch", "taker"):
            hdr += [f"{k}{lb}_side", f"{k}{lb}_px", f"{k}{lb}_ts"]
    w.writerow(hdr)
    done = 0
    for i in range(0, len(starts), 10):
        if time.time() - t0 > budget * 60:
            log("budget reached")
            break
        if i < 30:
            log("chunk", i, f"{time.time() - t0:.0f}s")
        chunk = starts[i:i + 10]
        slugs = [f"{c}-updown-15m-{s}" for s in chunk for c in coins]
        ms = markets_for(slugs)
        for slug in slugs:
            m = ms.get(slug)
            if not m:
                continue
            outs, px = parse_list(m.get("outcomes")), parse_list(m.get("outcomePrices"))
            if len(outs) != 2 or len(px) != 2 or not m.get("closed"):
                continue
            try:
                winner = "up" if float(px[0]) > 0.5 else "down"
                if outs[0].lower() != "up":
                    winner = "down" if winner == "up" else "up"
            except ValueError:
                continue
            up_name = outs[0] if outs[0].lower() == "up" else outs[1]
            start = int(slug.rsplit("-", 1)[1])
            end = start + 900
            tr = tape(m["conditionId"], end - max(LOOKBACKS))
            if done < 3:
                log("sample", slug, "trades", len(tr), "keys", sorted(tr[0]) if tr else None,
                    {k: tr[0].get(k) for k in ("side", "outcome", "price", "size", "timestamp")}
                    if tr else None)
            sh = sum(float(t["size"]) for t in tr)
            usd = sum(float(t["size"]) * float(t["price"]) for t in tr)
            row = [slug.split("-updown")[0], start, end, winner, len(tr), round(sh, 1), round(usd, 2)]
            for lb in LOOKBACKS:
                r = simulate(tr, up_name, end, lb)
                for k in ("strict", "touch", "taker"):
                    row += list(r[k]) if r[k] else ["", "", ""]
            w.writerow(row)
            done += 1
        f.flush()
        if done and (done % 200 < 40 or done < 50):
            log(f"{done} markets, {(time.time() - t0) / 60:.1f} min")
    log(f"finished {done} markets in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
