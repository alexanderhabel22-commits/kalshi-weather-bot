"""Build a per-window trade ledger for the 15-min favourite-bid rule, with features
known BEFORE the trade, from Kalshi 1-min candles (hist15-research-data).

  python research_regime/ledger_m15.py <candle dir> <out.pkl>

Rule replayed (today's bot): first minute with <=10 min left where the favourite's bid is
71-95c -> buy 1 contract at that bid (filled; the candle replay is ~1c optimistic, see
analysis/m15-edge). Rule E variant: 14..10 min left, 80-95c. Maker fee $0 (ledger, Nov 10).
"""
import collections, csv, gzip, math, sys, zoneinfo, datetime
import pandas as pd

ET = zoneinfo.ZoneInfo("America/New_York")
SERIES = ["KXBTC15M", "KXSILVER15M", "KXGOLD15M", "KXETH15M", "KXSOL15M", "KXCOPPER15M",
          "KXPLATINUM15M", "KXPALLADIUM15M", "KXWTI15M"]


def rows(p):
    try:
        yield from csv.DictReader(gzip.open(p, "rt"))
    except EOFError:
        pass


def load(d, s):
    mk = {}
    for r in rows(f"{d}/{s}_markets.csv.gz"):
        if r["result"] in ("yes", "no"):
            mk[r["ticker"]] = dict(open=int(r["open_ts"]), close=int(r["close_ts"]), res=r["result"],
                                   floor=float(r["floor"] or 0), vol=float(r["volume"] or 0))
    cd = collections.defaultdict(dict)
    for r in rows(f"{d}/{s}_candles.csv.gz"):
        if r["ticker"] in mk:
            cd[r["ticker"]][int(r["t"])] = (float(r["bid"]), float(r["ask"]), float(r["vol"] or 0))
    return mk, cd


def entry(m, c, start, end, lo, hi):
    cum = 0.0
    for t in sorted(c):
        yb, ya, v = c[t]
        left = m["close"] - t
        if left > start:
            cum += v
            continue
        if left < end:
            break
        nob = round(1 - ya, 4)
        if lo <= yb <= hi:
            return dict(px=yb, side="yes", t=t, left=left / 60, spread=round(ya - yb, 4), mvol=cum)
        if lo <= nob <= hi:
            return dict(px=nob, side="no", t=t, left=left / 60, spread=round(ya - yb, 4), mvol=cum)
        cum += v
    return None


def build(d, s, rule):
    mk, cd = load(d, s)
    order = sorted(mk, key=lambda k: mk[k]["open"])
    byopen = {mk[k]["open"]: mk[k]["floor"] for k in order}
    start, end, lo, hi = (600, 60, 0.71, 0.95) if rule == "today" else (840, 600, 0.80, 0.95)
    out = []
    for k in order:
        m = mk[k]
        e = entry(m, cd.get(k, {}), start, end, lo, hi)
        if not e:
            continue
        o, f = m["open"], m["floor"]
        rets = []
        for j in range(1, 9):
            a, b = byopen.get(o - 900 * j), byopen.get(o - 900 * (j - 1))
            if a and b and a > 0 and b > 0:
                rets.append(math.log(b / a))
        rv = (sum(x * x for x in rets) / len(rets)) ** .5 if len(rets) >= 6 else float("nan")
        a = byopen.get(o - 3600)
        r1 = math.log(f / a) if a and f > 0 else float("nan")
        a24 = byopen.get(o - 86400)
        r24 = math.log(f / a24) if a24 and f > 0 else float("nan")
        sgn = 1 if e["side"] == "yes" else -1
        dt = datetime.datetime.fromtimestamp(e["t"], ET)
        win = int(e["side"] == m["res"])
        out.append(dict(series=s, rule=rule, ticker=k, t=e["t"], close=m["close"], px=e["px"], side=e["side"],
                        left=e["left"], spread=e["spread"], mvol=e["mvol"], rv2h=rv,
                        trend1h=sgn * r1, trend24h=sgn * r24, absret1h=abs(r1),
                        hour=dt.hour, dow=dt.weekday(), day=dt.date().isoformat(),
                        win=win, pnl=100 * ((1 - e["px"]) if win else -e["px"])))
    return pd.DataFrame(out)


if __name__ == "__main__":
    d, outp = sys.argv[1:3]
    dfs = []
    for s in SERIES:
        for rule in ("today", "early"):
            try:
                df = build(d, s, rule)
            except FileNotFoundError:
                continue
            print(s, rule, len(df), df.day.min() if len(df) else "", df.day.max() if len(df) else "",
                  round(df.pnl.mean(), 2) if len(df) else "")
            dfs.append(df)
    pd.concat(dfs).to_pickle(outp)
