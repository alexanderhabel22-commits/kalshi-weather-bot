import csv
import json

from wxbot import m15_fv as fv


def test_parse_venues():
    cb = json.dumps({"type": "ticker", "product_id": "BTC-USD", "price": "100", "best_bid": "99",
                     "best_ask": "101"})
    assert fv.parse_venue("coinbase", cb) == [("BTC-USD", 100.0)]
    kr = json.dumps({"channel": "ticker", "type": "update",
                     "data": [{"symbol": "BTC/USD", "bid": 10, "ask": 12, "last": 11.5}]})
    assert fv.parse_venue("kraken", kr) == [("BTC/USD", 11.0)]
    bs = json.dumps({"event": "trade", "channel": "live_trades_btcusd", "data": {"price": 5.5}})
    assert fv.parse_venue("bitstamp", bs) == [("btcusd", 5.5)]
    assert fv.parse_venue("kraken", "not json") == []
    assert fv.parse_venue("coinbase", json.dumps({"type": "heartbeat"})) == []
    assert fv.parse_venue("kraken", json.dumps({"channel": "ticker", "data": [{"bid": "x"}]})) == []


def test_fair_yes_shapes():
    sig = 1e-4                                              # 1 bp per second
    assert abs(fv.fair_yes(100.0, 100.0, sig, 600, []) - 0.5) < 1e-9
    assert fv.fair_yes(100.5, 100.0, sig, 600, []) > 0.9    # 50 bp ahead, ~2 sd
    assert fv.fair_yes(99.5, 100.0, sig, 600, []) < 0.1
    # inside the last minute: 50 s of the average already fixed above the strike
    assert fv.fair_yes(99.99, 100.0, sig, 10, [100.05] * 50) > 0.99
    # averaging makes the last minute much more certain than a point price would
    point = fv._phi((100.01 - 100.0) / (100.01 * sig * 30 ** 0.5))
    assert fv.fair_yes(100.01, 100.0, sig, 30, [100.01] * 30) > point
    assert fv.fair_yes(100.0, 100.0, sig, 0, [100.2]) == 1.0


def test_composite_median_and_sigma():
    c = fv.Composite(["BTC"])
    for i in range(200):
        t = 1000.0 + i
        c.on_price("coinbase", "BTC", 100 + (i % 2) * 0.01, t)
        c.on_price("kraken", "BTC", 100.02, t)
        c.on_price("bitstamp", "BTC", 500.0, t - 60)      # stale: ignored
        c.sample(t)
        c.sample(t + 0.5)                                 # same second: no extra point
    assert len(c.series["BTC"]) == 200
    px, n = c.now_px("BTC", 1199.0)
    assert n == 2 and 100 < px < 100.02
    s = c.sigma("BTC", 1199.0)
    assert s is not None and s > 0
    assert c.healthy("BTC", 1199.0) and not c.healthy("BTC", 1300.0)


class FakeBot:
    def __init__(self, markets):
        self.state = {"markets": markets}
        self.http = None

    def quote(self, tk, now):
        return {"yes_bid_dollars": 0.80, "yes_ask_dollars": 0.82}


def test_shadow_step_logs_ticks_and_fills(tmp_path):
    c = fv.Composite(["BTC"])
    for i in range(300):
        t = 10_000.0 + i
        c.on_price("coinbase", "BTC", 100.0 + 0.002 * (i % 3), t)
        c.sample(t)
    now = 10_299.5
    m = {"ticker": "KXBTC15M-X", "series": "KXBTC15M", "close": int(now) + 300, "strike": 99.9,
         "filled": 0, "fav_side": "yes", "order": {"side": "yes", "q": 0.8}}
    other = {"ticker": "KXSILVER15M-X", "series": "KXSILVER15M", "close": int(now) + 300}
    sh = fv.FairShadow(c, {"KXBTC15M": "BTC"}, tmp_path, every_s=5)
    bot = FakeBot({m["ticker"]: m, other["ticker"]: other})
    sh.step(bot, now)
    m["filled"] = 1
    sh.step(bot, now + 1)                      # fill shows up -> fill row, before the next tick
    files = list(tmp_path.glob("fv-*.csv"))
    assert len(files) == 1
    rows = files[0].read_text().strip().splitlines()
    assert rows[0].split(",") == fv.FV_COLS
    events = [r.split(",")[1] for r in rows[1:]]
    assert events == ["tick", "fill"]
    assert rows[1].split(",")[fv.FV_COLS.index("yes_bid")] == "0.8"
    assert 0.5 < m["fv_fill"] <= 1.0 and m["fv_fills"] == 1
    assert sh.files(now) == {}                 # current hour isn't uploaded yet
    later = sh.files(now + 7200)
    assert list(later) == [files[0].name]
    sh.mark_uploaded(later)
    assert sh.files(now + 7200) == {}


