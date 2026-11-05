from datetime import datetime, timedelta, timezone

import pytest

from wxbot import trader
from wxbot.kalshi_api import KalshiClient, OrderRejected, check_order, order_fill_count
from wxbot.strats import favorites, mentions
from wxbot.util import load_config

UTC = timezone.utc
NOW = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)
CLOSE = (NOW + timedelta(hours=20)).isoformat().replace("+00:00", "Z")


def cfg(mode="paper", bankroll=40.0, **mentions_kw):
    c = load_config()
    c.raw["account"]["bankroll"] = bankroll
    c.raw["trading"]["mode"] = mode
    c.raw["trading"]["mentions"].update(mentions_kw)
    return c


def mkt(word, yb, ya, ev="KXTRUMPMENTION-26NOV06", vol=500):
    return {"ticker": f"{ev}-{word}", "event_ticker": ev, "close_time": CLOSE,
            "occurrence_datetime": (NOW + timedelta(hours=10)).isoformat().replace("+00:00", "Z"),
            "yes_bid_dollars": f"{yb:.2f}", "yes_ask_dollars": f"{ya:.2f}", "volume": vol,
            "title": f"Will Trump say {word}?"}


class Http:
    def __init__(self, markets, results=None, trades=()):
        self.markets, self.results, self.trades, self.posts = markets, results or {}, list(trades), []

    def get_json(self, url, params=None, headers=None):
        if url.endswith("/events"):
            return {"events": [], "cursor": ""}
        if url.endswith("/series"):
            return {"series": [{"ticker": "KXTRUMPMENTION", "category": "Mentions"}]}
        if url.endswith("/markets"):
            s = (params or {}).get("series_ticker")
            return {"markets": [m for m in self.markets if not s or m["event_ticker"].startswith(s + "-")],
                    "cursor": ""}
        if url.endswith("/markets/trades"):
            return {"trades": [{"yes_price_dollars": f"{p:.2f}"} for p in self.trades], "cursor": ""}
        if "/markets/" in url:
            tk = url.rsplit("/", 1)[1]
            return {"market": {"ticker": tk, "result": self.results.get(tk, "")}}
        raise AssertionError(url)

    def post_json(self, url, payload, headers=None):
        self.posts.append(payload)
        return {}


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    for k in ("TRADING_MODE", "BANKROLL", "KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_limits_scale_with_bankroll():
    t = trader.settings(cfg(bankroll=40))
    assert (t["max_open_dollars"], t["daily_loss_stop"], t["total_loss_stop"]) == (24.0, -4.0, -10.0)
    t = trader.settings(cfg(bankroll=400))
    assert t["total_loss_stop"] == -100.0
    assert trader.strategy_settings(cfg(bankroll=40), "mentions", t | {"bankroll": 40})["max_event_dollars"] == 10.0


def test_kill_switch_variable(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "off")
    out = trader.run(cfg(), now=NOW, http=Http([mkt("crypto", 0.12, 0.16)]))
    assert out["mode"] == "off" and not out["placed"] and not trader.load()
    monkeypatch.setenv("TRADING_MODE", "yolo")          # anything unknown = off
    assert trader.settings(cfg())["mode"] == "off"


def test_live_without_key_falls_back_to_paper_only():
    out = trader.run(cfg(mode="live", live=True), now=NOW, http=Http([mkt("crypto", 0.12, 0.16)]))
    assert not out["placed"] and "no API key" in out["stops"][0]


def test_strategy_stays_paper_until_cleared_for_live():
    cl = FakeClient()
    out = trader.run(cfg(mode="live"), now=NOW, http=Http([mkt("a", 0.12, 0.16)]), client=cl)
    assert not cl.placed and [r["mode"] for r in out["placed"]] == ["paper"]
    assert "mentions" in trader.strategies() and "weather" in trader.strategies()


def test_mentions_uses_event_time_not_close_time():
    class Ctx:
        state, now = {}, NOW
        http = Http([mkt("a", 0.12, 0.16) | {"close_time": "2026-11-20T15:00:00Z"},
                     mkt("b", 0.12, 0.16) | {"occurrence_datetime": "2026-11-18T15:00:00Z"}])
    got = [m["ticker"][-1] for m in mentions.open_markets(Ctx, {"KXTRUMPMENTION"}, 1, 72)]
    assert got == ["a"]


def test_mentions_picks_band_cheapest_three_per_event():
    ms = [mkt("a", 0.12, 0.16), mkt("b", 0.20, 0.24), mkt("c", 0.15, 0.19), mkt("d", 0.11, 0.13),
          mkt("e", 0.02, 0.04),                    # NO 96c: outside band
          mkt("f", 0.12, 0.16, vol=5),             # too thin
          mkt("g", 0.12, 0.16, ev="KXNBA-26NOV06")]   # not a Mentions series
    out = mentions.candidates(ms, {"KXTRUMPMENTION"}, {"contracts": 2})
    assert [(i.ticker[-1], i.price) for i in out] == [("b", 0.77), ("c", 0.82), ("a", 0.85)]
    assert all(i.side == "no" and i.count == 2 for i in out)
    # 1c spread: join the bid instead of crossing
    assert mentions.candidates([mkt("d", 0.12, 0.13)], {"KXTRUMPMENTION"}, {})[0].price == 0.87


def test_paper_run_caps_dedupes_then_settles():
    ms = [mkt(w, 0.12, 0.16) for w in "abc"] + [mkt(w, 0.12, 0.16, ev=f"KXTRUMPMENTION-26NOV0{i}")
                                               for i, w in enumerate("defgh", 1)]
    c = cfg(bankroll=20)                  # open cap 12, mentions 10, per event 5
    # one speech: three words at 1.70 each would be 5.10 > the 5.00 event cap
    one = trader.run(c, now=NOW, http=Http(ms[:3]), dry_run=True)
    assert len(one["placed"]) == 2 and one["skipped"][0][2] == "event open-$ cap"
    out = trader.run(c, now=NOW, http=Http(ms))
    rows = trader.load()
    assert len(out["placed"]) == len(rows) > 0
    assert trader.exposure(rows) <= 10.0 + 1e-9
    for ev in {r["event"] for r in rows}:
        assert trader.exposure(rows, event=ev) <= 5.0 + 1e-9
    assert any(why == "strategy open-$ cap" for *_, why in out["skipped"])
    # next scan: same markets are never ordered twice
    out2 = trader.run(c, now=NOW + timedelta(minutes=30), http=Http(ms))
    assert not out2["placed"] and all(why in ("duplicate", "strategy open-$ cap", "overall open-$ cap",
                                             "event open-$ cap") for *_, why in out2["skipped"])
    # after close: word "a" was said (YES), filled by a 15c YES print -> lose 2 x 85c
    later = NOW + timedelta(hours=30)
    res = {r["ticker"]: "no" for r in rows} | {rows[0]["ticker"]: "yes"}
    trader.run(cfg(bankroll=20, enabled=False), now=later, http=Http([], res, trades=[0.15]))
    rows = trader.load()
    assert all(r["status"] == "settled" for r in rows)
    assert float(rows[0]["pnl"]) == pytest.approx(-1.70)
    assert trader.realized(rows) == pytest.approx(-1.70 + 0.30 * (len(rows) - 1))


def test_total_stop_halts_and_cancels():
    rows = [{"id": "x", "mode": "paper", "strategy": "mentions", "ticker": "T1", "event": "E",
             "side": "no", "price": "0.85", "count": 3, "filled": 3, "status": "settled",
             "pnl": "-11.00", "settled_at": NOW.isoformat(), "close": CLOSE},
            {"id": "y", "mode": "paper", "strategy": "mentions", "ticker": "T2", "event": "E2",
             "side": "no", "price": "0.80", "count": 2, "filled": 0, "status": "resting",
             "close": CLOSE, "placed_at": NOW.isoformat()}]
    trader.save(rows)
    http = Http([mkt("z", 0.12, 0.16)])
    out = trader.run(cfg(bankroll=40), now=NOW, http=http)
    assert out["stops"] and "TOTAL STOP" in out["stops"][0] and not out["placed"]
    assert trader.load()[1]["status"] == "cancelled"
    assert "paper" in trader.read_json(trader.STATE)["halted"]
    # still halted on the next run, with no new orders
    out = trader.run(cfg(bankroll=40), now=NOW + timedelta(minutes=30), http=http)
    assert not out["placed"]


def test_daily_stop_pauses_new_orders():
    trader.save([{"id": "x", "mode": "paper", "strategy": "mentions", "ticker": "T1", "event": "E",
                  "side": "no", "price": "0.85", "count": 2, "filled": 2, "status": "settled",
                  "pnl": "-5.00", "settled_at": NOW.isoformat(), "close": CLOSE}])
    out = trader.run(cfg(bankroll=40), now=NOW, http=Http([mkt("z", 0.12, 0.16)]))
    assert "DAILY STOP" in out["stops"][0] and not out["placed"]


def test_order_sanity():
    for side, price, count in (("yes", 1.0, 1), ("no", 0.0, 1), ("no", 0.855, 1), ("buy", 0.5, 1),
                               ("no", 0.5, 0), ("no", 0.5, 100), ("no", 0.5, 2.0)):
        with pytest.raises(OrderRejected):
            check_order(side, price, count)
    check_order("no", 0.85, 2)
    assert order_fill_count({"fill_count": 2}, 3) == 2
    assert order_fill_count({"remaining_count": 1}, 3) == 2


def test_signed_headers_verify():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    import base64
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    c = KalshiClient(None, "kid", pem)
    h = c.headers("POST", "https://api.elections.kalshi.com/trade-api/v2/portfolio/orders?x=1", 1700000000000)
    assert h["KALSHI-ACCESS-KEY"] == "kid" and h["KALSHI-ACCESS-TIMESTAMP"] == "1700000000000"
    key.public_key().verify(base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]),
                            b"1700000000000POST/trade-api/v2/portfolio/orders",
                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                        salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())


