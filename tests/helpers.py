"""Realistic fake API responses for an NYC afternoon (Tue Nov 3, 2026)."""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

UTC = timezone.utc


def f_to_c(f):
    return (f - 32) * 5 / 9


def t_group(c):
    sign = "1" if c < 0 else "0"
    return f"T{sign}{abs(round(c * 10)):03d}0028"


def metar_feature(t, temp_f, rmk_extra="", icao="KNYC", with_tgroup=True, qc="V"):
    c = f_to_c(temp_f)
    tg = t_group(c) if with_tgroup else ""
    raw = f"{icao} {t:%d%H%M}Z 27008KT 10SM CLR {round(c):02d}/03 A3012 RMK AO2 SLP199 {tg} {rmk_extra}".strip()
    return {"properties": {
        "timestamp": t.isoformat(),
        "temperature": {"value": round(c, 1) if with_tgroup else float(round(c)),
                        "unitCode": "wmoUnit:degC", "qualityControl": qc},
        "rawMessage": raw}}


# Observed NYC temps (°F) at :51 past each UTC hour on Nov 3 (EST = UTC-5)
NYC_OBS = {
    5: 50.0, 6: 49.5, 7: 49.1, 8: 48.6, 9: 48.4, 10: 48.2, 11: 48.0, 12: 48.9,
    13: 51.1, 14: 53.6, 15: 55.9, 16: 57.9, 17: 59.5, 18: 60.6, 19: 61.2, 20: 59.4,
    21: 58.1,
}


def nyc_observations(until: datetime):
    feats = []
    for h, f in sorted(NYC_OBS.items()):
        t = datetime(2026, 11, 3, h, 51, tzinfo=UTC)
        if t > until:
            continue
        extra = ""
        if h == 17:   # synoptic 18Z report: max/min of 12Z-18Z (stamped 17:51)
            extra = f"1{0}{round(f_to_c(59.6) * 10):03d} 2{0}{round(f_to_c(48.7) * 10):03d}"
        feats.append(metar_feature(t, f, extra))
    feats.reverse()  # NWS returns newest first
    return {"features": feats}


def diurnal(t: datetime, mean: float, amp: float, peak_utc: float = 19.0) -> float:
    h = t.hour + t.minute / 60
    return mean + amp * math.cos(2 * math.pi * (h - peak_utc) / 24)


def ensemble_json(start=datetime(2026, 11, 2, tzinfo=UTC), days=4, members=31):
    times = [start + timedelta(hours=i) for i in range(24 * days)]
    means = {2: 54.0, 3: 55.0, 4: 53.5, 5: 52.0}
    hourly = {"time": [int(t.timestamp()) for t in times]}
    for k in range(members):
        off = -2.0 + 4.0 * k / (members - 1)
        key = "temperature_2m" if k == 0 else f"temperature_2m_member{k:02d}"
        hourly[key] = [round(diurnal(t, means[t.day] + off * 0.7, 6.0), 2) for t in times]
    return {"latitude": 40.78, "longitude": -73.97, "hourly": hourly,
            "hourly_units": {"temperature_2m": "°F"}}


def nws_grid():
    def v(day, start_h, dur, c):
        return {"validTime": f"2026-11-{day:02d}T{start_h:02d}:00:00+00:00/PT{dur}H", "value": c}
    return {"properties": {
        "maxTemperature": {"uom": "wmoUnit:degC", "values": [
            v(3, 12, 13, f_to_c(61)), v(4, 12, 13, f_to_c(59)), v(5, 12, 13, f_to_c(58))]},
        "minTemperature": {"uom": "wmoUnit:degC", "values": [
            v(3, 0, 14, f_to_c(48)), v(4, 0, 14, f_to_c(49)), v(4, 23, 14, f_to_c(47))]},
    }}


def mk(ticker, event, strike_type, floor=None, cap=None, label="", yb=None, ya=None, nb=None, na=None,
       close="2026-11-04T05:00:00Z", volume=2000):
    m = {"ticker": ticker, "event_ticker": event, "status": "active", "strike_type": strike_type,
         "yes_sub_title": label, "close_time": close, "volume": volume,
         "response_price_units": "usd_cent"}
    if floor is not None:
        m["floor_strike"] = floor
    if cap is not None:
        m["cap_strike"] = cap
    for k, v in (("yes_bid", yb), ("yes_ask", ya), ("no_bid", nb), ("no_ask", na)):
        if v is not None:
            m[f"{k}_dollars"] = f"{v:.4f}"
            m[k] = round(v * 100)
    return m


