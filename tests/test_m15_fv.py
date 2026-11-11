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
