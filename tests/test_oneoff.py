from datetime import datetime, timezone

from wxbot import trader
from wxbot.strats import oneoff

NOW = datetime(2026, 11, 28, 4, 0, tzinfo=timezone.utc)
MKT = {"ticker": "KXU3-26NOV-T4.2", "event_ticker": "KXU3-26NOV", "status": "active",
       "yes_bid_dollars": "0.13", "yes_ask_dollars": "0.14", "close_time": "2026-12-04T13:29:00Z",
       "title": "U3 above 4.2%", "volume_fp": "60000"}
ORDER = {"ticker": "KXU3-26NOV-T4.2", "side": "no", "price": 0.87, "count": 8,
         "until": "2026-12-03T23:00:00Z"}


class Ctx:
    def __init__(self, rows, params, now=NOW):
        self.orders, self.params, self.now, self.mode, self.http = rows, params, now, "live", None


def _run(monkeypatch, rows, mkt=MKT, now=NOW):
    monkeypatch.setattr(oneoff.kalshi, "get_markets", lambda http, t: {mkt["ticker"]: mkt})
    return oneoff.propose(Ctx(rows, {"orders": [ORDER]}, now))


def test_buys_at_limit_as_taker(monkeypatch):
    out = _run(monkeypatch, [])
    assert len(out) == 1 and out[0].taker and out[0].count == 8 and abs(out[0].price - 0.87) < 1e-9


def test_no_chasing_above_limit(monkeypatch):
    assert _run(monkeypatch, [], {**MKT, "yes_bid_dollars": "0.11"}) == []


def test_counts_its_own_fills_only(monkeypatch):
    rows = [{"strategy": "fade_leads", "mode": "live", "ticker": ORDER["ticker"], "side": "no", "filled": 2},
            {"strategy": "oneoff", "mode": "live", "ticker": ORDER["ticker"], "side": "no", "filled": 5}]
    assert _run(monkeypatch, rows)[0].count == 3
    rows[1]["filled"] = 8
    assert _run(monkeypatch, rows) == []


def test_expires(monkeypatch):
    assert _run(monkeypatch, [], now=datetime(2026, 12, 4, tzinfo=timezone.utc)) == []


def test_vet_allow_duplicate_ignores_other_strategies():
    it = trader.Intent("oneoff", ORDER["ticker"], "KXU3-26NOV", "no", 0.87, 8,
                       close="2026-12-04T13:29:00Z", taker=True)
    rows = [{"strategy": "fade_leads", "mode": "live", "ticker": ORDER["ticker"], "event": "KXU3-26NOV",
             "side": "no", "price": "0.86", "count": 2, "filled": 2, "status": "filled"}]
    t = {"max_contracts_per_order": 10, "max_open_dollars": 45, "max_event_dollars": 8.98}
    sp = {"taker": True, "allow_duplicate": True, "min_price": 0.8, "max_price": 0.87, "max_contracts": 8}
    assert trader.vet(it, rows, t, sp, NOW, []) is None
    assert trader.vet(it, rows, t, {k: v for k, v in sp.items() if k != "allow_duplicate"}, NOW, []) == "duplicate"
    assert trader.vet(it, rows, {**t, "max_event_dollars": 8.0}, sp, NOW, []) == "event open-$ cap"