def nyc_high_markets():
    e = "KXHIGHNY-26NOV03"
    e2 = "KXHIGHNY-26NOV04"
    return [
        mk(f"{e}-T57", e, "less", cap=57, label="56° or below", yb=.01, ya=.02, nb=.98, na=.99),
        mk(f"{e}-B57.5", e, "between", 57, 58, "57° to 58°", .02, .03, .97, .98),
        mk(f"{e}-B59.5", e, "between", 59, 60, "59° to 60°", .05, .08, .92, .95),
        mk(f"{e}-B61.5", e, "between", 61, 62, "61° to 62°", .70, .74, .26, .30),
        mk(f"{e}-B63.5", e, "between", 63, 64, "63° to 64°", .10, .13, .87, .90),
        mk(f"{e}-T64", e, "greater", floor=64, label="65° or above", yb=.02, ya=.04, nb=.96, na=.98),
        # tomorrow, roughly fairly priced
        mk(f"{e2}-T55", e2, "less", cap=55, label="54° or below", yb=.03, ya=.05, nb=.95, na=.97,
           close="2026-11-05T05:00:00Z"),
        mk(f"{e2}-B55.5", e2, "between", 55, 56, "55° to 56°", .10, .13, .87, .90, close="2026-11-05T05:00:00Z"),
        mk(f"{e2}-B57.5", e2, "between", 57, 58, "57° to 58°", .33, .37, .63, .67, close="2026-11-05T05:00:00Z"),
        mk(f"{e2}-B59.5", e2, "between", 59, 60, "59° to 60°", .36, .40, .60, .64, close="2026-11-05T05:00:00Z"),
        mk(f"{e2}-T60", e2, "greater", floor=60, label="61° or above", yb=.10, ya=.13, nb=.87, na=.90,
           close="2026-11-05T05:00:00Z"),
    ]


def nyc_low_markets():
    e = "KXLOWTNYC-26NOV03"
    return [
        mk(f"{e}-T47", e, "less", cap=47, label="46° or below", yb=.01, ya=.03, nb=.97, na=.99),
        mk(f"{e}-T49", e, "less", cap=49, label="48° or below", yb=.85, ya=.90, nb=.10, na=.15),
        mk(f"{e}-B49.5", e, "between", 49, 50, "49° to 50°", .05, .09, .91, .95),
        mk(f"{e}-T50", e, "greater", floor=50, label="51° or above", yb=.01, ya=.02, nb=.98, na=.99),
    ]


class FakeHttp:
    """Routes URLs to fixture data; records ntfy posts."""

    def __init__(self, now: datetime, settled: dict | None = None, kalshi_down=False, ens_down=False,
                 overrides: dict | None = None):
        self.overrides = overrides or {}   # ticker -> fields to change (e.g. a price that moved)
        self.now = now
        self.posts = []
        self.calls = []
        self.settled = settled or {}
        self.kalshi_down = kalshi_down
        self.ens_down = ens_down

    def get_json(self, url, params=None, headers=None):
        from wxbot.util import HttpError
        self.calls.append((url, params))
        if "kalshi.com" in url:
            if self.kalshi_down:
                raise HttpError(503, url, "down")
            if url.endswith("/series"):
                return {"series": []}
            if re.search(r"/markets/[^/]+/orderbook$", url):
                return {"orderbook": {"yes": [[5, 100], [4, 200]], "no": [[26, 40], [70, 300]]}}
            if m := re.search(r"/markets/([^/?]+)$", url):
                t = m.group(1)
                if t in self.settled:
                    return {"market": {"ticker": t, "status": "finalized", **self.settled[t]}}
                return {"market": {"ticker": t, "status": "active", "result": ""}}
            if url.endswith("/markets"):
                s = params.get("series_ticker")
                if s == "KXHIGHNY":
                    ms = nyc_high_markets()
                    for m in ms:
                        m.update(self.overrides.get(m["ticker"], {}))
                    return {"markets": ms, "cursor": ""}
                if s == "KXLOWTNYC":
                    return {"markets": nyc_low_markets(), "cursor": ""}
                return {"markets": [], "cursor": ""}
        if "api.weather.gov/stations/KNYC/observations" in url:
            return nyc_observations(self.now)
        if "api.weather.gov/points" in url:
            return {"properties": {"forecastGridData": "https://api.weather.gov/gridpoints/OKX/33,37"}}
        if "api.weather.gov/gridpoints" in url:
            return nws_grid()
        if "ensemble-api.open-meteo.com" in url:
            if self.ens_down:
                raise HttpError(503, url, "down")
            return ensemble_json()
        raise HttpError(404, url, "no fixture")

    def post_json(self, url, payload, headers=None):
        self.posts.append(payload)
        return {"id": "x"}
