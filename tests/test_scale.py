"""Scale plumbing (Nov 7): batched reads, monthly archives, bundled alerts."""
from datetime import datetime, timedelta, timezone

import pytest

from wxbot import kalshi, trader
from wxbot.util import load_config

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    for k in ("TRADING_MODE", "BANKROLL", "KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY", "NTFY_TOPIC"):
        monkeypatch.delenv(k, raising=False)


def cfg(mode="paper", bankroll=40.0):
    c = load_config()
    c.raw["account"]["bankroll"] = bankroll
    c.raw["trading"]["mode"] = mode
    for name in ("mentions", "favorites", "macro_fade", "locks", "weather"):
        c.raw["trading"][name]["live"] = False
        c.raw["trading"][name]["enabled"] = False
    return c


class Http:
    """Fake Kalshi: counts calls; /markets honours tickers=..."""

    def __init__(self, results=None, trades=(), batch_ok=True):
        self.results, self.trades, self.batch_ok = results or {}, list(trades), batch_ok
        self.calls, self.posts = [], []

    def get_json(self, url, params=None, headers=None):
        params = params or {}
        if url.endswith("/markets") and "tickers" in params:
            self.calls.append(("batch", len(params["tickers"].split(","))))
            if not self.batch_ok:
                raise kalshi.HttpError(500, url, "")
            return {"markets": [{"ticker": t, "result": self.results.get(t, "")}
                                for t in params["tickers"].split(",")]}
        if url.endswith("/markets/trades"):
            self.calls.append(("trades", params["ticker"], params["min_ts"]))
            return {"trades": [{"yes_price_dollars": f"{p:.2f}"} for p in self.trades], "cursor": ""}
        if "/markets/" in url:
            tk = url.rsplit("/", 1)[1]
            self.calls.append(("one", tk))
            return {"market": {"ticker": tk, "result": self.results.get(tk, "")}}
        if url.endswith("/events") or url.endswith("/series") or url.endswith("/markets"):
            return {"events": [], "series": [], "markets": [], "cursor": ""}
        raise AssertionError(url)

    def post_json(self, url, payload, headers=None):
        self.posts.append(payload)
        return {}


def row(i, status="resting", mode="paper", placed=NOW - timedelta(hours=2), close=NOW + timedelta(hours=5),
        settled=None, pnl="", count=2, strategy="mentions"):
    return {"id": f"T{i}|no", "client_id": f"c{i}", "order_id": f"o{i}", "mode": mode, "strategy": strategy,
            "placed_at": placed.isoformat(), "ticker": f"T{i}", "event": f"E{i}", "title": "", "side": "no",
            "price": "0.80", "count": count, "filled": count if status in ("filled", "settled") else 0,
            "status": status, "close": close.isoformat(), "result": "", "pnl": pnl,
            "settled_at": settled.isoformat() if settled else "", "note": ""}


def test_get_markets_batches_and_falls_back():
    http = Http({"A": "yes"})
    got = kalshi.get_markets(http, [f"T{i}" for i in range(250)] + ["A", "A"])
    assert len(got) == 251 and got["A"]["result"] == "yes"
    assert [c for c in http.calls if c[0] == "batch"] == [("batch", 100), ("batch", 100), ("batch", 51)]
    assert not [c for c in http.calls if c[0] == "one"]
    http = Http({"A": "no"}, batch_ok=False)
    assert kalshi.get_markets(http, ["A", "B"])["A"]["result"] == "no"   # batch down: one by one
    assert [c[0] for c in http.calls] == ["batch", "one", "one"]


def test_settle_reads_markets_once_and_tape_once_per_ticker():
    rows = [row(1), row(2), dict(row(3), id="T1|yes", side="yes", price="0.10")]
    rows[2]["ticker"] = "T1"
    http = Http(trades=[0.15])           # nothing at our prices
    trader.settle(http, rows, NOW, cfg(), True)
    assert [c[0] for c in http.calls].count("batch") == 1
    assert sorted(c[1] for c in http.calls if c[0] == "trades") == ["T1", "T2"]
    assert all(r["checked_at"] for r in rows) and all(r["status"] == "resting" for r in rows)
    # next check starts where the last one stopped (a minute of overlap), not at placement
    http2 = Http(trades=[0.15])
    trader.settle(http2, rows, NOW + timedelta(minutes=30), cfg(), True)
    mins = {c[1]: c[2] for c in http2.calls if c[0] == "trades"}
    assert mins["T2"] == int((NOW - timedelta(minutes=1)).timestamp())


