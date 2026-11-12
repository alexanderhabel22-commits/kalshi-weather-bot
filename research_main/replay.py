"""Bot-faithful replay of the main bot's three resting-order strategies.

  python research_main/replay.py <run dir> <section> <out.pkl>     section: mentions | favorites | macro

Walks every event hour by hour on the hourly bid/ask candles (the bot scans every
30 min), applies the live rule of wxbot/strats/<section>.py (band, volume floor,
time window, per-event limit and pick order, best bid + 1c, one order per market,
the order rests until the market closes) and fills an order when a later trade
prints at our price ("touch": we improved the bid, so we are first there) or
through it ("through": the pessimistic, back-of-queue case). Money caps are not
applied: every order is one unit, so results are cents per order placed/filled.
`kind` = live (what the rule places), skipped (in the band but the event's slots
were full), small_event (favorites under the volume floor) or wide (first time a
market sat at 60-98c outside the live band), so the pick rule can be judged too.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

RULES = {
    "mentions": dict(side="no", lo=0.75, hi=0.90, min_vol=1000, min_h=1, max_h=168, per_event=3,
                     order="cheap", when="occ"),
    "macro": dict(side="no", lo=0.71, hi=0.95, min_vol=500, min_h=1, max_h=504, per_event=3,
                  order="cheap", when="close"),
    "favorites": dict(side="fav", lo=0.85, hi=0.96, min_vol=0, min_h=1, max_h=168, per_event=2,
                      order="rich", min_event_vol=1_000_000, when="close"),
}
WIDE = dict(lo=0.60, hi=0.98)       # also log orders outside the live band (tagged), for slicing


def load(run: str, sec: str):
    m = pd.read_csv(f"{run}/{sec}_markets.csv.gz")
    c = pd.read_csv(f"{run}/{sec}_candles.csv.gz")
    t = pd.read_csv(f"{run}/{sec}_trades.csv.gz")
    for k in ("yb", "ya"):
        c[k] = pd.to_numeric(c[k], errors="coerce")
    if c.ya.max() <= 0.011:                 # historical endpoint quirk: dollars / 100
        for k in ("yb", "ya", "yb_lo", "yb_hi", "ya_lo", "ya_hi", "last"):
            c[k] = c[k] * 100
    return m, c.sort_values(["ticker", "t"]), t.sort_values(["ticker", "t"])


def replay(m: pd.DataFrame, c: pd.DataFrame, t: pd.DataFrame, rule: dict) -> pd.DataFrame:
    m = m.copy()
    evmax = m.groupby("event").close_ts.transform("max")
    # when the thing happens: Mentions = occurrence time if given, else the event's last close
    # (markets close early when the word is said); the others = the market's close
    m["ev_ts"] = np.where(m.occ_ts > 0, m.occ_ts, evmax) if rule["when"] == "occ" else m.close_ts
    cg = {k: g for k, g in c.groupby("ticker")}
    tg = {k: g for k, g in t.groupby("ticker")}
    rows = []
    for ev, em in m.groupby("event"):
        books = {}
        for r in em.itertuples():
            g = cg.get(r.ticker)
            if g is None or len(g) == 0:
                continue
            tt = g.t.values.astype(np.int64)
            vol_after = g.vol.values[::-1].cumsum()[::-1] - g.vol.values       # traded after each candle
            books[r.ticker] = (r, tt, g.yb.values, g.ya.values, r.volume - vol_after)
        if not books:
            continue
        times = np.unique(np.concatenate([b[1] for b in books.values()]))
        live: dict[str, int] = {}                   # ticker -> order time (orders the live rule places)
        skipped, wide = set(), set()                # logged once each, never hold a slot
        for now in times:
            cands, ev_vol = [], 0.0
            for tk, (r, tt, yb, ya, vsofar) in books.items():
                i = np.searchsorted(tt, now, side="right") - 1
                if i < 0:
                    continue
                ev_vol += vsofar[i] if now < r.close_ts else r.volume
                if tk in live or now >= r.close_ts - 600 or now - tt[i] > 6 * 3600:
                    continue
                b, a = yb[i], ya[i]
                if not (np.isfinite(b) and np.isfinite(a) and 0 < b < a < 1):
                    continue
                h = (r.ev_ts - now) / 3600
                if not rule["min_h"] <= h <= rule["max_h"] or vsofar[i] < rule["min_vol"]:
                    continue
                if rule["side"] == "no" or b < 0.5:
                    side, bid, ask = "no", round(1 - a, 2), round(1 - b, 2)
                else:
                    side, bid, ask = "yes", round(b, 2), round(a, 2)
                px, joined = round(bid + 0.01, 2), 0
                if px >= ask:
                    px, joined = bid, 1
                cands.append((px, tk, side, bid, ask, joined, h, vsofar[i], r))
            if not cands:
                continue
            inband = [x for x in cands if rule["lo"] - 1e-9 <= x[0] <= rule["hi"] + 1e-9]
            inband.sort(key=lambda x: x[0] if rule["order"] == "cheap" else -x[0])
            big = ev_vol >= rule.get("min_event_vol", 0)
            free = rule["per_event"] - sum(1 for tk in live if books[tk][0].close_ts > now)
            for x in inband:
                px, tk, side, bid, ask, joined, h, vs, r = x
                if big and free > 0:
                    free -= 1
                    live[tk] = int(now)
                    kind = "live"
                elif tk not in skipped:
                    skipped.add(tk)
                    kind = "skipped" if big else "small_event"
                else:
                    continue
                rows.append(_order(r, tk, ev, side, px, bid, ask, joined, h, vs, ev_vol, now, kind, tg.get(tk)))
            for x in cands:
                px, tk, side, bid, ask, joined, h, vs, r = x
                if tk in wide or tk in live or tk in skipped or not WIDE["lo"] - 1e-9 <= px <= WIDE["hi"] + 1e-9 \
                        or rule["lo"] - 1e-9 <= px <= rule["hi"] + 1e-9:
                    continue
                wide.add(tk)
                rows.append(_order(r, tk, ev, side, px, bid, ask, joined, h, vs, ev_vol, now, "wide", tg.get(tk)))
    return pd.DataFrame(rows)


def _order(r, tk, ev, side, px, bid, ask, joined, h, vs, ev_vol, now, kind, tr):
    touch = through = np.nan
    if tr is not None:
        g = tr[tr.t.values > now]
        if side == "no":
            g = g[g.taker_side.values == "yes"]
            lvl = round(1 - px, 4)
            a, b = g[g.yes_price.values >= lvl - 1e-9], g[g.yes_price.values > lvl + 1e-9]
        else:
            g = g[g.taker_side.values == "no"]
            a, b = g[g.yes_price.values <= px + 1e-9], g[g.yes_price.values < px - 1e-9]
        touch = a.t.values[0] if len(a) else np.nan
        through = b.t.values[0] if len(b) else np.nan
    win = (r.result == side)
    return dict(ticker=tk, event=ev, series=r.series, side=side, px=px, bid=bid, ask=ask, joined=joined,
                hours=h, vol=vs, ev_vol=ev_vol, t0=int(now), kind=kind, win=int(win),
                pnl=100 * ((1 - px) if win else -px), touch=touch, through=through,
                close_ts=r.close_ts, early=getattr(r, "early", 0), total_vol=r.volume,
                title=getattr(r, "title", ""), sub=getattr(r, "sub", ""), cat=getattr(r, "cat", ""),
                event_volume=getattr(r, "event_volume", np.nan))


if __name__ == "__main__":
    run, sec, out = sys.argv[1:4]
    m, c, t = load(run, sec)
    rule = RULES[sec]
    df = replay(m, c, t, rule)
    df.to_pickle(out)
    print(sec, "markets", len(m), "rows", len(df), df.kind.value_counts().to_dict())