class FakeClient:
    def __init__(self):
        self.placed, self.cancelled, self.orders = [], [], {}

    def place_limit(self, ticker, side, price, count, cid):
        self.placed.append((ticker, side, price, count))
        oid = f"o{len(self.placed)}"
        self.orders[oid] = {"status": "resting", "fill_count": 0}
        return {"order_id": oid}

    def get_order(self, oid):
        return self.orders[oid]

    def cancel(self, oid):
        self.cancelled.append(oid)


def test_live_places_post_only_limits_and_alerts_fills(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    cl, http = FakeClient(), Http([mkt("a", 0.12, 0.16)])
    out = trader.run(cfg(mode="live", live=True), now=NOW, http=http, client=cl)
    assert cl.placed == [("KXTRUMPMENTION-26NOV06-a", "no", 0.85, 2)] and len(out["placed"]) == 1
    cl.orders["o1"] = {"status": "executed", "fill_count": 2}
    trader.run(cfg(mode="live", live=True), now=NOW + timedelta(minutes=5), http=http, client=cl)
    assert any("FILLED" in p["title"] for p in http.posts)
    assert trader.load()[0]["status"] == "filled"


def fav_event(markets, cat="Politics", ev="KXSHUTDOWN-26NOV"):
    return {"event_ticker": ev, "category": cat, "title": "Shutdown ends?", "markets": markets}


def fav_mkt(name, yb, ya, vol=600_000, hours=20, ev="KXSHUTDOWN-26NOV"):
    close = (NOW + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
    return {"ticker": f"{ev}-{name}", "event_ticker": ev, "close_time": close, "status": "active",
            "yes_bid_dollars": f"{yb:.2f}", "yes_ask_dollars": f"{ya:.2f}", "volume": vol}


def test_favorites_picks_big_event_favorites_only():
    p = {"contracts": 2}
    evs = [fav_event([fav_mkt("a", 0.88, 0.91), fav_mkt("b", 0.05, 0.08),   # NO fav at 93c
                      fav_mkt("c", 0.50, 0.55), fav_mkt("d", 0.97, 0.98),   # not a favorite / too dear
                      fav_mkt("e", 0.90, 0.92, hours=400)]),                 # closes too late
           fav_event([fav_mkt("f", 0.90, 0.93, vol=1000, ev="KXSMALL")], ev="KXSMALL"),  # small event
           fav_event([fav_mkt("g", 0.90, 0.93, vol=2_000_000, ev="KXNBA")], cat="Sports", ev="KXNBA")]
    out = favorites.candidates(evs, NOW, p)
    assert [(i.ticker[-1], i.side, i.price) for i in out] == [("b", "no", 0.93), ("a", "yes", 0.89)]
    assert all(i.count == 2 and i.strategy == "favorites" for i in out)
    # 1c spread: join the bid
    one = favorites.candidates([fav_event([fav_mkt("a", 0.90, 0.91, vol=2_000_000)])], NOW, p)
    assert one[0].price == 0.90
    assert "favorites" in trader.strategies()
