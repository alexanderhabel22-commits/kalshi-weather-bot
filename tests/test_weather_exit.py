from datetime import datetime, timedelta, timezone

from wxbot import trader
from wxbot.daybefore import save_orders
from wxbot.strats import weather_daybefore as wx

UTC = timezone.utc
TK = "KXHIGHTMIN-26NOV07-T50"
CLOSE = datetime(2026, 11, 8, 7, 0, tzinfo=UTC)
PARAMS = {"exit": {"hours_before_close": 12, "window_hours": 0.75, "margin": 0.08,
                   "blend": [0.37, 0.78, 0.652],
                   "series": {"KXHIGHTMIN": {"weights": {"gfs_seamless": 1.0},
                                             "bias": {"gfs_seamless": 0.0}, "sd": 1.5}}}}


def market(yb, ya):
    return {"ticker": TK, "event_ticker": "KXHIGHTMIN-26NOV07", "close_time": "2026-11-08T07:00:00Z",
            "yes_bid_dollars": f"{yb:.2f}", "yes_ask_dollars": f"{ya:.2f}",
            "strike_type": "less", "cap_strike": 50, "subtitle": "49° or below"}


class Meteo:
    def __init__(self, high):
        self.high, self.calls = high, 0

    def get_json(self, url, params=None, headers=None):
        self.calls += 1
        start = datetime(2026, 11, 7, 6, 0)          # CLI day starts 06Z for Chicago
        times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(30)]
        vals = [self.high - 8 + (8 if i == 15 else 0) for i in range(30)]
        return {"hourly": {"time": times, "temperature_2m_gfs_seamless": vals}}


def setup_order():
    save_orders([{"id": f"{TK}|yes", "ticker": TK, "event": "KXHIGHTMIN-26NOV07", "series": "KXHIGHTMIN",
                  "city": "Minneapolis", "metric": "high", "date": "2026-11-07", "bracket": "49° or below",
                  "side": "yes", "price": "0.09", "status": "open"}])


def test_value_only_in_window_and_tracks_forecast():
    setup_order()
    wx._FC.clear()
    row = {"ticker": TK, "side": "yes"}
    m = market(0.20, 0.24)
    assert wx.value(Meteo(46), row, m, CLOSE - timedelta(hours=20), PARAMS) is None   # too early
    cold = wx.value(Meteo(46), row, m, CLOSE - timedelta(hours=12), PARAMS)
    wx._FC.clear()
    warm = wx.value(Meteo(55), row, m, CLOSE - timedelta(hours=12), PARAMS)
    assert cold is not None and warm is not None and cold > 0.5 > warm
    wx._FC.clear()
    no = wx.value(Meteo(46), dict(row, side="no"), m, CLOSE - timedelta(hours=12), PARAMS)
    assert abs(no + cold - 1) < 1e-9


def test_model_exit_needs_a_real_margin():
    r = {"side": "yes"}
    assert trader.model_exit(r, market(0.51, 0.53), 0.50, 0.08) is None       # Alex: 51c vs 50% -> hold
    assert trader.model_exit(r, market(0.57, 0.59), 0.50, 0.08) is None       # 57 - 2c fee < 58
    sell = trader.model_exit(r, market(0.60, 0.62), 0.50, 0.08)               # 60 - 2c fee = 58
    assert sell and sell[0] == 0.60
    assert trader.model_exit({"side": "no"}, market(0.30, 0.38), 0.50, 0.08)  # NO bid 62c
