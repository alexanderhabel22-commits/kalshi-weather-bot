from datetime import datetime, timezone

from wxbot import rain
from wxbot.util import HttpError

UTC = timezone.utc

CLI_PRELIM = """
000
CDUS41 KOKX 032130
CLINYC

CLIMATE REPORT
NATIONAL WEATHER SERVICE NEW YORK, NY
430 PM EST TUE NOV 3 2026

...................................

...THE CENTRAL PARK NY CLIMATE SUMMARY FOR NOVEMBER 3 2026...
VALID TODAY AS OF 0400 PM LOCAL TIME.

CLIMATE NORMAL PERIOD 1991 TO 2020

WEATHER ITEM   OBSERVED TIME   RECORD YEAR NORMAL DEPARTURE LAST
                VALUE   (LST)  VALUE       VALUE  FROM      YEAR
                                                  NORMAL
...................................................................
TEMPERATURE (F)
 TODAY
  MAXIMUM         61    225 PM  78    1990  59      2       55
  MINIMUM         48    705 AM  30    1951  45      3       44

PRECIPITATION (IN)
  TODAY            0.42          2.05 1977   0.12   0.30     0.00
  MONTH TO DATE    2.95                      0.38   2.57     0.11
  SINCE SEP 1      9.21                      8.40   0.81     7.02
  SINCE JAN 1     38.21                     41.03  -2.82    46.11

SNOWFALL (IN)
  TODAY            0.0           0.5  1953   0.0    0.0      0.0
  MONTH TO DATE    0.0                       0.0    0.0      0.0
"""

CLI_FINAL_OCT = CLI_PRELIM.replace("FOR NOVEMBER 3 2026", "FOR OCTOBER 31 2026").replace(
    "VALID TODAY AS OF 0400 PM LOCAL TIME.\n", "").replace("MONTH TO DATE    2.95", "MONTH TO DATE    T   ")


def test_parse_cli_prelim_and_trace():
    c = rain.parse_cli(CLI_PRELIM)
    assert (c["year"], c["month"], c["day"], c["mtd"], c["as_of"]) == (2026, 11, 3, 2.95, (16, 0))
    f = rain.parse_cli(CLI_FINAL_OCT)
    assert f["month"] == 10 and f["mtd"] == 0.0 and f["as_of"] is None
    assert rain.parse_cli("nothing here") is None


def test_cli_coverage_uses_standard_time():
    c = rain.parse_cli(CLI_PRELIM)
    assert rain.cli_covers_until(c, "America/New_York") == datetime(2026, 11, 3, 21, 0, tzinfo=UTC)


def test_cli_code_mapping():
    assert rain.cli_code_for("KXRAINNYCM") == "NYC"
    assert rain.cli_code_for("KXRAINNOLAM") == "MSY"
    assert rain.cli_code_for("KXRAINDAUS") is None        # daily series: not covered
    assert rain.cli_code_for("KXRAINHOLIDAY") is None


def obs(t, mm, unit="wmoUnit:mm"):
    return {"properties": {"timestamp": t.isoformat(),
                           "precipitationLastHour": {"value": mm, "unitCode": unit}}}


def test_hourly_rain_skips_specials_and_old_reports():
    since = datetime(2026, 11, 3, 21, 0, tzinfo=UTC)
    js = {"features": [
        obs(datetime(2026, 11, 3, 20, 51, tzinfo=UTC), 5.0),     # before the report: ignored
        obs(datetime(2026, 11, 3, 21, 51, tzinfo=UTC), 2.54),    # 0.10 in
        obs(datetime(2026, 11, 3, 22, 10, tzinfo=UTC), 9.0),     # special report: ignored
        obs(datetime(2026, 11, 3, 22, 51, tzinfo=UTC), 1.27),    # 0.05 in
        obs(datetime(2026, 11, 3, 23, 51, tzinfo=UTC), None),
    ]}
    assert rain.hourly_rain_since(js, since) == 0.15


