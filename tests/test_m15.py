import csv
import json
from datetime import datetime, timezone

import pytest

from wxbot import kalshi, m15
from wxbot.kalshi_api import OrderRejected
from wxbot.m15_feeds import KalshiFeed, SpotFeed
from wxbot.util import load_config

T0 = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc).timestamp()
CLOSE = T0 + 600
TK = "KXBTC15M-26NOV091015-15"


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def cfg(**kw):
    c = load_config()
    c.raw["m15"].update({"series": ["KXBTC15M"], "guard": False, "contracts": 1, "extra_contracts": 0})
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


def test_fast_pull_cancels_live_bid_on_jump(monkeypatch):
    World(monkeypatch)
    clk = Clock()
    cl = Client()

    class Spot:
        blocked_now = False

        def blocked(self, prod, now=None):
            return self.blocked_now

        def healthy(self):
            return True

        def move_bps(self, prod, now=None):
            return 0.0
    sp = Spot()
    b = m15.Bot(cfg(mode="live", guard=True), http=None, client=cl, clock=clk, spot=sp,
                fetch_json=lambda url: {"mode": "live", "halted": {}} if "trader_state" in url else {"total": 50.0})
    b.step()
    assert len(cl.placed) == 1
    sp.blocked_now = True
    assert b.fast_pull(clk.t) == 1                 # cancelled without a full step
    assert cl.cancelled == ["o0"] and b.state["markets"][TK]["pulls"] == 1
    assert b.fast_pull(clk.t) == 0


def test_series_cut_after_100_losing_fills(monkeypatch):
    World(monkeypatch)
    clk = Clock()
    cl = Client()
    b = bot(cfg(mode="live"), clk, cl)
    b.state["series"] = {"KXBTC15M": {"fills": 99, "pnl": -5.0}}
    m = {"series": "KXBTC15M", "filled": 1, "close": clk.t}
    b.series_check(m, -0.81)
    assert "KXBTC15M" in b.state["cut"]
    b.step()
    assert cl.placed == [] and b.state["markets"][TK]["mode"] == "paper"


def test_series_not_cut_when_ok():
    c = cfg(mode="live")
    b = m15.Bot(c, http=None, clock=Clock(), fetch_json=lambda url: {})
    b.state["series"] = {"KXBTC15M": {"fills": 99, "pnl": 2.0}}
    b.series_check({"series": "KXBTC15M", "filled": 1, "close": T0}, -0.81)
    assert b.state.get("cut") == {}


def test_market_seen_in_paper_goes_live_when_switch_flips(monkeypatch):
    World(monkeypatch, yb=0.50, ya=0.52)           # coin flip: no quote yet
    clk = Clock()
    cl = Client()
    sw = {"mode": "paper", "halted": {}}
    b = bot(cfg(mode="live"), clk, cl, sw)
    b.step()
    assert b.state["markets"][TK]["mode"] == "paper"
    sw["mode"] = "live"
    clk.t += 61
    b.step()
    assert b.state["markets"][TK]["mode"] == "live"


def test_cut_series_and_reset_survive_restart(monkeypatch):
    World(monkeypatch)
    clk = Clock()
    b = bot(cfg(mode="live"), clk, Client())
    b.state["cut"] = {"KXBTC15M": {"fills": 100}}
    b.state["series"] = {"KXBTC15M": {"fills": 100, "pnl": -6.0}}
    b.state["reset_seen"] = "r1"
    b.save()
    b2 = bot(cfg(mode="live"), clk, Client())
    assert b2.state["cut"] == {"KXBTC15M": {"fills": 100}}
    assert b2.state["series"]["KXBTC15M"]["fills"] == 100 and b2.state["reset_seen"] == "r1"


def test_rejected_live_order_is_reported(monkeypatch):
    World(monkeypatch)
    clk = Clock()

    class Bad(Client):
        def place_limit(self, *a):
            raise OrderRejected("post only cross")
    b = bot(cfg(mode="live"), clk, Bad())
    b.step()
    assert "rejected" in b.state["markets"][TK]["why"]
    assert b.state["last_reject"]["ticker"] == TK


