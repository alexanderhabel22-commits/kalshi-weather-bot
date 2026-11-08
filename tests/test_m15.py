import csv
import json
from datetime import datetime, timezone

import pytest

from wxbot import kalshi, m15
from wxbot.m15_feeds import KalshiFeed, SpotFeed
from wxbot.util import load_config

T0 = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc).timestamp()
CLOSE = T0 + 600
TK = "KXBTC15M-26NOV091015-15"


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def cfg(**kw):
    c = load_config()
    c.raw["m15"].update({"series": ["KXBTC15M"], "guard": False, "contracts": 1})
    c.raw["m15"].update(kw)
    return c


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class World:
    """Fake Kalshi: one open BTC market with a settable book, tape and result."""

    def __init__(self, monkeypatch, yb=0.80, ya=0.82):
        self.book = {"yes_bid": yb, "yes_ask": ya}
        self.tape = []
        self.result = ""
        monkeypatch.setattr(kalshi, "list_open_markets", lambda http, s: [
            {"ticker": TK, "close_time": iso(CLOSE)}] if self.result == "" else [])
        monkeypatch.setattr(kalshi, "get_markets", lambda http, tks: {
            TK: {"ticker": TK, "yes_bid_dollars": str(self.book["yes_bid"]),
                 "yes_ask_dollars": str(self.book["yes_ask"]), "result": self.result}})
        monkeypatch.setattr(m15.Bot, "trades", lambda bot, m: list(self.tape))


class Client:
    def __init__(self):
        self.placed, self.cancelled, self.orders = [], [], {}
        self.bal = 50.0

    def place_limit(self, ticker, side, price, count, cid):
        oid = f"o{len(self.placed)}"
        self.placed.append((ticker, side, price, count))
        self.orders[oid] = {"order_id": oid, "ticker": ticker, "status": "resting",
                            "fill_count": 0, "remaining_count": count}
        return {"order_id": oid}

    def cancel(self, oid, ticker=""):
        self.cancelled.append(oid)
        if oid in self.orders:
            self.orders[oid]["status"] = "canceled"

    def get_order(self, oid):
        return self.orders[oid]

    def resting_orders(self):
        return {k: v for k, v in self.orders.items() if v["status"] == "resting"}

    def balance(self):
        return self.bal


def bot(c, clock, client=None, switch=None):
    switch = switch if switch is not None else {"mode": "live", "halted": {}}
    return m15.Bot(c, http=None, client=client, clock=clock,
                   fetch_json=lambda url: switch if "trader_state" in url else {"total": 50.0})


def test_desired_quote_joins_favourite_in_band():
    s = m15.settings(cfg())
    assert m15.desired_quote({"yes_bid_dollars": "0.80", "yes_ask_dollars": "0.82"}, s) == ("yes", 0.80)
    # NO favourite: YES ask 15c -> best NO bid 85c
    assert m15.desired_quote({"yes_bid_dollars": "0.13", "yes_ask_dollars": "0.15"}, s) == ("no", 0.85)
    assert m15.desired_quote({"yes_bid_dollars": "0.50", "yes_ask_dollars": "0.52"}, s) is None
    assert m15.desired_quote({"yes_bid_dollars": "0.97", "yes_ask_dollars": "0.98"}, s) is None


def test_back_of_queue_needs_a_trade_through_our_price():
    # resting YES bid at 80c: a NO taker at 20c only touches it, at 21c goes through
    tr = [(T0 + 1, 0.80, 5, "no"), (T0 + 2, 0.80, 5, "yes")]
    assert m15.through(tr, "yes", 0.80, T0) == (None, True)
    tr.append((T0 + 3, 0.79, 5, "no"))
    assert m15.through(tr, "yes", 0.80, T0)[0] == T0 + 3
    # trades before we joined don't count
    assert m15.through(tr, "yes", 0.80, T0 + 5) == (None, False)


def test_paper_fill_and_grade(monkeypatch):
    w = World(monkeypatch)
    clk = Clock()
    b = bot(cfg(mode="paper"), clk)
    b.step()
    m = b.state["markets"][TK]
    assert m["mode"] == "paper" and m["order"]["side"] == "yes" and m["order"]["q"] == 0.80
    clk.t += 30
    w.tape = [(clk.t - 5, 0.78, 10, "no")]           # NO taker paid 22c: through our 80c
    b.step()
    assert m["filled"] == 1 and m["order"] is None and m["cost"] == 0.80
    clk.t += 10
    b.step()
    assert m["order"] is None                         # 1 contract per market: done
    clk.t = CLOSE + 30
    w.result = "yes"
    b.step()
    path = m15.M15_DIR / f"markets-{b.day(CLOSE)}.csv"
    rows = list(csv.DictReader(open(path)))
    assert len(rows) == 1 and rows[0]["won"] == "1" and rows[0]["mode"] == "paper"
    assert float(rows[0]["pnl"]) == pytest.approx(0.20 - 0.01)    # maker fee rounds up to 1c
    assert b.state["pnl"]["paper_total"] == pytest.approx(0.19)


def test_requotes_up_when_favourite_firms(monkeypatch):
    w = World(monkeypatch)
    clk = Clock()
    b = bot(cfg(mode="paper"), clk)
    b.step()
    w.book = {"yes_bid": 0.86, "yes_ask": 0.88}
    clk.t += 10
    b.step()
    m = b.state["markets"][TK]
    assert m["order"]["q"] == 0.86 and m["quotes"] == 2 and m["first_q"] == 0.80


