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
    for name in ("mentions", "favorites", "macro_fade", "locks", "weather", "weather_late"):
        c.raw["trading"][name]["live"] = False      # tests opt in to real money explicitly
    c.raw["trading"]["mentions"].update({"contracts": 3, "word_pct": 0.05})   # sizes these tests were written for
    c.raw["trading"]["mentions"].update(mentions_kw)
    return c


def mkt(word, yb, ya, ev="KXTRUMPMENTION-26NOV06", vol=5000):
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
    assert (t["max_open_dollars"], t["daily_loss_stop"], t["total_loss_stop"]) == (40.0, -4.0, -10.0)
    assert t["max_event_dollars"] == 8.0
    t = trader.settings(cfg(bankroll=400))
    assert t["total_loss_stop"] == -100.0
    assert trader.strategy_settings(cfg(bankroll=40), "mentions", t | {"bankroll": 40})["max_event_dollars"] == 8.0


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


def test_mentions_size_scales_with_bankroll():
    p = {"contracts": 3, "word_pct": 0.05}
    assert mentions.word_count(0.80, p, 37) == 2       # $1.85 / 80c
    assert mentions.word_count(0.80, p, 10) == 1       # never below 1
    assert mentions.word_count(0.80, p, 500) == 3      # capped by contracts
    assert mentions.word_count(0.80, {"contracts": 2}, 500) == 2   # no word_pct: fixed
    out = mentions.candidates([mkt("a", 0.12, 0.16)], {"KXTRUMPMENTION"}, p, 100)
    assert out[0].count == 3                           # 5% of $100 / 85c = 5.9 -> cap 3


def test_paper_run_caps_dedupes_then_settles():
    ms = [mkt(w, 0.12, 0.16) for w in "abc"] + [mkt(w, 0.12, 0.16, ev=f"KXTRUMPMENTION-26NOV0{i}")
                                               for i, w in enumerate("defgh", 1)]
    c = cfg(bankroll=20, contracts=2, word_pct=None)  # fixed 2/word; open cap 12, mentions 10, per event 5
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
        self.bought, self.cash, self.fill = [], 100.0, 99

    def place_limit(self, ticker, side, price, count, cid):
        self.placed.append((ticker, side, price, count))
        oid = f"o{len(self.placed)}"
        self.orders[oid] = {"status": "resting", "fill_count": 0}
        return {"order_id": oid}

    def get_order(self, oid):
        return self.orders[oid]

    def balance(self):
        return self.cash

    def buy_now(self, ticker, side, price, count, cid):
        self.bought.append((ticker, side, price, count, cid))
        return {"order_id": f"b{len(self.bought)}", "status": "executed",
                "fill_count": min(count, self.fill)}

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


