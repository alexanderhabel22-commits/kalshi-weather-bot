"""State gas daily-change favorites: PAPER ONLY test from Nov 25, 2026 (thread "Find new paper-test
ideas like our winners"; Alex Nov 25 12:47 AM ET: "Anything else we should dig into and get on paper").
Never live: config keeps live = false. Real money needs Alex's explicit OK.

Kalshi's KXAAAGASCD<ST> series (50 states, listed ~Nov 11-14, 2026): five brackets for the change in
AAA's average regular gas price from the previous day, in cents: LD <= -1.5 < SD <= -0.5 < MC <= 0.5
< SI <= 1.5 < LI. Each day D's event opens ~8 AM ET on D-1 and closes ~3:30 AM ET on D, when AAA
publishes.

Same idea as our big-event favorites: late, the bracket the market already favours wins more often
than its price says. Replay (Nov 12-24 2026, 50 states, hourly candles, taker at the ask + 1-ct fee,
first hour 11 PM-3 AM ET with ask in 70-97c): +6.3c +- 2.0 per contract on 169 entries, 11 days, 9
positive (errors clustered by day: all states move together). First 7 days vs last 6 both positive
at every entry hour 8 PM-3 AM. CAVEATS: only ~11 days, all in one falling-price stretch (national
average -1.5 to -4c every day); a 'model' that conditions on yesterday's change LOST -5c (the market
already prices momentum), and longshot NO lost -2c. Write-up:
/mnt/project-files/analysis/gas-daily/RESULTS.md.

Rule:
  - KXAAAGASCD<ST>-<YYMONDD>-<bracket> markets for day D, only from 11 PM ET on D-1 to 3:15 AM ET on D;
  - buy YES at the ask when it is in [0.70, 0.97] (taker), one entry per market, hold to settlement.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .. import kalshi
from ..trader import Intent, held_by_event
from ..util import HttpError

NAME = "gas_fav_paper"
STATES = ("AK AL AR AZ CA CO CT DE FL GA HI IA ID IL IN KS KY LA MA MD ME MI MN MO MS MT NC ND NE "
          "NH NJ NM NV NY OH OK OR PA RI SC SD TN TX UT VA VT WA WI WV WY").split()
BRACKETS = ("LD", "SD", "MC", "SI", "LI")
LABEL = {"LD": "down 1.5c+", "SD": "down 0.5-1.5c", "MC": "within 0.5c", "SI": "up 0.5-1.5c",
         "LI": "up 1.5c+"}
DEFAULTS = {"contracts": 1, "min_price": 0.70, "max_price": 0.97,
            "start_et": "23:00", "end_et": "03:15", "batch": 50}
ET = ZoneInfo("America/New_York")
log = logging.getLogger("wxbot")


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def target_day(now: datetime, p: dict) -> date | None:
    """The settlement day D whose markets may be bought now, or None outside the window
    (start_et on D-1 through end_et on D, ET)."""
    t = now.astimezone(ET)
    if t.time() >= _hm(p["start_et"]):
        return t.date() + timedelta(days=1)
    if t.time() <= _hm(p["end_et"]):
        return t.date()
    return None


def tickers_for(day: date) -> list[str]:
    tag = day.strftime("%y%b%d").upper()
    return [f"KXAAAGASCD{s}-{tag}-{b}" for s in STATES for b in BRACKETS]


def candidates(markets: list[dict], now: datetime, p: dict, held: dict | None = None,
               name: str = NAME) -> list[Intent]:
    held = held or {}
    day = target_day(now, p)
    if day is None:
        return []
    tag = day.strftime("%y%b%d").upper()
    lo, hi = float(p["min_price"]), float(p["max_price"])
    out = []
    for m in markets:
        tk = m.get("ticker") or ""
        parts = tk.split("-")
        if len(parts) != 3 or not parts[0].startswith("KXAAAGASCD") or parts[1] != tag \
                or parts[2] not in BRACKETS:
            continue
        if (m.get("status") or "active") not in ("active", "open"):
            continue
        ev = m.get("event_ticker") or f"{parts[0]}-{parts[1]}"
        if tk in held.get(ev, set()):
            continue                                   # one entry per market
        a = kalshi.price(m, "yes_ask")
        if a is None or not (lo <= a <= hi):
            continue
        st = parts[0][len("KXAAAGASCD"):]
        vol = float(m.get("volume_fp") or m.get("volume") or 0)
        out.append(Intent(name, tk, ev, "yes", round(a, 2), int(p["contracts"]), m.get("close_time") or "",
                          f"{st} gas {day:%b %d} {LABEL[parts[2]]}: YES",
                          f"late favorite, ask {a * 100:.0f}c (replay +6.3c/ct, 11 days)", vol, taker=True))
    return out


def fetch(ctx, day: date, batch: int) -> list[dict]:
    tks, out = tickers_for(day), []
    for i in range(0, len(tks), batch):
        js = ctx.http.get_json(f"{kalshi.BASE}/markets", params={"tickers": ",".join(tks[i:i + batch])})
        out.extend((js or {}).get("markets") or [])
    return out


def propose(ctx) -> list[Intent]:
    p = {**DEFAULTS, **(ctx.params or {})}
    day = target_day(ctx.now, p)
    if day is None:
        return []
    try:
        markets = fetch(ctx, day, int(p["batch"]))
    except HttpError as exc:
        log.info("%s: %s", NAME, exc)
        return []
    out = candidates(markets, ctx.now, p, held_by_event(ctx.orders, NAME, ctx.mode))
    log.info("%s: %d markets for %s, %d candidates", NAME, len(markets), day, len(out))
    return out