def test_live_order_kill_switch_and_pool(monkeypatch):
    w = World(monkeypatch)
    clk = Clock()
    cl = Client()
    sw = {"mode": "live", "halted": {}}
    b = bot(cfg(mode="live"), clk, cl, sw)
    b.step()
    assert cl.placed == [(TK, "yes", 0.80, 1)]
    # Kalshi reports the fill
    cl.orders["o0"].update(fill_count=1, remaining_count=0, status="executed")
    clk.t += 10
    b.step()
    m = b.state["markets"][TK]
    assert m["filled"] == 1 and m["mode"] == "live"
    assert b.exposure("live") == pytest.approx(0.80)
    # kill switch: TRADING_MODE=off -> no live orders
    sw["mode"] = "off"
    clk.t += 120
    assert b.effective_mode(clk.t) == "live"
    b.refresh_switch(clk.t)
    assert b.effective_mode(clk.t) == "off"
    w.result = "no"
    clk.t = CLOSE + 30
    b.step()
    assert m["graded"] and m["pnl"] == pytest.approx(-0.81)
    assert b.state["pnl"]["live_total"] == pytest.approx(-0.81)


def test_live_cancels_on_switch_to_paper(monkeypatch):
    World(monkeypatch)
    clk = Clock()
    cl = Client()
    sw = {"mode": "live", "halted": {}}
    b = bot(cfg(mode="live"), clk, cl, sw)
    b.step()
    assert len(cl.placed) == 1
    sw["mode"] = "paper"
    clk.t += 61
    b.step()
    assert cl.cancelled == ["o0"]
    assert b.state["markets"][TK]["order"] is None


def test_pool_cap_blocks_live(monkeypatch):
    World(monkeypatch)
    clk = Clock()
    cl = Client()
    b = bot(cfg(mode="live"), clk, cl)
    b.refresh_switch(clk.t)
    b.paper_bankroll = 2.0          # whole account $2: pool 25% = 50c < 80c order
    b.step()
    assert cl.placed == []


def test_daily_stop(monkeypatch):
    World(monkeypatch)
    clk = Clock()
    cl = Client()
    b = bot(cfg(mode="live"), clk, cl)
    b.state["pnl"] = {"live_total": -5.5, "live_days": {b.day(clk.t): -5.5}}
    b.step()
    assert b.state["day_stop"] == b.day(clk.t)
    assert cl.placed == []                        # paused for the day (paper only)


def test_live_needs_fresh_kill_switch(monkeypatch):
    World(monkeypatch)
    clk = Clock()
    cl = Client()

    def broken(url):
        raise OSError("github down")
    b = m15.Bot(cfg(mode="live"), http=None, client=cl, clock=clk, fetch_json=broken)
    b.step()
    assert cl.placed == []                        # never read the switch -> no real orders


def test_guard_pulls_orders(monkeypatch):
    World(monkeypatch)
    clk = Clock()

    class Spot:
        blocked_now = False

        def blocked(self, prod, now=None):
            return self.blocked_now

        def healthy(self):
            return True

        def move_bps(self, prod, now=None):
            return 0.0
    sp = Spot()
    b = m15.Bot(cfg(mode="paper", guard=True), http=None, clock=clk, spot=sp,
                fetch_json=lambda url: {})
    b.step()
    m = b.state["markets"][TK]
    assert m["order"]
    sp.blocked_now = True
    clk.t += 5
    b.step()
    assert m["order"] is None and m["pulls"] == 1
    sp.blocked_now = False
    clk.t += 5
    b.step()
    assert m["order"]


def test_spot_guard_trips_on_jump():
    g = SpotFeed(["BTC-USD"], None, window_s=10, z=4, min_bps=4, cooldown_s=20)
    t, p = T0, 100000.0
    for i in range(200):                         # calm: +-0.5 bp wiggle each second
        g.on_price("BTC-USD", p * (1 + (0.00005 if i % 2 else -0.00005)), t + i)
    assert not g.blocked("BTC-USD", t + 199)
    g.on_price("BTC-USD", p * 1.002, t + 200)    # +20 bps in a second
    assert g.blocked("BTC-USD", t + 201) and g.trips == 1
    assert not g.blocked("BTC-USD", t + 230)


def test_kalshi_feed_parses_messages():
    f = KalshiFeed(client=None)
    f.tickers.add(TK)
    f.handle(json.dumps({"type": "ticker", "msg": {"market_ticker": TK, "yes_bid": 80, "yes_ask": 82}}))
    assert f.quote(TK)["yes_bid"] == 0.80
    f.handle(json.dumps({"type": "trade", "msg": {"market_ticker": TK, "yes_price_dollars": "0.7800",
                                                   "count_fp": "3.00", "taker_side": "no", "ts": T0}}))
    assert f.tape(TK, T0 - 1) == [(T0, 0.78, 3.0, "no")]
    f.handle(json.dumps({"type": "fill", "msg": {"order_id": "o1", "count": 1}}))
    assert f.fills["o1"] == 1
    f.handle("not json")                          # ignored


def test_trader_records_mode_for_server(monkeypatch):
    from wxbot import trader
    from wxbot.util import read_json
    c = load_config()
    monkeypatch.setenv("TRADING_MODE", "off")
    trader.run(c, now=datetime.fromtimestamp(T0, timezone.utc), http=None)
    assert read_json(trader.STATE, {})["mode"] == "off"


def test_control_file_stops_this_bot(monkeypatch):
    World(monkeypatch)
    clk = Clock()
    cl = Client()
    ctl = {"mode": "live"}

    def fetch(url):
        if "m15_control" in url:
            return ctl
        return {"mode": "live", "halted": {}} if "trader_state" in url else {"total": 50.0}
    b = m15.Bot(cfg(mode="live"), http=None, client=cl, clock=clk, fetch_json=fetch)
    b.step()
    assert len(cl.placed) == 1
    ctl["mode"] = "off"
    clk.t += 61
    b.step()
    assert cl.cancelled == ["o0"] and b.effective_mode(clk.t) == "off"
