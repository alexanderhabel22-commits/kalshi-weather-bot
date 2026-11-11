from wxbot import latency_probe as lp


class FakeHttp:
    def get_json(self, url, params=None, headers=None):
        if url.endswith("/markets") and params["series_ticker"] == "KXBTC15M":
            return {"markets": [{"ticker": "KXBTC15M-X", "yes_bid_dollars": "0.50",
                                 "yes_ask_dollars": "0.51", "close_time": "2099-01-01T00:00:00Z"}]}
        if url.endswith("/markets"):
            return {"markets": [{"ticker": "KXFED-LOW", "yes_bid_dollars": "0.01", "yes_ask_dollars": "0.02"},
                                {"ticker": "KXFED-MID", "yes_bid_dollars": "0.40", "yes_ask_dollars": "0.42"}]}
        return {"orderbook": {}}


class FakeClient:
    def __init__(self):
        self.placed, self.cancelled = [], []

    def balance(self):
        return 1.0

    def place_limit(self, tk, side, price, count, cid):
        assert side == "yes" and price == 0.01 and count == 1 and cid.startswith("latprobe-")
        self.placed.append(tk)
        return {"order_id": f"o{len(self.placed)}"}

    def cancel(self, oid, tk=""):
        self.cancelled.append(oid)

    def get_order(self, oid):
        return {"status": "canceled", "fill_count_fp": "0.00"}


def test_probe_places_tiny_bids_and_cancels_each():
    c = FakeClient()
    res = lp.run(FakeHttp(), c, rounds=3, pause=lambda s: None)
    assert set(res["markets"]) == {"KXBTC15M-X", "KXFED-MID"}
    assert len(c.placed) == 6 and len(c.cancelled) == 6
    r = res["markets"]["KXBTC15M-X"]
    assert r["filled"] == 0 and r["place_ms"]["n"] == 3 and not r["errors"]
    assert res["public_book_ms"]["n"] == 15
