"""Exits with multi-contract positions (Nov 7): partial sales, partly filled orders,
fresh order ids per attempt, fees shared out, and fill syncing after a split."""
import pytest

from wxbot import trader
from tests.test_trader import NOW, _book, _Books, cfg


class SellClient:
    """Fills at most `depth` contracts per sell (what the bid can take)."""
    def __init__(self, depth):
        self.depth, self.calls, self.orders = list(depth), [], {}

    def sell_now(self, ticker, side, price, count, cid):
        self.calls.append((ticker, side, price, count, cid))
        got = min(count, self.depth.pop(0) if self.depth else 0)
        return {"fill_count": got, "status": "executed"}

    def resting_orders(self):
        return self.orders

    def get_order(self, oid):
        return self.orders[oid]


def live_row(n=6, filled=6, status="filled", fee=""):
    return {"id": "A|no", "client_id": "wx-mentions-abc", "order_id": "o1", "mode": "live",
            "strategy": "mentions", "ticker": "A", "event": "A", "side": "no", "price": "0.50",
            "count": n, "filled": filled, "status": status, "title": "A", "note": "", "fee": fee}


@pytest.fixture(autouse=True)
def bundle(monkeypatch):
    for k in ("TRADING_MODE", "BANKROLL", "KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY"):
        monkeypatch.delenv(k, raising=False)
    trader._BUNDLE = []
    yield
    trader._BUNDLE = None


SPEC = {"mentions": {"hold_edge": -0.2}}
BOOK = _Books({"A": _book(0.01, 0.30)})          # NO bid 70c, worth selling under SPEC


def test_partial_sale_keeps_rest_and_retries_with_new_id():
    rows = [live_row()]
    cl = SellClient([2, 4])
    assert trader.exits(BOOK, rows, NOW, SPEC, cl, cfg(), False, set()) == 1
    assert cl.calls[0][3] == 6                      # asks for all 6, but only at 70c or better
    parent, sold = rows
    assert (parent["status"], parent["filled"], parent["count"]) == ("filled", 4, 4)
    assert (sold["id"], sold["filled"], sold["result"]) == ("A|no|exit", 2, "exit")
    assert float(sold["pnl"]) == pytest.approx(2 * 0.20 - trader._fee(0.70, 2))
    later = NOW.replace(minute=5)
    assert trader.exits(BOOK, rows, later, SPEC, cl, cfg(), False, set()) == 1
    assert cl.calls[1][3] == 4 and cl.calls[1][4] != cl.calls[0][4]   # fresh client id
    assert parent["status"] == "settled" and parent["filled"] == 4
    assert {r["id"] for r in rows} == {"A|no", "A|no|exit"}
    assert trader.realized(rows) == pytest.approx(0.40 - trader._fee(0.70, 2) + 0.80 - trader._fee(0.70, 4))


def test_empty_sale_changes_nothing():
    rows = [live_row()]
    assert trader.exits(BOOK, rows, NOW, SPEC, SellClient([0]), cfg(), False, set()) == 0
    assert len(rows) == 1 and rows[0]["status"] == "filled" and rows[0]["filled"] == 6


def test_partly_filled_resting_order_is_checked_and_keeps_resting():
    rows = [live_row(n=6, filled=4, status="resting")]
    cl = SellClient([4])
    assert trader.exits(BOOK, rows, NOW, SPEC, cl, cfg(), False, set()) == 1
    parent, sold = rows
    assert (parent["status"], parent["count"], parent["filled"]) == ("resting", 2, 0)
    assert sold["filled"] == 4 and sold["order_id"] == "o1"
    # Kalshi then reports 5 filled on the order (one more after our sale): only 1 is new
    cl.orders = {"o1": {"status": "resting", "fill_count": 5}}
    trader.sync_live(cl, rows, cfg(), None, True)
    assert parent["filled"] == 1 and parent["status"] == "resting"
    cl.orders = {"o1": {"status": "executed", "fill_count": 6}}
    trader.sync_live(cl, rows, cfg(), None, True)
    assert parent["filled"] == 2 and parent["status"] == "filled"


def test_entry_fee_is_shared_out_on_partial_sale():
    rows = [live_row(fee="0.06")]
    trader.exits(BOOK, rows, NOW, SPEC, SellClient([2]), cfg(), False, set())
    parent, sold = rows
    assert (sold["fee"], parent["fee"]) == ("0.02", "0.04")
    assert float(sold["pnl"]) == pytest.approx(2 * 0.20 - trader._fee(0.70, 2) - 0.02)


def test_order_fee_rounds_on_the_whole_order():
    assert trader._fee(0.50, 6) == 0.11 and 6 * trader._fee(0.50) == pytest.approx(0.12)


def test_netting_a_resting_leg_does_not_double_count_fills():
    base = {"mode": "live", "strategy": "weather", "ticker": "T", "close": "", "title": "T", "note": ""}
    rows = [dict(base, id="T|yes", order_id="oy", side="yes", price="0.27", count=1, filled=1, status="filled"),
            dict(base, id="T|no", order_id="on", side="no", price="0.55", count=3, filled=1, status="resting")]
    assert trader.net_pairs(rows, NOW) == 1
    no = rows[1]
    assert (no["count"], no["filled"], no["status"]) == (2, 0, "resting")
    cl = SellClient([])
    cl.orders = {"on": {"status": "resting", "fill_count": 1}, "x1": {}, "x2": {}, "x3": {}}
    trader.sync_live(cl, rows, cfg(), None, True)
    assert no["filled"] == 0                        # the 1 Kalshi reports was already netted
