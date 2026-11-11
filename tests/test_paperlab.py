from wxbot.paperlab import Lab, MakerPaper, ReadOnly, RfqPaper

T0 = 1_794_400_000.0


def iso(t):
    import datetime as dt
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat().replace("+00:00", "Z")


class Http:
    def __init__(self):
        self.book = {"orderbook_fp": {"yes_dollars": [["0.40", "10"]], "no_dollars": [["0.54", "10"]]}}
        self.trades = []
        self.markets = {}

    def get_json(self, url, params=None, headers=None):
        if url.endswith("/events"):
            return {"events": [{"event_ticker": "KXMEN-1", "category": "Mentions", "markets": [
                {"ticker": "KXMEN-1-A", "status": "active", "yes_bid_dollars": "0.40",
                 "yes_ask_dollars": "0.46", "volume_24h_fp": "500"},
                {"ticker": "KXMEN-1-B", "status": "active", "yes_bid_dollars": "0.40",
                 "yes_ask_dollars": "0.41", "volume_24h_fp": "900"}]},       # spread too tight
                {"event_ticker": "KXNFL-1", "category": "Sports", "markets": [
                    {"ticker": "KXNFL-1-A", "status": "active", "yes_bid_dollars": "0.40",
                     "yes_ask_dollars": "0.50", "volume_24h_fp": "9000"}]}]}
        if url.endswith("/orderbook"):
            return self.book
        if url.endswith("/markets/trades"):
            return {"trades": self.trades}
        if url.endswith("/markets"):
            return {"markets": [self.markets[t] for t in params["tickers"].split(",") if t in self.markets]}
        if "/markets/" in url:
            return {"market": self.markets.get(url.rsplit("/", 1)[1], {})}
        raise AssertionError(url)


def lab(tmp_path, http, clock):
    return Lab(tmp_path, http, clock=lambda: clock[0], sleep=lambda s: None)


def test_maker_picks_wide_nonsports_and_fills(tmp_path):
    http, clock = Http(), [T0]
    mm = MakerPaper(lab(tmp_path, http, clock))
    mm.cycle()
    assert list(mm.st["markets"]) == ["KXMEN-1-A"]
    assert mm.st["orders"]["KXMEN-1-A|yes"]["px"] == 0.41       # 1c above the best YES bid
    assert mm.st["orders"]["KXMEN-1-A|no"]["px"] == 0.55
    # a taker sells YES at 41c -> our YES bid fills; a YES buy at 44c is below our 45c offer (NO bid 55c)
    http.trades = [{"trade_id": "t1", "created_time": iso(T0 + 30), "yes_price_dollars": "0.41",
                    "taker_side": "no", "count_fp": "3"},
                   {"trade_id": "t2", "created_time": iso(T0 + 31), "yes_price_dollars": "0.44",
                    "taker_side": "yes", "count_fp": "3"}]
    clock[0] += 60
    mm.cycle()
    assert mm.st["inv"]["KXMEN-1-A"] == 1
    assert len(mm.st["pending"]) == 1
    # same trades again are not double counted
    clock[0] += 60
    mm.cycle()
    assert mm.st["inv"]["KXMEN-1-A"] == 1
    # mid rises to 47c: +6c at 30 minutes
    http.book = {"orderbook_fp": {"yes_dollars": [["0.44", "10"]], "no_dollars": [["0.50", "10"]]}}
    clock[0] += 1800
    mm.cycle()
    st = mm.status()["by_category"]["Mentions"]
    assert st["fills"] == 1 and st["markout_30m_c"] == 6.0


class Signed:
    def __init__(self, rfqs):
        self.rfqs, self.calls = rfqs, []

    def _get(self, path, params=None):
        self.calls.append(path)
        return {"rfqs": self.rfqs}

    def place_limit(self, *a):            # must never be reachable through ReadOnly
        raise AssertionError("order")


def test_rfq_prices_cross_combos_and_scores(tmp_path):
    http, clock = Http(), [T0]
    http.markets = {"KXA-1": {"ticker": "KXA-1", "yes_bid_dollars": "0.49", "yes_ask_dollars": "0.51"},
                    "KXB-1": {"ticker": "KXB-1", "yes_bid_dollars": "0.19", "yes_ask_dollars": "0.21"},
                    "KXMVE-1": {"ticker": "KXMVE-1", "result": ""}}
    rfqs = [{"id": "r1", "market_ticker": "KXMVE-1", "contracts_fp": "10", "created_ts": iso(T0),
             "mve_selected_legs": [{"event_ticker": "KXA", "market_ticker": "KXA-1", "side": "yes"},
                                   {"event_ticker": "KXB", "market_ticker": "KXB-1", "side": "no"}]},
            {"id": "r2", "market_ticker": "KXMVE-2", "contracts_fp": "5", "created_ts": iso(T0),
             "mve_selected_legs": [{"event_ticker": "KXA", "market_ticker": "KXA-1", "side": "yes"},
                                   {"event_ticker": "KXA", "market_ticker": "KXA-2", "side": "yes"}]}]
    signed = Signed(rfqs)
    ro = ReadOnly(signed)
    assert not hasattr(ro, "place_limit")
    rq = RfqPaper(lab(tmp_path, http, clock), ro)
    rq.poll()
    r1, r2 = rq.st["rfqs"]["r1"], rq.st["rfqs"]["r2"]
    assert r1["cross"] and r1["fair"] == 0.4                      # 0.50 * (1 - 0.20)
    assert r1["quotes"]["0.1"] == 0.45                            # 0.40 * 1.1 + 1c
    assert not r2["cross"] and "fair" not in r2                   # same event: not independent
    http.trades = [{"taker_side": "yes", "yes_price_dollars": "0.52"}]
    http.markets["KXMVE-1"]["result"] = "no"
    clock[0] += 700
    rq.check()
    st = rq.status()
    assert st["by_margin"]["0.1"]["would_win"] == 1
    assert st["by_margin"]["0.1"]["pnl_c_per_ct"] == 45.0         # sold YES at 45c, it settled NO
    assert st["by_margin"]["0.1"]["won_pnl_c_per_ct"] == 45.0
    assert st["by_margin"]["0.1"]["capped_pnl_dollars"] == 2.25   # loss cap: $3 / 55c risk = 5 contracts x 45c
    assert len(st["settled_rows"]) == 1


def test_capped_size_uses_dollar_size_and_loss_cap():
    from wxbot.paperlab import capped_size
    assert capped_size({"contracts": 0.0, "target_cost": 4.0}, 0.8) == 5       # $4 at 80c
    assert capped_size({"contracts": 100.0}, 0.1) == 3                        # $3 / 90c risk
    assert capped_size({"contracts": 100.0}, 0.9) == 25                       # contract cap
