from wxbot.paperlab import FieldPaper, Lab, MakerPaper, ReadOnly, RfqPaper

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


def test_maker_tags_fills_with_casual_share_and_scores_settlement(tmp_path):
    http, clock = Http(), [T0]
    mm = MakerPaper(lab(tmp_path, http, clock))
    mm.cycle()
    # one $4 order (two prints of the same order), one $400 order, then a small YES buy that fills our NO bid
    http.trades = [{"trade_id": "a1", "created_time": iso(T0 + 5), "yes_price_dollars": "0.42",
                    "taker_side": "yes", "count_fp": "5"},
                   {"trade_id": "a2", "created_time": iso(T0 + 5), "yes_price_dollars": "0.43",
                    "taker_side": "yes", "count_fp": "5"},
                   {"trade_id": "a3", "created_time": iso(T0 + 6), "yes_price_dollars": "0.60",
                    "taker_side": "no", "count_fp": "1000"},
                   {"trade_id": "a4", "created_time": iso(T0 + 30), "yes_price_dollars": "0.45",
                    "taker_side": "yes", "count_fp": "40"}]
    clock[0] += 60
    mm.cycle()
    assert mm.st["flow"]["KXMEN-1-A"] == {"all": 1050.0, "small": 50.0}
    (h,) = mm.st["held"]
    assert (h["side"], h["px"], h["casual"]) == ("no", 0.55, round(50 / 1050, 3))
    assert MakerPaper.flow_key(0.6, "no") == "50plus|no" and MakerPaper.flow_key(None, "yes") == "unknown|yes"
    mm.settle()                                   # market not settled yet: the fill stays held
    assert len(mm.st["held"]) == 1
    http.markets["KXMEN-1-A"] = {"result": "no"}
    mm.settle()
    assert mm.st["held"] == []
    assert mm.status()["by_flow"]["under20|no"]["settled_c"] == 45.0


def test_field_paper_sells_the_field_once_and_scores(tmp_path):
    http, clock = Http(), [T0]
    fp = FieldPaper(lab(tmp_path, http, clock))
    mk = lambda tk, b, a: {"ticker": tk, "status": "active", "yes_bid_dollars": b, "yes_ask_dollars": a,  # noqa: E731
                           "volume_24h_fp": "300"}
    ev = {"event_ticker": "KXTOP-1", "category": "Entertainment", "mutually_exclusive": True, "markets": [
        mk("A", "0.40", "0.44"), mk("B", "0.20", "0.24"), mk("C", "0.12", "0.16"),
        mk("D", "0.03", "0.05"), mk("E", "0.70", "0.74")]}
    fp.consider(ev, T0)
    fp.consider(ev, T0 + 60)                                   # an event is entered only once
    assert [r["tk"] for r in fp.st["open"]] == ["A", "B", "C"]   # only the 10-50c candidates
    assert fp.st["open"][0]["no_take"] == 0.60 and fp.st["events"]["KXTOP-1"]["ask_sum"] == 1.63
    # not one-winner, wrong category, or too few candidates: skipped
    fp.consider(dict(ev, event_ticker="X2", mutually_exclusive=False), T0)
    fp.consider(dict(ev, event_ticker="X3", category="Sports"), T0)
    fp.consider(dict(ev, event_ticker="X4", markets=ev["markets"][:3]), T0)
    assert len(fp.st["open"]) == 3
    assert fp.st["seen"] == {"entered": 1, "not_one_winner": 1, "under_5_candidates": 1}
    http.markets = {"A": {"result": "yes"}, "B": {"result": "no"}}
    fp.settle()
    assert [r["tk"] for r in fp.st["open"]] == ["C"]
    st = fp.status()
    assert st["all"]["n"] == 2 and st["all"]["no_won"] == 1
    a, b = st["recent"]
    assert a["pnl_take"] == round((0 - 0.60 - 0.07 * 0.6 * 0.4) * 100, 2)
    assert b["pnl_take"] == round((1 - 0.80 - 0.07 * 0.8 * 0.2) * 100, 2)


def test_maker_scan_feeds_field_paper(tmp_path):
    http, clock = Http(), [T0]
    lb = lab(tmp_path, http, clock)
    fp = FieldPaper(lb)
    seen = []
    fp.consider = lambda e, now: seen.append(e["event_ticker"])
    mm = MakerPaper(lb, fp)
    mm.cycle()
    assert seen == ["KXMEN-1", "KXNFL-1"]
    assert mm.status()["last_scan"]["events"] == 2 and mm.status()["last_scan"]["done"]


def test_maker_rescans_soon_after_a_failed_scan_and_on_first_run_of_new_version(tmp_path):
    http, clock = Http(), [T0]
    mm = MakerPaper(lab(tmp_path, http, clock))
    mm.cycle()
    mm.st.pop("select_info")                      # state saved by the older version
    calls = []
    orig = http.get_json

    def failing(url, params=None, headers=None):
        if url.endswith("/events"):
            calls.append(clock[0])
            raise RuntimeError("HTTP 429")
        return orig(url, params, headers)
    http.get_json = failing
    clock[0] += 60
    mm.cycle()                                    # scans at once, fails, keeps the old markets
    assert len(calls) == 1 and list(mm.st["markets"]) == ["KXMEN-1-A"]
    assert mm.status()["last_scan"]["error"] == "HTTP 429"
    clock[0] += 300
    mm.cycle()
    assert len(calls) == 1                        # not yet
    clock[0] += 400
    mm.cycle()
    assert len(calls) == 2                        # retried ~10 minutes later, not 6 hours


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
