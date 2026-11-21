import math
from datetime import datetime, timezone
from types import SimpleNamespace

from wxbot.strats import fx_paper as fx

UTC = timezone.utc
NOW = datetime(2026, 11, 18, 15, 0, tzinfo=UTC)          # Wed
P = dict(fx.DEFAULTS)
SIG = 0.0012                                              # hourly vol ~0.12%


def mkt(series="KXUSDMXNAW", k=18.5, bid=0.40, ask=0.42, st="greater", close="2026-11-20T22:00:00Z"):
    m = {"ticker": f"{series}-26NOV20-T{k}", "event_ticker": f"{series}-26NOV20", "strike_type": st,
         "yes_bid_dollars": f"{bid:.4f}", "yes_ask_dollars": f"{ask:.4f}", "close_time": close,
         "volume_fp": "500"}
    m["floor_strike" if st == "greater" else "cap_strike"] = k
    return m


def test_helpers():
    assert fx.pair_of("KXUSDMXNAW") == "USDMXN" and fx.pair_of("KXGBPUSDEVENT") == "GBPUSD"
    assert fx.pair_of("KXUSDMXN15M") is None and fx.pair_of("KXBTCD") is None
    assert fx.kind_of("KXEURUSDAD-26NOV20-T1.1") == "AD"
    assert abs(fx.phi(0) - 0.5) < 1e-12 and abs(fx.phi(1.96) - 0.975) < 1e-3
    # Wed 15:00 -> Fri 22:00 UTC = 55 h, all weekdays
    assert fx.weekday_hours(NOW, datetime(2026, 11, 20, 22, 0, tzinfo=UTC)) == 55
    # Fri 22:00 -> Mon 22:00 = 72 h of which the weekend is skipped (about 24 h left)
    h = fx.weekday_hours(datetime(2026, 11, 20, 22, 0, tzinfo=UTC), datetime(2026, 11, 23, 22, 0, tzinfo=UTC))
    assert 20 <= h <= 28
    assert fx.weekday_hours(NOW, NOW) == 1.0
    closes = [100 * math.exp(0.001 * ((-1) ** i)) for i in range(600)]
    s = fx.sigma(closes, 528)
    assert s is not None and abs(s - 0.002) < 1e-4
    assert fx.sigma([1.0, 1.0], 528) is None


def test_buys_yes_when_cheap_and_no_when_rich():
    tau = 55
    # spot well above strike -> P(above) high; ask 42c -> buy YES
    out = fx.candidates([mkt(k=18.5, bid=0.40, ask=0.42)], NOW, {"USDMXN": (18.7, SIG)}, P)
    assert len(out) == 1 and out[0].side == "yes" and out[0].price == 0.42 and out[0].taker
    assert out[0].count == 1 and out[0].strategy == "fx_paper"
    pr = fx.p_above(18.7, 18.5, SIG, tau)
    assert pr > 0.85 and f"{pr:.3f}" in out[0].note
    # spot well below -> buy NO at 1 - bid
    out = fx.candidates([mkt(k=18.5, bid=0.40, ask=0.42)], NOW, {"USDMXN": (18.3, SIG)}, P)
    assert len(out) == 1 and out[0].side == "no" and out[0].price == 0.60


def test_less_markets_flip_probability():
    out = fx.candidates([mkt(k=18.5, bid=0.40, ask=0.42, st="less")], NOW, {"USDMXN": (18.3, SIG)}, P)
    assert len(out) == 1 and out[0].side == "yes"


def test_skips_fair_price_no_spot_held_closed_band():
    pr = fx.p_above(18.5, 18.5, SIG, 55)                  # 0.5
    a = round(pr, 2)
    assert fx.candidates([mkt(bid=a - 0.02, ask=a + 0.02)], NOW, {"USDMXN": (18.5, SIG)}, P) == []
    assert fx.candidates([mkt()], NOW, {"USDJPY": (150.0, SIG)}, P) == []           # no spot for pair
    m = mkt(k=18.5)
    held = {m["event_ticker"]: {m["ticker"]}}
    assert fx.candidates([m], NOW, {"USDMXN": (18.7, SIG)}, P, held) == []
    assert fx.candidates([mkt(close="2026-11-18T14:00:00Z")], NOW, {"USDMXN": (18.7, SIG)}, P) == []
    # YES ask 99c is outside the band and NO is worthless -> nothing
    assert fx.candidates([mkt(k=18.5, bid=0.98, ask=0.99)], NOW, {"USDMXN": (18.7, SIG)}, P) == []
    assert fx.candidates([mkt(st="between")], NOW, {"USDMXN": (18.7, SIG)}, P) == []


class FakeHttp:
    def __init__(self, t):
        self.t, self.calls = t, []

    def get_json(self, url, params=None, headers=None):
        self.calls.append(url)
        closes = [18.0 * math.exp(0.001 * ((-1) ** i)) for i in range(600)]
        return {"chart": {"result": [{"meta": {"regularMarketPrice": 18.4, "regularMarketTime": self.t},
                                      "indicators": {"quote": [{"close": closes}]}}]}}


def test_fx_inputs_requires_fresh_spot():
    ctx = SimpleNamespace(now=NOW, http=FakeHttp(int(NOW.timestamp()) - 300), state={})
    got = fx.fx_inputs(ctx, {"USDMXN"}, P)
    assert got["USDMXN"][0] == 18.4 and abs(got["USDMXN"][1] - 0.002) < 1e-4
    ctx = SimpleNamespace(now=NOW, http=FakeHttp(int(NOW.timestamp()) - 3 * 3600), state={})
    assert fx.fx_inputs(ctx, {"USDMXN"}, P) == {}                                  # FX shut / stale
