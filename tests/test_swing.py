from datetime import datetime, timezone

from wxbot import swing
from wxbot.kalshi_api import OrderRejected, check_swing_order
from wxbot.util import Config

import pytest

NOW = datetime(2026, 11, 16, 20, 30, tzinfo=timezone.utc)      # Mon 3:30 PM ET
CLOSE_BTC = "2026-11-16T22:00:00Z"                              # 5 PM ET


def mk(t, floor, ask, close=CLOSE_BTC, st="greater"):
    return {"ticker": t, "event_ticker": "KXBTCD-26NOV1617", "strike_type": st, "floor_strike": floor,
            "close_time": close, "yes_ask_dollars": f"{ask:.4f}", "yes_sub_title": f"${floor} or above"}


MARKETS = [mk("A", 65000, 0.52), mk("B", 66000, 0.31), mk("C", 67000, 0.18),
           mk("D", 66000, 0.30, close="2026-11-16T21:00:00Z")]


class Http:
    def __init__(self, markets=MARKETS, result=None):
        self.markets, self.result = markets, result

    def get_json(self, url, params=None, headers=None):
        if url.endswith("/markets"):
            return {"markets": self.markets}
        t = url.rsplit("/", 1)[-1]
        return {"market": {"ticker": t, "result": self.result}}

    def post_json(self, *a, **k):
        return {}


class Client:
    def __init__(self, cash=5.65, fill=True):
        self.cash, self.fill, self.orders = cash, fill, []

    def cash_on(self, shard=0):
        return self.cash if shard == 2 else 0.0

    def buy_now(self, ticker, side, price, count, cid, swing=False):
        assert swing
        self.orders.append((ticker, side, price, count))
        n = count if self.fill else 0
        return {"fill_count_fp": str(n), "taker_fill_cost_dollars": f"{n * 0.31:.2f}",
                "taker_fees_dollars": "0.27"}


def cfg(**kw):
    raw = {"swing": {"enabled": True, "target": 500.0, "end": "2026-11-20T22:30:00Z",
                     "crypto": {"series": "KXBTCD", "close_hour_et": 17, "shard": 2,
                                "days": [0, 1, 2, 3, 4, 5, 6]}, **kw}}
    return Config(raw=raw)


@pytest.fixture(autouse=True)
def total(monkeypatch):
    monkeypatch.setattr(swing, "_account_total", lambda c: 57.98)


def test_pick_nearest_aim_and_close():
    p = swing.params(cfg())
    close = swing.close_time(NOW.astimezone(swing.ET).date(), 17)
    m = swing.pick(MARKETS, close, p)
    assert m["ticker"] == "B" and m["_ask"] == 0.31
    assert swing.pick([mk("E", 1, 0.30, st="greater_or_equal", close="2026-11-16T21:00:00Z")],
                      swing.close_time(close.date(), 16), p)["ticker"] == "E"


def test_stake_bold_play():
    assert swing.stake_for(26.0, 58.0, 500.0, 0.30) == 26.0          # all in: not enough to overshoot
    assert swing.stake_for(300.0, 400.0, 500.0, 0.30) == pytest.approx(42.86, abs=0.01)
    assert swing.stake_for(10.0, 510.0, 500.0, 0.30) == 0.0
    assert swing.contracts_for(5.65, 0.33) == 16


def test_run_places_one_bet_and_pushes_once():
    c = Client()
    out = swing.run(cfg(), now=NOW, http=Http(), client=c)
    assert len(c.orders) == 1 and c.orders[0][0] == "B" and c.orders[0][2] == 0.33
    assert "bought" in out[0]
    st = swing.load()
    assert st["bets"][0]["filled_up"] and st["bets"][0]["count"] == c.orders[0][3]
    swing.run(cfg(), now=NOW, http=Http(), client=c)                    # no second bet same event
    assert len(c.orders) == 1


def test_settle_and_target_stop():
    c = Client()
    swing.run(cfg(), now=NOW, http=Http(), client=c)
    later = datetime(2026, 11, 16, 22, 10, tzinfo=timezone.utc)
    out = swing.run(cfg(), now=later, http=Http(result="yes"), client=c)
    b = swing.load()["bets"][0]
    assert b["status"] == "won" and b["pnl"] > 0 and any("WON" in x for x in out)


def test_target_reached_ends_swing(monkeypatch):
    monkeypatch.setattr(swing, "_account_total", lambda c: 512.0)
    c = Client()
    swing.run(cfg(), now=NOW, http=Http(), client=c)
    assert not c.orders and swing.load()["done"]
    assert not swing.active(cfg(), NOW)
    assert not swing.pause_others(cfg(), NOW)


def test_outside_window_and_after_end():
    c = Client()
    early = datetime(2026, 11, 16, 14, 0, tzinfo=timezone.utc)          # 9 AM ET
    swing.run(cfg(), now=early, http=Http(), client=c)
    assert not c.orders
    assert not swing.active(cfg(), datetime(2026, 11, 21, tzinfo=timezone.utc))


def test_no_fill_retries_then_drops():
    c = Client(fill=False)
    for _ in range(5):
        swing.run(cfg(), now=NOW, http=Http(), client=c)
    assert len(c.orders) == 4 and swing.load()["bets"][0]["status"] == "nofill"


def test_swing_order_ceiling():
    check_swing_order("yes", 0.30, 1500)
    with pytest.raises(OrderRejected):
        check_swing_order("yes", 0.40, 1600)          # $640 > $600


def test_disabled_does_nothing():
    c = Client()
    assert swing.run(Config(raw={"swing": {"enabled": False}}), now=NOW, http=Http(), client=c) == []
    assert not c.orders


def test_pause_others_while_active():
    assert swing.pause_others(cfg(pause_others=True), NOW)
    assert not swing.pause_others(cfg(pause_others=False), NOW)