def market(thr, ask, close="2026-12-01T04:59:00Z"):
    return {"ticker": f"KXRAINNYCM-26NOV-{thr}", "event_ticker": "KXRAINNYCM-26NOV",
            "floor_strike": thr, "strike_type": "greater", "close_time": close,
            "yes_ask_dollars": f"{ask:.4f}", "yes_bid_dollars": f"{ask - 0.02:.4f}"}


class RainHttp:
    def __init__(self, cli_text, rain_mm, asks):
        self.cli_text, self.rain_mm, self.asks = cli_text, rain_mm, asks
        self.calls = []

    def get_json(self, url, params=None, headers=None):
        self.calls.append(url)
        if url.endswith("/series"):
            return {"series": []}
        if url.endswith("/markets"):
            if params.get("series_ticker") == "KXRAINNYCM":
                return {"markets": [market(t, a) for t, a in self.asks.items()], "cursor": ""}
            return {"markets": [], "cursor": ""}
        if url.endswith("/orderbook"):
            return {"orderbook": {"yes": [[2, 50]], "no": [[10, 40], [8, 60]]}}
        if "/products/types/CLI/locations/NYC" in url:
            return {"@graph": [{"@id": "https://api.weather.gov/products/abc"}]}
        if url.endswith("/products/abc"):
            return {"productText": self.cli_text}
        if "/stations/KNYC/observations" in url:
            return {"features": [obs(datetime(2026, 11, 3, 21, 51, tzinfo=UTC), self.rain_mm)]}
        raise HttpError(404, url, "no fixture")


def test_census_scan_levels_and_cheapest_ask():
    census, state = {}, {}
    now = datetime(2026, 11, 3, 22, 30, tzinfo=UTC)
    # official 2.95 in; +0.10 in of rain since the 4 PM report -> provisional 3.05 in
    http = RainHttp(CLI_PRELIM, 2.54, {1: 0.99, 2: 0.99, 3: 0.80, 4: 0.20})
    assert rain.census_scan(http, state, census, now) == 5
    assert set(census) == {"KXRAINNYCM-26NOV-1", "KXRAINNYCM-26NOV-2", "KXRAINNYCM-26NOV-3"}
    three = census["KXRAINNYCM-26NOV-3"]
    assert three["provisional_ask"] == 0.8 and "official_at" not in three
    assert three["provisional_total"] == 3.05 and three["provisional_depth"] == 100
    assert census["KXRAINNYCM-26NOV-1"]["official_ask"] == 0.99

    # later scan: cheaper ask is remembered, nothing re-counted
    http.asks[3] = 0.75
    assert rain.census_scan(http, state, census, now) == 0
    assert census["KXRAINNYCM-26NOV-3"]["min_ask"] == 0.75

    text = "\n".join(rain.report_section(census))
    assert "KXRAINNYCM-26NOV-3" in text and "1 of 3" in text


def test_new_month_starts_at_zero():
    census = {}
    now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    http = RainHttp(CLI_FINAL_OCT, 30.0, {1: 0.40})      # 1.18 in since Oct 31's final report
    http_obs_time = datetime(2026, 11, 1, 11, 51, tzinfo=UTC)
    orig = http.get_json

    def get_json(url, params=None, headers=None):
        if "/observations" in url:
            return {"features": [obs(http_obs_time, 30.0)]}
        return orig(url, params, headers)
    http.get_json = get_json
    assert rain.census_scan(http, {}, census, now) == 1
    assert census["KXRAINNYCM-26NOV-1"]["provisional_total"] == 1.18


def test_save_keeps_recent_months(tmp_path):
    census = {f"X-{m}": {"month": f"2026-{m:02d}", "min_ask": 1.0} for m in range(1, 8)}
    rain.save(census)
    assert sorted(v["month"] for v in rain.load().values()) == ["2026-04", "2026-05", "2026-06", "2026-07"]


def test_discovered_city_codes():
    assert rain.cli_code_for("KXRAINCHIM") == "ORD"
    assert rain.cli_code_for("KXRAINMINM") == "MSP"
    assert rain.cli_code_for("KXRAINMIAM") == "MIA"
