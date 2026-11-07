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


# ---- live-readings exit (shadow only)

OBS_PARAMS = {"series": {"KXHIGHTMIN": {"weights": {"gfs_seamless": 1.0}, "bias": {"gfs_seamless": 0.0},
                                        "sd": 1.5}},
              "exit_obs": {"hours_before_close": [18, 6], "window_hours": 0.25, "margin": 0.08,
                           "blend": {"18": [0, 1, 0.3], "6": [0, 0.5, 1.0]}}}


class Both(Meteo):
    """Open-Meteo forecast plus NWS station readings (METAR with a 0.1 C T-group)."""
    def __init__(self, high, read_c):
        super().__init__(high)
        self.read_c = read_c

    def get_json(self, url, params=None, headers=None):
        if "/observations" not in url:
            return super().get_json(url, params, headers)
        start = datetime(2026, 11, 7, 6, 0, tzinfo=UTC)
        feats = []
        for i in range(19):
            c = self.read_c if i == 12 else self.read_c - 3
            t = f"T{'1' if c < 0 else '0'}{abs(round(c * 10)):03d}0000"
            feats.append({"properties": {"timestamp": (start + timedelta(hours=i)).isoformat(),
                                         "temperature": {"value": c}, "rawMessage": f"KMSP 0000Z RMK {t}"}})
        return {"features": feats}


def test_obs_shadow_logs_once_and_never_trades():
    import csv
    setup_order()
    wx._FC.clear(); wx._OBS.clear()
    row = {"id": f"{TK}|yes|1", "ticker": TK, "side": "yes", "mode": "live", "price": "0.09"}
    m = market(0.40, 0.44)
    t6 = CLOSE - timedelta(hours=6)
    assert wx.obs_value(Both(46, 12.0), row, m, CLOSE - timedelta(hours=10), OBS_PARAMS) is None
    # readings already hit 12.0 C = 53.6 F, so "49 or below" is dead -> value ~0, would sell at 40c
    H, v, p, n, top = wx.obs_value(Both(46, 12.0), row, m, t6, OBS_PARAMS)
    assert H == 6 and n >= 15 and abs(top - 53.6) < 0.1 and v < 0.2
    wx._FC.clear(); wx._OBS.clear()
    cold = wx.obs_value(Both(46, 7.0), row, m, t6, OBS_PARAMS)[1]     # max 44.6 F, cool rest of day
    assert cold > 0.5
    wx._FC.clear(); wx._OBS.clear()
    assert wx.shadow(Both(46, 12.0), row, m, t6, OBS_PARAMS) is True
    assert wx.shadow(Both(46, 12.0), row, m, t6 + timedelta(minutes=5), OBS_PARAMS) is False   # once per H
    with open(wx.SHADOW_CSV, newline="") as fh:
        got = list(csv.DictReader(fh))
    assert len(got) == 1 and got[0]["would_sell"] == "yes" and got[0]["hours_left"] == "6"


def test_value_uses_readings_only_when_switched_on():
    setup_order()
    row = {"id": f"{TK}|yes|1", "ticker": TK, "side": "yes", "mode": "live", "price": "0.09"}
    m = market(0.40, 0.44)
    t6 = CLOSE - timedelta(hours=6)
    P = dict(OBS_PARAMS, exit_obs=dict(OBS_PARAMS["exit_obs"], shadow=True))
    wx._FC.clear(); wx._OBS.clear()
    assert wx.value(Both(46, 12.0), row, m, t6, P) is None                 # shadow: no 6h value
    P["exit_obs"]["shadow"] = False
    wx._FC.clear(); wx._OBS.clear()
    v = wx.value(Both(46, 12.0), row, m, t6, P)
    assert v is not None and v < 0.2
    assert trader.model_exit(row, m, v, 0.08)                               # 40c bid beats a dead bracket