def test_archive_moves_old_finished_rows_and_keeps_totals():
    old = NOW - timedelta(days=5)
    rows = [row(1, "settled", placed=old - timedelta(days=1), close=old, settled=old, pnl="0.40"),
            row(2, "settled", mode="live", placed=old - timedelta(days=1), close=old, settled=old, pnl="-1.60"),
            row(3, "done-unfilled", placed=old, close=old, settled=old),
            row(4, "settled", placed=NOW - timedelta(hours=5), close=NOW - timedelta(hours=1),
                settled=NOW - timedelta(hours=1), pnl="0.20"),             # too recent: stays hot
            row(5, "cancelled", placed=old, close=NOW + timedelta(days=3)),    # market still open: stays
            row(6)]
    hot = trader.archive(rows, NOW)
    assert sorted(r["id"] for r in hot) == ["T4|no", "T5|no", "T6|no"]
    s = trader.archive_summary()
    assert s["months"] == ["2026-11"] and s["counts"]["2026-11"] == 3
    assert trader.archived_realized("paper") == pytest.approx(0.40)
    assert trader.archived_realized("live") == pytest.approx(-1.60)
    # all-time P&L for the loss stops = hot + archive
    trader.save(hot)
    assert len(trader.load_all()) == 6
    # re-running (e.g. after a crash before the hot file was saved) never duplicates
    trader.archive(rows, NOW)
    assert trader.archive_summary()["counts"]["2026-11"] == 3
    # weekly capacity still sees contracts placed this week that are now archived
    assert trader.archived_placed("paper", "mentions", NOW - timedelta(days=7)) == 4


def test_weekly_capacity_counts_archived_orders():
    old = NOW - timedelta(days=3)
    trader.archive([row(i, "settled", placed=old, close=old, settled=old, pnl="0.1", count=40)
                    for i in range(5)], NOW)
    c = cfg()
    t = trader.settings(c)
    sp = {"max_week_contracts": 250}
    it = trader.Intent("mentions", "TX", "EX", "no", 0.8, 3, close=(NOW + timedelta(hours=9)).isoformat(),
                       volume=100000)
    room, why = trader.capacity(it, [], t | {"_mode_hint": "paper"}, sp, NOW, [])
    assert room == 50 and "weekly" in why


def test_alerts_bundle_into_one_push(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    c, http = cfg(), Http()
    trader._BUNDLE = []
    for i in range(3):
        trader._alert(http, c, f"FILLED {i}", "x", False, bundle=True)
    assert trader.flush_alerts(http, c, False) == 3 and len(http.posts) == 3
    http.posts.clear()
    trader._BUNDLE = []
    for i in range(30):
        trader._alert(http, c, f"FILLED {i}", "x", False, bundle=True)
    trader._alert(http, c, "Trading HALTED", "stop", False, 5)     # stops are never held back
    assert http.posts[0]["title"] == "Trading HALTED"
    assert trader.flush_alerts(http, c, False) == 1
    assert http.posts[1]["title"] == "30 trade updates" and "...and 18 more" in http.posts[1]["message"]
    assert trader._BUNDLE is None


class Client:
    def __init__(self, listed, single):
        self.listed, self.single, self.gets = listed, single, []

    def resting_orders(self):
        return self.listed

    def get_order(self, oid):
        self.gets.append(oid)
        return self.single[oid]


def test_sync_live_uses_one_listing():
    rows = [row(i, mode="live") for i in range(6)]
    listed = {f"o{i}": {"status": "resting", "fill_count": 0} for i in range(5)}   # o5 left the list
    listed["o2"]["fill_count"] = 1
    cl = Client(listed, {"o5": {"status": "executed", "fill_count": 2}})
    trader._BUNDLE = []
    trader.sync_live(cl, rows, cfg(), Http(), True)
    trader._BUNDLE = None
    assert cl.gets == ["o5"]
    assert rows[5]["status"] == "filled" and int(rows[2]["filled"]) == 1 and rows[2]["status"] == "resting"


def test_sync_live_listing_failure_falls_back():
    class Bad(Client):
        def resting_orders(self):
            raise kalshi.HttpError(500, "x", "")
    rows = [row(i, mode="live") for i in range(4)]
    cl = Bad({}, {f"o{i}": {"status": "resting", "fill_count": 0} for i in range(4)})
    trader.sync_live(cl, rows, cfg(), Http(), True)
    assert cl.gets == ["o0", "o1", "o2", "o3"]


def test_run_time_budget_stops_placing(monkeypatch):
    c = cfg()
    c.raw["trading"]["run_budget_s"] = -1        # budget already used up
    called = []

    def propose(ctx):
        called.append(1)
        return [trader.Intent("mentions", "TZ", "EZ", "no", 0.8, 1,
                              close=(NOW + timedelta(hours=9)).isoformat(), volume=5000)]
    monkeypatch.setattr(trader, "strategies", lambda: {"mentions": propose})
    c.raw["trading"]["mentions"]["enabled"] = True
    out = trader.run(c, now=NOW, http=Http())
    assert called and not out["placed"] and "budget" in out["skipped"][0][2]