def test_post_only_cross_does_not_trigger_reject_alert(monkeypatch):
    World(monkeypatch)
    clk = Clock()
    alerts = []
    monkeypatch.setattr(m15.Bot, "alert", lambda self, t, body, p=3: alerts.append(t))

    class Racy(Client):
        def place_limit(self, *a):
            raise OrderRejected("invalid order: post only cross")
    b = bot(cfg(mode="live"), clk, Racy())
    for _ in range(8):
        b.step()
        clk.t += 4
    assert not [a for a in alerts if "rejected" in a]

    class Bad(Client):
        def place_limit(self, *a):
            raise OrderRejected("insufficient_shard_balance")
    b.client = Bad()
    for _ in range(8):
        b.step()
        clk.t += 4
    assert [a for a in alerts if "rejected" in a]


def test_funds_crypto_shard_up_to_pool(monkeypatch):
    World(monkeypatch)
    clk = Clock()

    class Sharded(Client):
        def __init__(self):
            super().__init__()
            self.moves = []
            self.sh = {0: 27.77, 2: 0.0}

        def shard_balances(self):
            return dict(self.sh)

        def shard_transfer(self, d, src, dst):
            self.moves.append((round(d, 2), src, dst))
            self.sh[src] -= d
            self.sh[dst] += d
            return {"transfer_id": "t1"}
    cl = Sharded()
    b = bot(cfg(mode="live"), clk, cl)
    b.step()
    assert cl.moves == [(12.5, 0, 2)]              # 25% of $50
    assert len(cl.placed) == 1
    clk.t += 400
    b.step()
    assert len(cl.moves) == 1                      # already funded


def test_shard_transfer_units():
    from wxbot.kalshi_api import KalshiClient
    sent = {}
    c = KalshiClient.__new__(KalshiClient)
    c._post = lambda path, payload: sent.update(payload) or {"transfer_id": "x"}
    c.shard_transfer(13.0, 0, 2)
    assert sent["amount"] == 130000 and sent["destination_exchange_shard"] == 2


def test_silver_waits_for_last_five_minutes(monkeypatch):
    World(monkeypatch)
    monkeypatch.setattr(kalshi, "list_open_markets", lambda http, s: [
        {"ticker": "KXSILVER15M-X", "close_time": iso(CLOSE)}])
    monkeypatch.setattr(kalshi, "get_markets", lambda http, tks: {
        "KXSILVER15M-X": {"ticker": "KXSILVER15M-X", "yes_bid_dollars": "0.80", "yes_ask_dollars": "0.82", "result": ""}})
    clk = Clock()
    cl = Client()
    b = bot(cfg(mode="live", series=["KXSILVER15M"], live_series=["KXSILVER15M"]), clk, cl)
    b.step()                                      # 10 min left: wait
    assert cl.placed == []
    clk.t = CLOSE - 290
    b.step()
    assert len(cl.placed) == 1


def test_started_push_only_when_not_a_routine_code_update(monkeypatch):
    sent = []
    clock = Clock()
    monkeypatch.setattr(m15.Bot, "alert", lambda self, t, b, p=3: sent.append(t))
    ver = {"v": "aaa"}
    monkeypatch.setattr(m15.Bot, "code_version", staticmethod(lambda: ver["v"]))
    b = bot(cfg(), clock)
    b.state.update(code_version=None, saved_at=None)
    b.startup_notice()                       # first start: push
    assert sent == ["15-min bot started"]
    b.save()
    ver["v"] = "bbb"
    clock.t += 60
    b2 = bot(cfg(), clock)
    b2.startup_notice()                      # updater restart a minute later: quiet
    assert len(sent) == 1
    b2.save()
    clock.t += 30
    bot(cfg(), clock).startup_notice()       # same code restarting = crash: push
    assert len(sent) == 2
    ver["v"] = "ccc"
    clock.t += 3600
    bot(cfg(), clock).startup_notice()       # new code after an hour down: push
    assert len(sent) == 3