def test_shadow_skips_without_strike_or_feed(tmp_path):
    c = fv.Composite(["BTC"])
    sh = fv.FairShadow(c, {"KXBTC15M": "BTC"}, tmp_path)
    m = {"ticker": "T", "series": "KXBTC15M", "close": 2000, "strike": None, "filled": 0}
    sh.step(FakeBot({"T": m}), 1500.0)
    assert not list(tmp_path.glob("fv-*.csv"))
    assert sh.fair_value({**m, "strike": 100}, 1500.0) is None


def test_parse_cf():
    raw = json.dumps({"type": "cfbenchmarks_value_5hz", "sid": 3,
                      "msg": {"index_id": "BRTI", "value_usd": "76012.12345678",
                              "source_ts_ms": 1794300000123, "received_at": 1}})
    assert fv.parse_cf(raw) == [("BTC", 76012.12345678, 1794300000.123)]
    assert fv.parse_cf(json.dumps({"type": "ticker", "msg": {"index_id": "BRTI", "value_usd": "1"}})) == []
    assert fv.parse_cf(json.dumps({"type": "cfbenchmarks_value_5hz", "msg": {"index_id": "XYZ"}})) == []
    assert fv.parse_cf("nope") == []


def test_shadow_logs_cf_side_by_side(tmp_path):
    c = fv.Composite(["BTC"])
    for i in range(300):
        t = 20_000.0 + i
        c.on_price("coinbase", "BTC", 100.0 + 0.002 * (i % 3), t)
        c.on_cf("BTC", 100.01 + 0.002 * (i % 3), t)
        c.on_cf("ZZZ", 5.0, t)                     # coin we don't track: ignored
        c.sample(t)
    now = 20_299.5
    m = {"ticker": "B", "series": "KXBTC15M", "close": int(now) + 30, "strike": 100.0, "filled": 0}
    got = fv.FairShadow(c, {"KXBTC15M": "BTC"}, tmp_path).fair_value(m, now)
    assert got["cf_index"] and 0 <= got["fv_cf"] <= 1 and got["fv_cf"] > got["fv_yes"] - 0.5
    # an hour file with an older header gets a side file instead of mixed columns
    sh = fv.FairShadow(c, {"KXBTC15M": "BTC"}, tmp_path)
    from datetime import datetime, timezone
    old = tmp_path / f"fv-{datetime.fromtimestamp(now, timezone.utc):%Y-%m-%d-%H}.csv"
    old.write_text("ts,event\n1,tick\n")
    sh.step(FakeBot({"B": m}), now)
    side = old.with_name(old.stem + "-b.csv")
    assert side.exists() and side.read_text().splitlines()[0].split(",") == fv.FV_COLS
    assert sh.files(now) == {}                     # neither file of the current hour goes yet
    assert set(sh.files(now + 3600)) == {old.name, side.name}


class LagBot(FakeBot):
    def __init__(self, markets, tape=()):
        super().__init__(markets)
        self.http = object()
        self.kfeed = type("K", (), {"tape": lambda s, tk, since: [t for t in tape if t[0] > since]})()


