from datetime import date, datetime, timedelta, timezone

import pytest

from wxbot import daybefore as db
from wxbot.stations import SeriesInfo, Station, cli_window
from wxbot.util import HttpError

UTC = timezone.utc
NY = Station("KNYC", "Central Park", 40.78, -73.97, "America/New_York")
SI = SeriesInfo("KXHIGHNY", "high", NY, "NYC")
DAY = date(2026, 11, 6)
CLOSE = datetime(2026, 11, 7, 5, 0, tzinfo=UTC)
PARAMS = {"hours_before_close": 36, "threshold": 0.06, "blend": [0.0, 1.0, 0.0],
          "series": {"KXHIGHNY": {"weights": {"gfs_seamless": 1.0, "ecmwf_ifs025": 1.0},
                                  "bias": {"gfs_seamless": 1.0, "ecmwf_ifs025": -1.0}, "sd": 1.5}}}


def mk(tk, kind, lo=None, hi=None, bid=0.10, ask=0.14):
    m = {"ticker": f"KXHIGHNY-26NOV06-{tk}", "event_ticker": "KXHIGHNY-26NOV06",
         "strike_type": kind, "close_time": CLOSE.isoformat().replace("+00:00", "Z"),
         "yes_bid_dollars": f"{bid:.2f}", "yes_ask_dollars": f"{ask:.2f}"}
    if kind == "between":
        m.update(floor_strike=lo, cap_strike=hi)
    elif kind == "greater":
        m["floor_strike"] = lo
    else:
        m["cap_strike"] = hi
    return m


def hourly(gfs_peak=60.0, ec_peak=62.0):
    start, _ = cli_window(DAY, NY.tz)
    times = [(start + timedelta(hours=i - 24)).strftime("%Y-%m-%dT%H:00") for i in range(72)]
    return {"time": times,
            "gfs_seamless": [gfs_peak - abs(i - 42) * 0.2 for i in range(72)],
            "ecmwf_ifs025": [ec_peak - abs(i - 42) * 0.2 for i in range(72)]}


def test_forecast_uses_climate_day_and_bias():
    mu, sd = db.forecast(PARAMS["series"]["KXHIGHNY"], hourly(), DAY, NY.tz, "high")
    assert mu == pytest.approx(61.0)          # (60+1 + 62-1) / 2
    assert sd == 1.5


def test_plan_orders_rests_inside_spread_only_with_edge():
    # blend = market only -> no edge anywhere
    ms = [mk("B60.5", "between", 60, 61, bid=0.30, ask=0.36)]
    assert db.plan_orders(ms, 61.0, 1.5, [0.0, 1.0, 0.0], 0.06) == []
    # blend = model only: 60-61 is ~ 45% likely vs ~33c -> buy YES at 31c
    out = db.plan_orders(ms, 61.0, 1.5, [0.0, 0.0, 1.0], 0.06)
    assert [(o["side"], o["price"]) for o in out] == [("yes", 0.31)]
    # a far bracket the model rules out -> buy NO at 1 - (ask - 1c)
    ms = [mk("T70", "greater", lo=70, bid=0.20, ask=0.25)]
    out = db.plan_orders(ms, 61.0, 1.5, [0.0, 0.0, 1.0], 0.06)
    assert [(o["side"], o["price"]) for o in out] == [("no", 0.76)]


def test_fill_flags():
    assert db.fill_flags("yes", 0.31, [0.35, 0.31]) == (True, False)
    assert db.fill_flags("yes", 0.31, [0.30]) == (True, True)
    assert db.fill_flags("yes", 0.31, [0.33]) == (False, False)
    # NO at 76c = offering YES at 24c
    assert db.fill_flags("no", 0.76, [0.24]) == (True, False)
    assert db.fill_flags("no", 0.76, [0.26, 0.20]) == (True, True)
    assert db.fill_flags("no", 0.76, [0.22]) == (False, False)


class Http:
    def __init__(self, result="", trades=()):
        self.result, self.trades, self.calls = result, list(trades), []

    def get_json(self, url, params=None, headers=None):
        self.calls.append(url)
        if "open-meteo" in url:
            return {"hourly": {"time": hourly()["time"],
                               **{f"temperature_2m_{k}": v for k, v in hourly().items() if k != "time"}}}
        if url.endswith("/markets/trades"):
            return {"trades": [{"yes_price_dollars": f"{p:.2f}"} for p in self.trades], "cursor": ""}
        if "/markets/" in url:
            return {"market": {"close_time": CLOSE.isoformat(), "result": self.result}}
        raise HttpError(404, url)


def test_place_once_at_36h_then_fill_and_grade():
    ms = [mk("B60.5", "between", 60, 61, bid=0.10, ask=0.14),
          mk("T70", "greater", lo=70, bid=0.20, ask=0.25)]
    params = dict(PARAMS, blend=[0.0, 0.0, 1.0])
    orders, state = [], {}
    early = CLOSE - timedelta(hours=40)
    assert db.place(Http(), orders, {"KXHIGHNY-26NOV06": (SI, ms)}, early, state, params) == 0
    now = CLOSE - timedelta(hours=36)
    n = db.place(Http(), orders, {"KXHIGHNY-26NOV06": (SI, ms)}, now, state, params)
    assert n == 2 and {o["side"] for o in orders} == {"yes", "no"}
    # second scan in the window places nothing new
    assert db.place(Http(), orders, {"KXHIGHNY-26NOV06": (SI, ms)}, now + timedelta(minutes=5),
                    state, params) == 0
    # before close nothing is graded
    assert db.settle(Http("yes"), orders, CLOSE - timedelta(hours=1)) == 0
    # after close: YES 60-61 at 11c traded through (10c print), result no -> -11c;
    # NO >70 at 76c never filled (no YES print at 24c+)
    assert db.settle(Http("no", trades=[0.10, 0.15]), orders, CLOSE + timedelta(hours=8)) == 2
    yes = next(o for o in orders if o["side"] == "yes")
    no = next(o for o in orders if o["side"] == "no")
    assert yes["status"] == "filled" and yes["through"] == 1 and yes["pnl"] == "-0.11"
    assert no["status"] == "unfilled" and no["pnl"] == ""
    db.save_orders(orders)
    text = "\n".join(db.report_section(db.load_orders()))
    assert "filled 1 (50%)" in text and "$-0.11" in text
