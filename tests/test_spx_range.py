from datetime import datetime, timezone

from wxbot import trader
from wxbot.strats import spx_range as sx

UTC = timezone.utc
CLOSE = "2026-11-13T21:00:00Z"            # Fri Nov 13 4 PM ET (EST)
EVENING = datetime(2026, 11, 12, 23, 0, tzinfo=UTC)   # Thu 6 PM ET: 22 h before the close
P = dict(sx.DEFAULTS)


def mk(strike, yb, ya, kind="between", close=CLOSE):
    return {"ticker": f"KXINX-26NOV13H1600-B{strike}", "event_ticker": "KXINX-26NOV13H1600",
            "strike_type": kind, "close_time": close, "yes_bid_dollars": f"{yb:.4f}",
            "yes_ask_dollars": f"{ya:.4f}", "volume_fp": "500"}


BOOK = [mk(7587, 0.10, 0.13), mk(7612, 0.14, 0.17), mk(7637, 0.15, 0.19), mk(7662, 0.12, 0.15),
        mk(7687, 0.08, 0.10), mk(7712, 0.05, 0.07), mk(7737, 0.02, 0.04), mk(7562, 0.06, 0.08),
        mk(7999, 0.0, 0.01, kind="greater")]


def test_rests_no_bids_at_65_on_the_six_likeliest_ranges():
    out = sx.candidates(BOOK, P, EVENING)
    assert len(out) == 6
    assert {i.ticker.rsplit("B", 1)[-1] for i in out} == {"7587", "7612", "7637", "7662", "7687", "7562"}
    assert all(i.side == "no" and i.price == 0.65 and not i.taker and i.count == 1 for i in out)


def test_takes_a_yes_bid_at_or_above_35c():
    out = sx.candidates([mk(7637, 0.41, 0.45)], P, EVENING)
    assert len(out) == 1 and out[0].taker and out[0].price == 0.59


def test_only_before_the_open_and_not_held():
    # 8:40 AM ET on the day (7.3 h before the close): too late to place
    assert sx.candidates(BOOK, P, datetime(2026, 11, 13, 13, 40, tzinfo=UTC)) == []
    # two days out: not yet
    assert sx.candidates(BOOK, P, datetime(2026, 11, 11, 23, 0, tzinfo=UTC)) == []
    held = {"KXINX-26NOV13H1600": {b["ticker"] for b in BOOK[:3]}}
    assert len(sx.candidates(BOOK, P, EVENING, held)) == 3


def test_cancel_due_before_the_open():
    r = {"close": CLOSE}
    assert not sx.cancel_due(r, datetime(2026, 11, 13, 13, 40, tzinfo=UTC))   # 8:40 AM ET
    assert sx.cancel_due(r, datetime(2026, 11, 13, 13, 46, tzinfo=UTC))       # 8:46 AM ET
    # summer (EDT): close 20:00Z, cancel by 8:45 AM EDT = 12:45Z
    assert sx.cancel_due({"close": "2026-07-15T20:00:00Z"}, datetime(2026, 7, 15, 12, 46, tzinfo=UTC))


class FakeClient:
    def __init__(self):
        self.cancelled = []

    def cancel(self, oid, ticker=""):
        self.cancelled.append(oid)


def test_expire_resting_cancels_live_and_closes_paper():
    now = datetime(2026, 11, 13, 13, 50, tzinfo=UTC)
    rows = [
        {"strategy": "spx_range", "mode": "live", "status": "resting", "order_id": "A", "close": CLOSE, "filled": "0"},
        {"strategy": "spx_range", "mode": "paper", "status": "resting", "close": CLOSE, "filled": "0"},
        {"strategy": "spx_range", "mode": "live", "status": "filled", "order_id": "B", "close": CLOSE, "filled": "1"},
        {"strategy": "macro_fade", "mode": "live", "status": "resting", "order_id": "C", "close": CLOSE, "filled": "0"},
        {"strategy": "spx_range", "mode": "live", "status": "resting", "order_id": "D",
         "close": "2026-11-16T21:00:00Z", "filled": "0"},
    ]
    c = FakeClient()
    assert trader.expire_resting(c, rows, now, dry_run=False) == 2
    assert c.cancelled == ["A"]
    assert rows[0]["status"] == "resting"        # sync_live books it from the order's status
    assert rows[1]["status"] == "cancelled"
    assert rows[3]["status"] == "resting" and rows[4]["status"] == "resting"


def test_config_is_one_contract_live():
    from wxbot.util import load_config
    c = load_config()
    sp = c.raw["trading"]["spx_range"]
    assert sp["live"] is True and sp["contracts"] == sp["max_contracts"] == 1
    assert sp["max_price"] == 0.65 and sp["sell_yes"] == 0.35