def test_lag_probe_logs_jump_book_and_follow_ups(tmp_path, monkeypatch):
    from wxbot import kalshi
    books = {"n": 0}

    def fake_book(http, tk):
        books["n"] += 1
        return {"yes": [(0.40, 5.0)], "no": [(0.45, 7.0), (0.30, 2.0)]}   # buy yes at 0.55 x7
    monkeypatch.setattr(kalshi, "get_orderbook", fake_book)
    c = fv.Composite(["BTC"])
    for i in range(300):
        t = 10_000.0 + i
        c.on_price("coinbase", "BTC", 100.0 + 0.002 * (i % 3), t)
        c.sample(t)
    now = 10_299.5
    m = {"ticker": "KXBTC15M-X", "series": "KXBTC15M", "close": int(now) + 400, "strike": 100.0}
    sh = fv.FairShadow(c, {"KXBTC15M": "BTC"}, tmp_path, lag=True)
    bot = LagBot({m["ticker"]: m}, tape=[(now + 2, 0.55, 3.0, "yes"), (now + 3, 0.60, 1.0, "yes"),
                                         (now + 3, 0.50, 4.0, "no")])
    for k in range(7):                        # flat for 6 s
        c.on_price("coinbase", "BTC", 100.0, now - 6 + k)
        c.sample(now - 6 + k)
        sh.step(bot, now - 6 + k)
    assert sh.lag.n == 0
    c.on_price("coinbase", "BTC", 100.08, now + 1)   # index jumps up -> fair yes jumps
    c.sample(now + 1)
    sh.step(bot, now + 1)
    assert sh.lag.n == 1
    for t in (now + 7, now + 17):
        c.on_price("coinbase", "BTC", 100.08, t)
        c.sample(t)
        sh.step(bot, t)
    rows = list(csv.DictReader(open(next(tmp_path.glob("lag-*.csv")))))
    assert [r["stage"] for r in rows] == ["jump", "+5s", "+15s"]
    j = rows[0]
    assert j["side"] == "yes" and j["book_ask"] == "0.55" and j["depth_ask"] == "7.0"
    assert float(j["fair"]) > float(j["fair_before"]) + 0.08
    assert rows[2]["tape_ct_at_ask"] == "3.0" and rows[2]["tape_ct_any"] == "4.0"
    assert books["n"] == 3
    assert not sh.lag.open


def test_lag_files_upload(tmp_path):
    sh = fv.FairShadow(fv.Composite(["BTC"]), {"KXBTC15M": "BTC"}, tmp_path, lag=True)
    (tmp_path / "lag-2026-11-13-01.csv").write_text("a\n")
    (tmp_path / "lag-2026-11-13-02.csv").write_text("b\n")
    from datetime import datetime, timezone
    now = datetime(2026, 11, 13, 2, 30, tzinfo=timezone.utc).timestamp()
    assert list(sh.files(now)) == ["lag-2026-11-13-01.csv"]


def test_place_check_logs_cached_vs_real_book(tmp_path, monkeypatch):
    from wxbot import kalshi
    monkeypatch.setattr(kalshi, "get_orderbook",
                        lambda http, tk: {"yes": [(0.78, 900.0), (0.80, 5.0)], "no": [(0.18, 50.0)]})
    sh = fv.FairShadow(fv.Composite(["BTC"]), {"KXBTC15M": "BTC"}, tmp_path, lag=True)
    m = {"ticker": "KXBTC15M-X", "series": "KXBTC15M", "close": 10_400, "strike": 100.0}
    sh.lag.place_check(LagBot({m["ticker"]: m}), m, "yes", 0.80, 2, 10_000.0)
    r = list(csv.DictReader(open(next(tmp_path.glob("lag-*.csv")))))[0]
    assert r["stage"] == "place" and r["ws_ask"] == "0.8" and r["book_bid"] == "0.8"
    assert r["book_ask"] == "0.82" and r["depth_ask"] == "5.0" and r["depth_edge"] == "0.8"
    assert r["tape_ct_at_ask"] == "2" and r["fair"] == ""