def test_macro_fade_band_window_and_per_event_cap():
    from wxbot.strats import macro_fade

    def m(tk, yb, ya, hours=48, vol=1000, ev="KXFED-26DEC"):
        close = (NOW + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
        return {"ticker": f"{ev}-{tk}", "event_ticker": ev, "close_time": close,
                "yes_bid_dollars": f"{yb:.2f}", "yes_ask_dollars": f"{ya:.2f}", "volume": vol}
    ms = [m("a", 0.10, 0.12), m("b", 0.20, 0.25), m("c", 0.05, 0.08), m("d", 0.15, 0.18),
          m("e", 0.01, 0.03),                 # NO 97c: outside band (1-4c longshots skipped)
          m("f", 0.40, 0.45),                 # not a longshot
          m("g", 0.10, 0.12, hours=0.5),      # closes too soon
          m("h", 0.10, 0.12, hours=900),      # too far out
          m("i", 0.10, 0.12, vol=10)]         # too thin
    out = macro_fade.candidates(ms, {}, NOW)
    assert [(i.ticker[-1], i.price) for i in out] == [("b", 0.76), ("d", 0.83), ("a", 0.89)]
    assert all(i.side == "no" and i.strategy == "macro_fade" for i in out)
    assert "macro_fade" in trader.strategies()


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


def test_favorites_propose_scans_big_series_only():
    big = [fav_mkr for fav_mkr in (fav_mkt("a", 0.88, 0.91, ev="KXBIG-26NOV"),
                                   fav_mkt("b", 0.40, 0.45, ev="KXBIG-26NOV"))]

    class FavHttp:
        def __init__(self):
            self.calls = []

        def get_json(self, url, params=None, headers=None):
            self.calls.append((url.rsplit("/", 1)[1], dict(params or {})))
            if url.endswith("/series"):
                cat = params["category"]
                return {"series": [{"ticker": "KXBIG", "category": "Politics", "volume": 5_000_000},
                                   {"ticker": "KXTINY", "category": "Politics", "volume": 10}]
                        if cat == "Politics" else []}
            if url.endswith("/events"):
                return {"events": [{"event_ticker": "KXBIG-26NOV", "series_ticker": "KXBIG"},
                                   {"event_ticker": "KXTINY-26NOV", "series_ticker": "KXTINY"},
                                   {"event_ticker": "KXNBA-26NOV", "series_ticker": "KXNBA"}],
                        "cursor": ""}
            if url.endswith("/markets"):
                assert params["series_ticker"] == "KXBIG"
                return {"markets": big, "cursor": ""}
            raise AssertionError(url)

    http = FavHttp()
    ctx = trader.Context(cfg(), http, NOW, {}, [], {"contracts": 2})
    out = favorites.propose(ctx)
    assert [(i.ticker, i.side, i.price) for i in out] == [("KXBIG-26NOV-a", "yes", 0.89)]
    assert ctx.state["active"] == ["KXBIG"]
    # second pass inside 6 h reuses the cached series lists: only the market call
    http.calls.clear()
    favorites.propose(ctx)
    assert [c[0] for c in http.calls] == ["markets"]


def test_paper_fill_marked_before_settlement():
    trader.run(cfg(), now=NOW, http=Http([mkt("a", 0.12, 0.16)]))
    assert trader.load()[0]["status"] == "resting"
    # an hour later YES traded at 20c (>= our 15c offer): filled, market not settled yet
    trader.run(cfg(enabled=False), now=NOW + timedelta(minutes=60), http=Http([], trades=[0.20]))
    r = trader.load()[0]
    assert r["status"] == "filled" and int(r["filled"]) == 2 and r["filled_at"] and not r["pnl"]


def test_paper_fill_when_quotes_move_through_our_bid():
    r = {"side": "no", "price": "0.78"}
    assert trader.crossed(r, {"yes_bid_dollars": "0.31", "yes_ask_dollars": "0.44"})   # NO offered at 69c
    assert not trader.crossed(r, {"yes_bid_dollars": "0.20", "yes_ask_dollars": "0.24"})
    assert trader.crossed({"side": "yes", "price": "0.30"}, {"yes_bid_dollars": "0.25", "yes_ask_dollars": "0.29"})


def test_overall_cap_is_whole_bankroll_live_and_paper():
    # Alex, Nov 12 10:38 PM ET: the whole bankroll may be traded at once (was 75% live).
    t = trader.settings(cfg(bankroll=10))
    sp = {"max_open_dollars": 100}
    rows = [{"strategy": "x", "event": "E0", "ticker": "T0", "side": "no", "price": "0.80",
             "count": 9, "filled": 9, "status": "filled"}]                   # $7.20 open
    it = trader.Intent("mentions", "T1", "E1", "no", 0.80, 1, CLOSE)       # +$0.80 -> $8.00
    assert trader.vet(it, rows, t, sp, NOW, [], "live") is None                   # <= $10.00
    big = trader.Intent("mentions", "T2", "E2", "no", 0.80, 4, CLOSE)      # +$3.20 -> $10.40
    assert trader.vet(big, rows, t, sp, NOW, [], "live") == "overall open-$ cap"


def test_global_event_cap_applies_to_every_strategy():
    t = trader.settings(cfg(bankroll=10))                                 # 20% -> $2.00 per event
    rows = [{"strategy": "x", "event": "E1", "ticker": "T0", "side": "no", "price": "0.80",
             "count": 2, "filled": 2, "status": "filled"}]                   # $1.60 on E1
    it = trader.Intent("anything", "T1", "E1", "no", 0.80, 1, CLOSE)
    assert trader.vet(it, rows, t, {}, NOW, [], "paper") == "event open-$ cap"
    assert trader.vet(it, rows, t, {"max_event_dollars": 50}, NOW, [], "paper") == "event open-$ cap"


def _book(yb, ya):
    return {"yes_bid_dollars": f"{yb:.2f}", "yes_ask_dollars": f"{ya:.2f}", "result": ""}


def test_exit_holds_through_normal_swings():
    r = {"side": "no", "price": "0.85"}
    # NO fell from 85c to ~60c: no panic sale
    assert trader.exit_decision(r, _book(0.38, 0.42), {}, cap_blocked=False) is None
    # NO rose to 95c: still hold, the bid never beats mid + edge
    assert trader.exit_decision(r, _book(0.04, 0.06), {}, cap_blocked=False) is None
    # 98c+ but the cap is not blocking anything: hold to settle
    assert trader.exit_decision(r, _book(0.01, 0.02), {}, cap_blocked=False) is None


class _Books:
    def __init__(self, books):
        self.books = books

    def get_json(self, url, params=None, headers=None):
        tks = (params or {}).get("tickers", "").split(",") if params else [url.rsplit("/", 1)[1]]
        ms = [dict(self.books[t], ticker=t) for t in tks if t in self.books]
        return {"markets": ms} if params else {"market": ms[0] if ms else {}}


def _held(tk, price, n=2, strat="mentions"):
    return {"id": f"{tk}|no", "client_id": f"c-{tk}", "mode": "paper", "strategy": strat, "ticker": tk,
            "event": tk, "side": "no", "price": f"{price:.2f}", "count": n, "filled": n,
            "status": "filled", "title": tk, "note": ""}


def test_98c_sale_only_frees_what_a_blocked_order_needs():
    """Alex (Nov 7): no 98c 'free money' sale unless a real order is waiting for the cash."""
    T = trader.settings(cfg(bankroll=100)) | {"max_open_dollars": 5.0, "paper_max_open_dollars": 5.0}
    rows = [_held("A", 0.90), _held("B", 0.95), _held("C", 0.50)]          # $3.70 open
    books = _Books({"A": _book(0.01, 0.02), "B": _book(0.01, 0.02), "C": _book(0.40, 0.45)})
    it = trader.Intent("favorites", "NEW", "NEW", "yes", 0.80, 2, CLOSE, "new", "")  # $1.60 -> $5.30
    sold = trader.free_cap_for(books, it, "overall open-$ cap", "paper", rows, [], T, {}, NOW, None, cfg(), False)
    assert len(sold) == 1                                   # one 98c position is enough, not both
    assert sum(r["status"] == "settled" for r in rows) == 1 and rows[2]["status"] == "filled"
    assert trader.exposure(rows) + 1.60 <= 5.0 + 1e-9


def test_98c_sale_skipped_when_it_would_not_be_enough():
    T = {"max_open_dollars": 5.0, "paper_max_open_dollars": 5.0}
    rows = [_held("A", 0.90, 1), _held("C", 0.50, 6)]                       # $3.90 open
    books = _Books({"A": _book(0.01, 0.02), "C": _book(0.40, 0.45)})
    it = trader.Intent("favorites", "NEW", "NEW", "yes", 0.90, 3, CLOSE, "new", "")  # needs $1.60 freed
    assert trader.free_cap_for(books, it, "overall open-$ cap", "paper", rows, [], T, {}, NOW,
                               None, cfg(), False) == []
    assert all(r["status"] == "filled" for r in rows)


def test_98c_sale_skipped_when_another_cap_still_blocks():
    """Nov 8 01:32 UTC live: two weather wins were sold at 98-99c to free the overall cap
    for a Mentions word that the event cap then refused. Sell only if the order then fits."""
    T = trader.settings(cfg(bankroll=100)) | {"max_open_dollars": 5.0, "paper_max_open_dollars": 5.0,
                                              "max_event_dollars": 2.0}
    rows = [_held("A", 0.90), _held("B", 0.95), _held("C", 0.50)]          # $3.70 open
    rows.append(dict(_held("D", 0.80, 2), event="NEW"))                     # $1.60 already on NEW
    books = _Books({"A": _book(0.01, 0.02), "B": _book(0.01, 0.02), "C": _book(0.40, 0.45),
                    "D": _book(0.40, 0.45)})
    it = trader.Intent("favorites", "NEW2", "NEW", "yes", 0.80, 1, CLOSE, "new", "")
    assert trader.free_cap_for(books, it, "overall open-$ cap", "paper", rows, [], T, {}, NOW,
                               None, cfg(), False) == []
    assert all(r["status"] == "filled" for r in rows)


def test_live_fill_time_is_recorded():
    class Cl:
        def get_order(self, oid):
            return {"status": "resting", "fill_count": 1}
    row = {"id": "T|no", "mode": "live", "strategy": "mentions", "ticker": "T", "side": "no",
           "price": "0.85", "count": 2, "filled": 0, "status": "resting", "order_id": "o1",
           "title": "T", "note": ""}
    trader.sync_live(Cl(), [row], cfg(), None, True, NOW)
    assert row["filled"] == 1 and row["filled_at"] == NOW.isoformat(timespec="seconds")
    later = NOW.replace(hour=16)
    Cl.get_order = lambda self, oid: {"status": "executed", "fill_count": 2}
    trader.sync_live(Cl(), [row], cfg(), None, True, later)
    assert row["filled_at"] == NOW.isoformat(timespec="seconds")      # first fill time kept


def test_no_blanket_98c_sale_in_exits():
    rows = [_held("A", 0.85)]
    assert trader.exits(_Books({"A": _book(0.01, 0.02)}), rows, NOW, {"mentions": {}}, None, cfg(),
                        False, {"paper"}) == 0
    assert rows[0]["status"] == "filled"


def test_exit_sells_when_bid_clearly_beats_hold_value():
    # lopsided book: a YES bid of 70c with nobody offering below 99c
    r = {"side": "yes", "price": "0.50"}
    d = trader.exit_decision(r, _book(0.70, 0.99), {"hold_edge": 0.0}, cap_blocked=False)
    assert d is None                              # 0.70 - fee < mid 0.845: hold
    d = trader.exit_decision(r, _book(0.97, 0.99), {"hold_edge": -0.05}, cap_blocked=False)
    assert d and d[0] == 0.97


def test_paper_exit_books_pnl():
    rows = [_held("A", 0.50)]
    n = trader.exits(_Books({"A": _book(0.01, 0.30)}), rows, NOW, {"mentions": {"hold_edge": -0.2}},
                     None, cfg(), False, set())
    assert n == 1 and rows[0]["status"] == "settled" and rows[0]["result"] == "exit"
    assert float(rows[0]["pnl"]) == pytest.approx(2 * (0.70 - 0.50) - trader._fee(0.70, 2))


def test_capacity_scales_with_market_volume():
    t = trader.settings(cfg())
    it = trader.Intent("mentions", "A", "E", "no", 0.80, 3, CLOSE, volume=5000)
    sp = {"max_market_contracts": 200}
    assert trader.capacity(it, [], t, sp, NOW, [])[0] == 200          # 10% of 5000 = 500, capped 200
    it.volume = 800
    assert trader.capacity(it, [], t, sp, NOW, [])[0] == 80           # 10% of volume
    it.volume = 0
    assert trader.capacity(it, [], t, sp, NOW, [])[0] == 50           # unknown: the floor
    held = [{"ticker": "A", "status": "filled", "count": 45, "strategy": "mentions",
             "placed_at": NOW.isoformat()}]
    room, why = trader.capacity(it, held, t, sp, NOW, [])
    assert room == 5 and "market" in why


def test_weekly_capacity_ceiling():
    t = trader.settings(cfg())
    it = trader.Intent("mentions", "B", "E", "no", 0.80, 3, CLOSE, volume=10_000)
    old = (NOW - timedelta(days=8)).isoformat()
    rows = [{"ticker": f"X{i}", "status": "settled", "count": 10, "strategy": "mentions",
             "placed_at": NOW.isoformat()} for i in range(10)] + \
           [{"ticker": "Y", "status": "settled", "count": 500, "strategy": "mentions", "placed_at": old}]
    room, why = trader.capacity(it, rows, t, {"max_week_contracts": 102}, NOW, [])
    assert room == 2 and "weekly" in why


# ---------------------------------------------------------------- live LOCKs

def lcfg(**kw):
    """Config with LOCKs cleared for real money (as if Alex said go)."""
    c = cfg(**kw)
    c.raw["trading"]["locks"]["live"] = True
    return c


def test_only_approved_strategies_trade_real_money():
    # Alex, Nov 5 7 PM ET: mentions, favorites, macro_fade live; LOCKs paper.
    # Weather day-before was a small real-money test Nov 5-11 and is back on paper.
    c = load_config()
    live = {n for n in trader.strategies()
            if trader.strategy_mode(c.raw["trading"]["mode"], c.raw["trading"].get(n, {})) == "live"}
    # Alex, Nov 11 2:18 PM ET: late-day lows (weather_late) live in its place, 1 contract.
    # Alex, Nov 12 2:34 PM ET: Mentions back to paper (no edge in the audit), Fed/CPI 1 -> 3.
    # Alex, Nov 12 2:38 PM ET ("ok testing with real money"): the two new rules at 1 contract.
    assert live == {"favorites", "macro_fade", "weather_late", "macro_wide", "favorites_wide"}
    assert c.raw["trading"]["macro_wide"]["contracts"] == c.raw["trading"]["favorites_wide"]["contracts"] == 1
    assert c.raw["trading"]["macro_fade"]["contracts"] == 3
    wl = c.raw["trading"]["weather_late"]
    assert wl["contracts"] == 1 and wl["max_open_pct"] <= 0.15 and wl["metrics"] == ["low", "high"] and wl["kinds"] == ["need", "holds"]
    assert c.raw["trading"]["weather"]["max_open_pct"] <= 0.15


def test_rejected_live_order_alerts_once(monkeypatch):
    from wxbot.util import HttpError
    monkeypatch.setenv("NTFY_TOPIC", "t")

    class Refuse(FakeClient):
        def place_limit(self, *a):
            raise HttpError(400, "orders", "bad order")
    http = Http([mkt("a", 0.12, 0.16)])
    trader.run(cfg(mode="live", live=True), now=NOW, http=http, client=Refuse())
    trader.run(cfg(mode="live", live=True), now=NOW + timedelta(minutes=30), http=http, client=Refuse())
    assert sum("rejected" in p["title"] for p in http.posts) == 1
    assert not trader.load()


def test_post_only_cross_is_quiet(monkeypatch):
    from wxbot.util import HttpError
    monkeypatch.setenv("NTFY_TOPIC", "t")

    class Cross(FakeClient):
        def place_limit(self, *a):
            raise HttpError(400, "orders", '{"error":{"code":"invalid_order","details":"post only cross"}}')
    http = Http([mkt("a", 0.12, 0.16)])
    trader.run(cfg(mode="live", live=True), now=NOW, http=http, client=Cross())
    assert not any("rejected" in p["title"] for p in http.posts)
    assert not trader.load()


LOCK_TK = "KXHIGHPHIL-26NOV06-B74.5"


def lock_signal(at=NOW, **kw):
    from wxbot import scanner
    from wxbot.util import write_json
    sig = {"ticker": LOCK_TK, "event": "KXHIGHPHIL-26NOV06", "side": "no", "city": "Philadelphia",
           "metric": "high", "date": "2026-11-06", "bracket": "74° to 75°", "price": 0.64,
           "fair_p": 0.995, "close": CLOSE, "obs_extreme": 77.0, "guaranteed": 77,
           "seen_at": at.isoformat()} | kw
    write_json(scanner.LOCK_SIGNALS, {"at": at.isoformat(), "signals": [sig]})


class LockHttp(Http):
    def __init__(self, yb=0.33, ya=0.35, result=""):
        super().__init__([], {LOCK_TK: result} if result else {})
        self.yb, self.ya = yb, ya

    def get_json(self, url, params=None, headers=None):
        if url.endswith("/markets/" + LOCK_TK):
            return {"market": {"ticker": LOCK_TK, "event_ticker": "KXHIGHPHIL-26NOV06",
                               "status": "active", "close_time": CLOSE, "volume": 4000,
                               "result": self.results.get(LOCK_TK, ""),
                               "yes_bid_dollars": f"{self.yb:.2f}", "yes_ask_dollars": f"{self.ya:.2f}",
                               "no_bid_dollars": f"{1 - self.ya:.2f}", "no_ask_dollars": f"{1 - self.yb:.2f}"}}
        return super().get_json(url, params, headers)


def test_lock_buys_at_the_ask_with_real_money_and_alerts(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    lock_signal()
    cl, http = FakeClient(), LockHttp(yb=0.33, ya=0.35)       # NO ask 67c (not the 64c alert)
    out = trader.run(lcfg(mode="live"), now=NOW, http=http, client=cl)
    assert [b[:4] for b in cl.bought] == [(LOCK_TK, "no", 0.67, 5)]   # Kelly, 10% of $40
    assert not cl.placed                                     # mentions etc. never live
    row = [r for r in trader.load() if r["strategy"] == "locks"][0]
    assert (row["mode"], row["status"], row["filled"], row["fee"]) == ("live", "filled", "5", "0.08")
    assert any("BOUGHT" in p["title"] for p in http.posts)
    assert len(out["placed"]) == 1
    # next scan: same lock is not bought again
    trader.run(lcfg(mode="live"), now=NOW + timedelta(minutes=5), http=http, client=cl)
    assert len(cl.bought) == 1
    # settles NO: +5 x 33c minus the fee
    http.results[LOCK_TK] = "no"
    trader.run(lcfg(mode="live"), now=NOW + timedelta(hours=20), http=http, client=cl)
    row = [r for r in trader.load() if r["strategy"] == "locks"][0]
    assert row["status"] == "settled" and row["pnl"] == "1.57"


def test_lock_skips_when_edge_gone_stale_or_no_cash():
    cl = FakeClient()
    lock_signal()
    trader.run(lcfg(mode="live"), now=NOW, http=LockHttp(yb=0.01, ya=0.02), client=cl)  # NO ask 99c
    assert not cl.bought
    lock_signal(at=NOW - timedelta(minutes=40))               # old scan
    trader.run(lcfg(mode="live"), now=NOW, http=LockHttp(), client=cl)
    assert not cl.bought
    lock_signal()
    cl.cash = 0.50                                           # account can't pay for one
    out = trader.run(lcfg(mode="live"), now=NOW, http=LockHttp(), client=cl)
    assert not cl.bought and any("cash" in why for *_, why in out["skipped"])
    cl.cash = 2.0                                            # shrinks to what the cash covers
    trader.run(lcfg(mode="live"), now=NOW, http=LockHttp(), client=cl)
    assert [b[3] for b in cl.bought] == [2]


def test_lock_unfilled_ioc_is_not_recorded_and_retries():
    cl = FakeClient()
    cl.fill = 0
    lock_signal()
    out = trader.run(lcfg(mode="live"), now=NOW, http=LockHttp(), client=cl)
    assert len(cl.bought) == 1 and not [r for r in trader.load() if r["strategy"] == "locks"]
    assert any("nothing left" in why for *_, why in out["skipped"])
    cl.fill = 99
    lock_signal(at=NOW + timedelta(minutes=5))
    trader.run(lcfg(mode="live"), now=NOW + timedelta(minutes=5), http=LockHttp(), client=cl)
    assert len(cl.bought) == 2 and cl.bought[0][4] != cl.bought[1][4]   # fresh client id


def test_lock_respects_event_cap_and_halt():
    from wxbot.util import write_json
    cl = FakeClient()
    lock_signal()
    c = lcfg(mode="live", bankroll=40)
    c.raw["trading"]["locks"]["max_event_pct"] = 0.10         # $4 on this city-day
    c.raw["strategy"]["max_bet_frac_lock"] = 0.5               # Kelly wants 10 contracts...
    trader.run(c, now=NOW, http=LockHttp(), client=cl)
    assert [b[3] for b in cl.bought] == [5]                    # ...shrunk to fit $4 at 67c
    write_json(trader.STATE, {"halted": {"live": {"at": NOW.isoformat(), "pnl": -10}}})
    cl2 = FakeClient()
    lock_signal(ticker=LOCK_TK)
    trader.save([])
    trader.run(lcfg(mode="live"), now=NOW, http=LockHttp(), client=cl2)
    assert not cl2.bought


def test_taker_only_for_strategies_that_allow_it():
    it = trader.Intent("mentions", "X", "E", "no", 0.8, 1, CLOSE, taker=True)
    t = trader.settings(cfg())
    assert "taker" in trader.vet(it, [], t, {"max_price": 0.9}, NOW, [])


def test_scan_writes_lock_signals_only_when_there_are_any(monkeypatch):
    from wxbot import scanner
    from wxbot.util import read_json
    scanner.save_lock_signals([], NOW)
    assert not scanner.LOCK_SIGNALS.exists()
    scanner.save_lock_signals([{"ticker": "A"}], NOW)
    assert read_json(scanner.LOCK_SIGNALS)["signals"] == [{"ticker": "A"}]
    scanner.save_lock_signals([], NOW + timedelta(minutes=5))   # clears the old lock
    assert read_json(scanner.LOCK_SIGNALS)["signals"] == []


def test_key_check_reads_balance_while_paper(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from wxbot.util import read_json
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    monkeypatch.setenv("KALSHI_API_KEY_ID", "kid")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", pem)

    class H(Http):
        def get_json(self, url, params=None, headers=None):
            if url.endswith("/portfolio/balance"):
                assert headers and headers["KALSHI-ACCESS-KEY"] == "kid"
                return {"balance": 4971}
            return super().get_json(url, params, headers)
    trader.run(cfg(), now=NOW, http=H([]))
    kc = read_json(trader.STATE)["key_check"]
    assert kc["ok"] and kc["balance"] == 49.71
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", "not a key")
    trader.save([])
    trader.run(cfg(), now=NOW + timedelta(hours=7), http=H([]))
    assert read_json(trader.STATE)["key_check"]["ok"] is False


def test_orders_use_kalshi_v2_single_book():
    """Kalshi retired POST /portfolio/orders (HTTP 410, Nov 5 2026). V2 has one
    YES book: bid = buy YES, ask = sell YES (= buy NO), YES price as a string."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()

    class H:
        def __init__(self):
            self.calls = []

        def post_json(self, url, payload, headers=None):
            self.calls.append((url, payload))
            return {"order_id": "o1", "fill_count": "2.00", "remaining_count": "0.00"}
    h = H()
    c = KalshiClient(h, "kid", pem)
    o = c.place_limit("T", "no", 0.85, 2, "cid1")
    url, p = h.calls[-1]
    assert url.endswith("/trade-api/v2/portfolio/events/orders")
    assert p == {"ticker": "T", "client_order_id": "cid1", "side": "ask", "count": "2",
                 "price": "0.15", "time_in_force": "good_till_canceled",
                 "self_trade_prevention_type": "taker_at_cross", "post_only": True}
    assert o["order_id"] == "o1"
    c.place_limit("T", "yes", 0.96, 1, "cid2")
    assert (h.calls[-1][1]["side"], h.calls[-1][1]["price"]) == ("bid", "0.96")
    o = c.buy_now("T", "no", 0.67, 2, "cid3")
    assert h.calls[-1][1]["side"] == "ask" and h.calls[-1][1]["price"] == "0.33"
    assert h.calls[-1][1]["time_in_force"] == "immediate_or_cancel"
    assert order_fill_count(o, 2) == 2
    c.sell_now("T", "no", 0.90, 2, "cid4")                 # sell NO = buy YES at 10c
    p = h.calls[-1][1]
    assert (p["side"], p["price"], p["reduce_only"]) == ("bid", "0.10", True)
    c.sell_now("T", "yes", 0.98, 1, "cid5")
    assert (h.calls[-1][1]["side"], h.calls[-1][1]["price"]) == ("ask", "0.98")


def test_yes_and_no_in_one_market_net_like_kalshi():
    base = {"mode": "live", "strategy": "weather", "ticker": "KXLOWTPHIL-26NOV07-T49", "close": CLOSE}
    rows = [dict(base, id="t|yes", side="yes", price="0.27", count=1, filled=1, status="filled"),
            dict(base, id="t|no", side="no", price="0.55", count=2, filled=2, status="filled")]
    assert trader.net_pairs(rows, NOW) == 1
    netted = [r for r in rows if r.get("result") == "netted"]
    assert len(netted) == 2 and abs(sum(float(r["pnl"]) for r in netted) - 0.18) < 1e-9
    left = [r for r in rows if r["status"] == "filled"]
    assert len(left) == 1 and left[0]["side"] == "no" and left[0]["filled"] == 1
    assert trader.net_pairs(rows, NOW) == 0
    assert abs(trader.realized(rows, mode="live") - 0.18) < 1e-9


def test_per_event_limits_count_open_orders_across_scans():
    # Nov 8 audit: 5 live words on one speech although max_words_per_event = 3,
    # because each 30-minute scan picked its own 3. Open orders now use up slots.
    ms = [mkt("a", 0.12, 0.16), mkt("b", 0.20, 0.24), mkt("c", 0.15, 0.19), mkt("d", 0.11, 0.13)]
    ev = ms[0]["event_ticker"]
    held = {ev: {ms[1]["ticker"], "OLD-WORD"}}             # 2 words already open there
    out = mentions.candidates(ms, {"KXTRUMPMENTION"}, {"contracts": 2}, None, held)
    assert [i.ticker[-1] for i in out] == ["c"]            # 1 slot left; held "b" not repeated
    full = {ev: {"x", "y", "z"}}
    assert mentions.candidates(ms, {"KXTRUMPMENTION"}, {"contracts": 2}, None, full) == []
    # only open orders of this strategy and mode count
    rows = [{"strategy": "mentions", "status": "filled", "mode": "live", "event": ev, "ticker": "x"},
            {"strategy": "mentions", "status": "settled", "mode": "live", "event": ev, "ticker": "y"},
            {"strategy": "mentions", "status": "resting", "mode": "paper", "event": ev, "ticker": "z"},
            {"strategy": "favorites", "status": "filled", "mode": "live", "event": ev, "ticker": "w"}]
    assert trader.held_by_event(rows, "mentions", "live") == {ev: {"x"}}
    # favorites: 2 markets per event, one already held
    evs = [fav_event([fav_mkt("a", 0.88, 0.91), fav_mkt("b", 0.05, 0.08)])]
    fev = evs[0]["event_ticker"]
    out = favorites.candidates(evs, NOW, {"contracts": 2}, {fev: {"KXOTHER"}})
    assert len(out) == 1


def test_per_event_pick_handles_event_with_no_picks():
    # Nov 8: an event with no market in the band made favorites crash every scan
    assert trader.per_event_pick([], {}, 2) == []
    assert trader.per_event_pick([], None, 2) == []
    evs = [fav_event([fav_mkt("c", 0.50, 0.55, vol=2_000_000)]),  # no favorite in band
           fav_event([fav_mkt("a", 0.88, 0.91, vol=2_000_000, ev="KXB")], ev="KXB")]
    assert len(favorites.candidates(evs, NOW, {"contracts": 2}, {})) == 1


def test_retried_post_adopts_existing_order():
    """Nov 8: a POST timed out after reaching Kalshi; the HTTP retry got 409
    order_already_exists and the bot lost track of a bid that later filled a 3rd
    contract. The client now looks the order up by client_order_id and returns it."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from wxbot.util import HttpError
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()

    class H:
        def __init__(self, orders):
            self.orders = orders

        def post_json(self, url, payload, headers=None):
            raise HttpError(409, url, '{"error":{"code":"order_already_exists"}}')

        def get_json(self, url, params=None, headers=None):
            assert params["ticker"] == "T"
            return {"orders": self.orders}
    c = KalshiClient(H([{"order_id": "x", "client_order_id": "other"},
                        {"order_id": "o9", "client_order_id": "cid1", "fill_count": "0.00"}]), "kid", pem)
    assert c.place_limit("T", "no", 0.85, 1, "cid1")["order_id"] == "o9"
    c = KalshiClient(H([]), "kid", pem)                       # not found: still a reject
    with pytest.raises(HttpError):
        c.place_limit("T", "no", 0.85, 1, "cid1")


def test_kill_switch_cancels_resting_live_orders_and_books_fills(monkeypatch):
    class Cl(FakeClient):
        def cancel(self, oid, ticker=""):
            self.cancelled.append(oid)
    cl = Cl()
    http = Http([mkt("a", 0.12, 0.16), mkt("b", 0.12, 0.16)])
    out = trader.run(cfg(mode="live", live=True), now=NOW, http=http, client=cl)
    assert len(out["placed"]) == 2
    cl.orders["o1"] = {"status": "executed", "fill_count": 2}      # filled before the switch was read
    monkeypatch.setenv("TRADING_MODE", "off")
    out = trader.run(cfg(mode="live", live=True), now=NOW + timedelta(minutes=5), http=http, client=cl)
    rows = {r["order_id"]: r["status"] for r in trader.load()}
    assert rows == {"o1": "filled", "o2": "cancelled"} and cl.cancelled == ["o2"]
    assert "KILL SWITCH" in out["stops"][0]
    # next run: nothing left to cancel, no second alert
    out = trader.run(cfg(mode="live", live=True), now=NOW + timedelta(minutes=10), http=http, client=cl)
    assert not out["stops"] and cl.cancelled == ["o2"]


def test_mentions_wide_only_logs_extra_words_on_paper():
    from wxbot.strats import mentions_wide
    c = cfg()
    live, wide = c.raw["trading"]["mentions"], c.raw["trading"]["mentions_wide"]
    assert wide["live"] is False
    ms = [mkt(w, 0.12, 0.14) for w in "ABCDE"] + [mkt("THIN", 0.12, 0.14, vol=300),
                                                 mkt("TINY", 0.12, 0.14, vol=50)]
    series = {"KXTRUMPMENTION"}
    live_pick = {i.ticker for i in mentions.candidates(ms, series, live, 40.0, None)}
    out = mentions_wide.extra(ms, series, wide, live, 40.0, None, None)
    tks = {i.ticker for i in out}
    assert len(live_pick) == 3 and not (tks & live_pick)
    assert "KXTRUMPMENTION-26NOV06-THIN" in tks and "KXTRUMPMENTION-26NOV06-TINY" not in tks
    assert len(tks) == 3 and all(i.strategy == "mentions_wide" and i.count == 1 for i in out)


def test_mentions_wide_paper_mode_even_when_trading_live():
    c = cfg(mode="live")
    sp = trader.strategy_settings(c, "mentions_wide", trader.settings(c))
    assert trader.strategy_mode("live", sp) == "paper"


def test_favorites_wide_logs_decision_date_markets_on_paper():
    from wxbot.strats import favorites, favorites_wide
    c = cfg()
    live, wide = c.raw["trading"]["favorites"], c.raw["trading"]["favorites_wide"]
    far = "2030-11-05T15:00:00Z"
    soon = (NOW + timedelta(days=3)).isoformat().replace("+00:00", "Z")

    def m(tk, close, exp=None, yb=0.90, ya=0.93):
        d = {"ticker": tk, "event_ticker": tk.rsplit("-", 1)[0], "close_time": close,
             "yes_bid_dollars": f"{yb:.2f}", "yes_ask_dollars": f"{ya:.2f}", "volume": 900_000,
             "title": tk}
        if exp:
            d["expected_expiration_time"] = exp
        return d
    events = [
        {"event_ticker": "KXGOV-A", "category": "Elections",
         "markets": [m("KXGOV-A-X", far, soon), m("KXGOV-A-Y", far, soon, 0.04, 0.06)]},
        {"event_ticker": "KXRT-B", "category": "Entertainment",
         "markets": [m("KXRT-B-X", soon, soon), m("KXRT-B-Y", soon, soon, 0.05, 0.07)]},
        {"event_ticker": "KXGOV-C", "category": "Elections",
         "markets": [m("KXGOV-C-X", far, far), m("KXGOV-C-Y", far, None, 0.05, 0.07)]},
    ]
    live_pick = {i.ticker for i in favorites.candidates(events, NOW, live, None)}
    out = favorites_wide.extra(events, NOW, wide, live, None, None)
    assert {i.ticker for i in out} == {"KXGOV-A-X", "KXGOV-A-Y"}
    assert not ({i.ticker for i in out} & live_pick)
    assert all(i.strategy == "favorites_wide" and i.count == 1 and i.close == far for i in out)
    sides = {i.ticker: (i.side, i.price) for i in out}
    assert sides["KXGOV-A-X"] == ("yes", 0.91) and sides["KXGOV-A-Y"] == ("no", 0.95)


def test_macro_wide_is_paper_and_relabels_macro_fade_picks():
    from wxbot.strats import macro_fade, macro_wide
    c = cfg(mode="live")
    close = (NOW + timedelta(days=3)).isoformat().replace("+00:00", "Z")
    ms = [{"ticker": f"KXJOBLESSCLAIMS-26NOV19-{k}", "event_ticker": "KXJOBLESSCLAIMS-26NOV19",
           "close_time": close, "yes_bid_dollars": f"{yb:.2f}", "yes_ask_dollars": f"{ya:.2f}",
           "volume": 2000} for k, yb, ya in (("A", 0.10, 0.14), ("B", 0.50, 0.55))]
    p = c.raw["trading"]["macro_wide"]
    base = macro_fade.candidates(ms, p, NOW)
    out = macro_wide.candidates(ms, p, NOW)
    assert [i.ticker for i in out] == [i.ticker for i in base] == ["KXJOBLESSCLAIMS-26NOV19-A"]
    assert out[0].strategy == "macro_wide" and out[0].price == 0.87 and out[0].count == 1