def test_extra_contract_only_in_its_band():
    s = {"contracts": 1, "extra_contracts": 1, "extra_min": 0.80, "extra_max": 0.87}
    assert m15.target_contracts(0.73, s) == 1
    assert m15.target_contracts(0.80, s) == 2
    assert m15.target_contracts(0.87, s) == 2
    assert m15.target_contracts(0.90, s) == 1
    assert m15.target_contracts(None, s) == 1
    assert m15.target_contracts(0.82, dict(s, extra_contracts=0)) == 1


def test_second_contract_when_favourite_reaches_extra_band(monkeypatch):
    w = World(monkeypatch, yb=0.73, ya=0.75)
    clk = Clock()
    cl = Client()
    b = bot(cfg(mode="live", extra_contracts=1), clk, cl)
    b.step()
    assert cl.placed == [(TK, "yes", 0.73, 1)]               # 73c: just the base contract
    cl.orders["o0"].update(fill_count=1, remaining_count=0, status="executed")
    clk.t += 10
    b.step()
    assert len(cl.placed) == 1                                 # filled, 73c: nothing more
    w.book.update(yes_bid=0.82, yes_ask=0.84)
    clk.t += 10
    b.step()
    assert cl.placed[-1] == (TK, "yes", 0.82, 1)               # 82c: one more, max 2 total
    cl.orders["o1"].update(fill_count=1, remaining_count=0, status="executed")
    clk.t += 10
    b.step()
    assert b.state["markets"][TK]["filled"] == 2 and len(cl.placed) == 2


def test_edge_decay_alert_then_rolling_cut(monkeypatch):
    alerts = []
    monkeypatch.setattr(m15.Bot, "alert", lambda self, t, body, p=3: alerts.append(t))
    b = m15.Bot(cfg(mode="live"), http=None, clock=Clock(), fetch_json=lambda url: {})
    b.state["series"] = {"KXBTC15M": {"fills": 1000, "pnl": 60.0}}     # great all-time record
    for _ in range(300):                                               # then flat: ~0c/contract
        b.series_check({"series": "KXBTC15M", "filled": 1, "close": T0}, 0.0)
    assert any("edge fading" in a for a in alerts) and b.state["cut"] == {}
    assert sum(r[0] for r in b.state["series"]["KXBTC15M"]["recent"]) == 300
    for _ in range(300):                                               # then losing -10c
        b.series_check({"series": "KXBTC15M", "filled": 1, "close": T0}, -0.10)
    assert "KXBTC15M" in b.state["cut"]                                 # all-time still +
    assert b.state["series"]["KXBTC15M"]["pnl"] > 0


def test_partial_contract_fill_is_kept_and_not_rebought(monkeypatch):
    """Nov 9: Kalshi filled half a contract; int() read it as 0, the bot re-bid, and the
    silver window ended with 2.5 contracts. Fractions are now kept and count toward the max."""
    w = World(monkeypatch, yb=0.73, ya=0.76)
    clk = Clock()
    cl = Client()
    b = bot(cfg(mode="live", extra_contracts=0), clk, cl)
    b.step()
    assert cl.placed == [(TK, "yes", 0.73, 1)]
    cl.orders["o0"].update(fill_count="0.50", remaining_count="0.50")
    w.book.update(yes_bid=0.74, yes_ask=0.76)                 # favourite firms: requote
    clk.t += 10
    b.step()
    m = b.state["markets"][TK]
    assert m["filled"] == 0.5                                  # kept, not truncated to 0
    assert len(cl.placed) == 1                                 # 0.5 left: no whole contract to add
    assert m15.qty_str(0.5) == 0.5 and m15.qty_str(2.0) == 2
