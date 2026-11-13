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
    c.raw["m15"].update({"series": ["KXBTC15M"], "guard": False, "contracts": 1, "extra_contracts": 0,
                         "early_on": False, "e2_series": []})   # tests turn rule E / E2 on themselves
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
    assert float(rows[0]["pnl"]) == pytest.approx(0.20)    # no fee: Kalshi charges none here
    assert rows[0]["fills"] == "1@0.80"                           # each fill's own price
    assert b.state["pnl"]["paper_total"] == pytest.approx(0.20)


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
    assert m["graded"] and m["pnl"] == pytest.approx(-0.80)
    assert b.state["pnl"]["live_total"] == pytest.approx(-0.80)


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
    b = bot(cfg(mode="live", daily_loss_pct=0.10, total_loss_pct=0.125, rolling_stop=False), clk, cl)
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

        def healthy(self, prod=None):
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

        def healthy(self, prod=None):
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
    s1 = dict(s, single_series=["KXWTI15M"])
    assert m15.target_contracts(0.82, s1, "KXWTI15M") == 1
    assert m15.target_contracts(0.82, s1, "KXBTC15M") == 2


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


def test_daily_stop_guard_uses_room_left_today(monkeypatch):
    """Nov 9 (Alex): only hold back when everything open losing at once would reach the
    daily stop. Room = 10% of bankroll + today's live P&L."""
    World(monkeypatch, yb=0.73, ya=0.75)
    clk = Clock()
    cl = Client()
    b = bot(cfg(mode="live", pool_pct=1.0, stop_guard=True, rolling_stop=False, daily_loss_pct=0.10), clk, cl)
    b.refresh_switch(clk.t)
    b.paper_bankroll = 10.0                    # stop = $1.00 of room on a flat day
    b.state["pnl"]["live_days"] = {b.day(clk.t): -0.40}   # down 40c: 60c left < 73c order
    b.step()
    assert cl.placed == [] and "daily-stop guard" in b.state["markets"][TK]["why"]
    b.state["pnl"]["live_days"] = {b.day(clk.t): 0.50}    # up 50c: $1.50 of room
    clk.t += 10
    b.step()
    assert cl.placed == [(TK, "yes", 0.73, 1)]


def test_refused_retry_adopts_order_that_was_placed(monkeypatch):
    """Nov 9: WTI held 4 contracts (max 2). A POST refused on retry ("post only cross") may
    already have placed the order: the bot must find and track it, not place another."""
    World(monkeypatch, yb=0.73, ya=0.76)
    clk = Clock()

    class Retry(Client):
        def place_limit(self, ticker, side, price, count, cid):
            super().place_limit(ticker, side, price, count, cid)
            self.orders["o0"]["client_order_id"] = cid
            raise OrderRejected("invalid order: post only cross")

        def find_order(self, ticker, cid):
            return next((o for o in self.orders.values() if o.get("client_order_id") == cid), {})
    cl = Retry()
    b = bot(cfg(mode="live", extra_contracts=0), clk, cl)
    b.step()
    assert b.state["markets"][TK]["order"]["id"] == "o0"
    clk.t += 10
    b.step()
    assert len(cl.placed) == 1


def test_untracked_resting_order_is_cancelled_and_booked(monkeypatch):
    World(monkeypatch, yb=0.73, ya=0.76)
    clk = Clock()
    cl = Client()
    b = bot(cfg(mode="live", extra_contracts=0), clk, cl)
    b.step()
    cl.orders["ghost"] = {"order_id": "ghost", "ticker": TK, "status": "resting",
                          "fill_count": "1", "remaining_count": "1"}
    b.sync_live(force=True)
    assert "ghost" in cl.cancelled
    assert b.state["markets"][TK]["filled"] == 1 and b.state["orphans"] == 1
    assert "o0" not in cl.cancelled                            # the tracked order is left alone


def test_failed_cancel_never_stacks_a_second_order(monkeypatch):
    w = World(monkeypatch, yb=0.73, ya=0.76)
    clk = Clock()

    class Stuck(Client):
        def cancel(self, oid, ticker=""):
            raise m15.HttpError(503, "x", "busy")
    cl = Stuck()
    b = bot(cfg(mode="live", extra_contracts=0), clk, cl)
    b.step()
    w.book.update(yes_bid=0.75, yes_ask=0.77)                 # favourite firms: would requote
    clk.t += 10
    b.step()
    assert len(cl.placed) == 1 and b.state["markets"][TK]["order"]["id"] == "o0"


def test_swept_order_that_was_already_booked_is_not_booked_twice(monkeypatch):
    # an order the bot ended (and booked 1 of) turns up resting later: only new fills count
    World(monkeypatch, yb=0.73, ya=0.76)
    clk = Clock()
    cl = Client()
    b = bot(cfg(mode="live", extra_contracts=0), clk, cl)
    b.step()
    m = b.state["markets"][TK]
    m["filled"], m["cost"] = 1, 0.74
    m["ended"] = {"old": 1}
    cl.orders["old"] = {"order_id": "old", "ticker": TK, "status": "resting",
                        "fill_count": "1", "remaining_count": "1"}
    b.sync_live(force=True)
    assert "old" in cl.cancelled and m["filled"] == 1


def test_cancel_failure_with_unknown_status_keeps_tracking(monkeypatch):
    w = World(monkeypatch, yb=0.73, ya=0.76)
    clk = Clock()

    class Odd(Client):
        def cancel(self, oid, ticker=""):
            raise m15.HttpError(503, "x", "busy")

        def get_order(self, oid):
            o = dict(super().get_order(oid))
            o["status"] = "pending"
            return o
    cl = Odd()
    b = bot(cfg(mode="live", extra_contracts=0), clk, cl)
    b.step()
    w.book.update(yes_bid=0.75, yes_ask=0.77)
    clk.t += 10
    b.step()
    assert len(cl.placed) == 1 and b.state["markets"][TK]["order"]["id"] == "o0"


def test_group_cap_stops_later_metals_in_the_same_window(monkeypatch):
    World(monkeypatch, yb=0.73, ya=0.76)
    clk = Clock()
    b = bot(cfg(mode="live", group=["KXSILVER15M", "KXCOPPER15M", "KXPLATINUM15M"], group_cap=1),
            clk, Client())
    mk = lambda tk, se, f: {"ticker": tk, "series": se, "close": 100, "mode": "live", "filled": f}
    b.state["markets"] = {"s": mk("s", "KXSILVER15M", 1), "c": mk("c", "KXCOPPER15M", 0),
                          "b": mk("b", "KXBTC15M", 0)}
    assert b.group_full(b.state["markets"]["c"])           # silver filled first: copper sits out
    assert not b.group_full(b.state["markets"]["s"])       # the filled one keeps its position
    assert not b.group_full(b.state["markets"]["b"])       # BTC isn't in the group
    off = bot(cfg(mode="live", group_cap=0), clk, Client())
    off.state["markets"] = b.state["markets"]
    assert not off.group_full(off.state["markets"]["c"])   # cap 0 = off


def test_oanda_prices_feed_the_jump_guard_and_links_trip_together():
    from wxbot.m15_feeds import OandaFeed, Spots
    f = OandaFeed("tok", ["XAG_USD", "XCU_USD"], None, 10, 4.0, 4.0, 20)
    t = 1000.0
    f.handle(json.dumps({"type": "HEARTBEAT"}))
    for i in range(80):                                   # quiet market builds the baseline
        px = 30.0 + (0.0003 if i % 2 else 0)
        f.handle(json.dumps({"type": "PRICE", "instrument": "XAG_USD",
                             "bids": [{"price": str(px)}], "asks": [{"price": str(px + 0.01)}]}),
                 now=t + i)
    assert not f.blocked("XAG_USD", t + 80)
    f.handle(json.dumps({"type": "PRICE", "instrument": "XAG_USD", "bids": [{"price": "30.3"}],
                         "asks": [{"price": "30.31"}]}), now=t + 81)   # +1% jump
    assert f.blocked("XAG_USD", t + 81)
    sp = Spots([f], [["XAG_USD", "XCU_USD"]])
    assert sp.blocked("XCU_USD", t + 81)                   # copper pulled with silver
    assert not sp.blocked("BTC-USD", t + 81) and sp.healthy("BTC-USD")   # no feed: unguarded


def test_oanda_with_no_instruments_never_blocks_trading(monkeypatch):
    from wxbot.m15_feeds import OandaFeed, Spots
    import requests

    class R:
        def __init__(self, js): self.js = js
        def raise_for_status(self): pass
        def json(self): return self.js
    monkeypatch.setattr(requests, "get", lambda url, **kw: R(
        {"accounts": [{"id": "a"}]} if url.endswith("/accounts") else {"instruments": []}))
    f = OandaFeed("tok", ["XAG_USD"], None)
    f._session()
    assert f.stop and f.products == []
    sp = Spots([f])
    assert sp.healthy("XAG_USD") and not sp.blocked("XAG_USD")


def test_rolling_24h_stop(monkeypatch):
    World(monkeypatch)
    clk = Clock()
    cl = Client()
    b = bot(cfg(mode="live", daily_loss_pct=0.10, total_loss_pct=0.9, rolling_stop=True), clk, cl)
    # a big loss 20h ago (yesterday by the ET clock) still counts
    b.state["pnl"] = {"live_total": -5.5, "live_days": {}, "live_log": [[clk.t - 20 * 3600, -5.5]]}
    b.step()
    assert b.state["roll_stop"] and cl.placed == []
    assert b.effective_mode(clk.t) == "paper"
    # 5 hours later the loss is older than 24h -> resumes by itself
    clk.t += 5 * 3600
    b.check_stops(clk.t)
    assert not b.state["roll_stop"]
    assert b.state["pnl"]["live_log"] == []


def test_kalshi_feed_ignores_repeated_fill():
    f = KalshiFeed(client=None)
    msg = json.dumps({"type": "fill", "msg": {"order_id": "o1", "trade_id": "t1", "count": 1}})
    f.handle(msg)
    f.handle(msg)                                 # re-sent after a reconnect
    assert f.fills["o1"] == 1


def test_socket_double_count_does_not_drop_or_overbook(monkeypatch):
    World(monkeypatch)
    clk = Clock()
    cl = Client()
    b = bot(cfg(mode="live"), clk, cl)
    b.refresh_switch(clk.t)
    m = {"ticker": TK, "series": "KXBTC15M", "mode": "live", "filled": 0, "cost": 0.0, "fee": 0.0,
         "order": {"side": "yes", "q": 0.8, "n": 2, "filled": 0, "t0": clk.t, "id": "o1"}}
    b.state["markets"] = {TK: m}

    class Feed:
        fills = {"o1": 2}                         # socket says 2 (one fill counted twice)
        def forget(self, tk):
            pass
    b.kfeed = Feed()
    cl.resting_orders = lambda: {"o1": {"order_id": "o1", "ticker": TK, "status": "resting",
                                        "fill_count_fp": "1.00", "remaining_count_fp": "1.00"}}
    b.sync_live(force=True)
    assert m["order"] is not None                 # Kalshi still shows it resting: keep tracking
    # later it is cancelled with only 1 really filled
    cl.resting_orders = lambda: {}
    cl.get_order = lambda oid: {"order_id": oid, "status": "canceled", "fill_count_fp": "1.00"}
    b.sync_live(force=True)
    assert m["order"] is None and m["filled"] == 1
    assert abs(m["cost"] - 0.8) < 1e-9


def test_record_upgrades_an_old_header(monkeypatch):
    """A day file written before the "fills" column keeps its rows and gains the column."""
    World(monkeypatch)
    clk = Clock()
    b = bot(cfg(mode="paper"), clk)
    path = m15.M15_DIR / f"markets-{b.day(CLOSE)}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    old = [c for c in m15.COLS if c != "fills"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=old)
        w.writeheader()
        w.writerow({c: ("OLD-1" if c == "ticker" else "") for c in old})
    m = {"ticker": TK, "series": "KXBTC15M", "mode": "paper", "close": CLOSE, "quotes": 1,
         "filled": 2, "cost": 1.62, "fee": 0.02, "fills": ["1@0.77", "1@0.85"]}
    b.record(m)
    rows = list(csv.DictReader(open(path)))
    assert list(rows[0].keys()) == m15.COLS
    assert rows[0]["ticker"] == "OLD-1" and rows[0]["fills"] == ""
    assert rows[1]["ticker"] == TK and rows[1]["fills"] == "1@0.77;1@0.85"



class FillClient(Client):
    """Client that also answers Kalshi's fills and positions endpoints."""
    def __init__(self):
        super().__init__()
        self.kfills, self.held = [], 0.0

    def fills(self, ticker=None, min_ts=None, max_pages=50):
        return [f for f in self.kfills if ticker is None or f["ticker"] == ticker]

    def position(self, ticker):
        return self.held


def test_fill_totals_reads_kalshi_fills():
    fs = [{"ticker": TK, "side": "no", "count_fp": "2.00", "no_price_dollars": "0.8300", "fee_cost": "0.0000"},
          {"ticker": TK, "side": "no", "count": 2, "no_price": 82},
          {"ticker": TK, "side": "yes", "count": 1, "yes_price_dollars": "0.9", "fee_cost": "0.01"}]
    n, cost, fee = m15.kalshi_api.fill_totals(fs)[TK]
    assert n == 5 and cost == pytest.approx(1.66 + 1.64 + 0.90) and fee == pytest.approx(0.01)


def test_grading_uses_kalshi_fills(monkeypatch):
    w = World(monkeypatch)
    clk = Clock()
    cl = FillClient()
    b = bot(cfg(mode="live"), clk, cl)
    b.step()
    cl.orders["o0"].update(fill_count=1, remaining_count=0, status="executed")
    clk.t += 10
    b.step()
    m = b.state["markets"][TK]
    assert m["filled"] == 1
    # Kalshi really filled 2 (a fill the bot never saw)
    cl.kfills = [{"ticker": TK, "side": "yes", "count": 1, "yes_price_dollars": "0.80"},
                 {"ticker": TK, "side": "yes", "count": 1, "yes_price_dollars": "0.81"}]
    w.result = "yes"
    clk.t = CLOSE + 30
    b.step()
    assert m["filled"] == 2 and m["pnl"] == pytest.approx(2 - 1.61)
    assert b.state["trued"] == 1


def test_position_check_books_unseen_fill(monkeypatch):
    World(monkeypatch, yb=0.82, ya=0.84)
    clk = Clock()
    cl = FillClient()
    b = bot(cfg(mode="live"), clk, cl)
    cl.held = 2.0                                  # Kalshi says we already hold 2
    b.step()
    m = b.state["markets"][TK]
    assert cl.placed == [] and m["filled"] == 2 and m["why"] == "caught unbooked fill"


def test_history_true_up_rewrites_csv_and_state(monkeypatch):
    World(monkeypatch)
    clk = Clock()
    cl = FillClient()
    b = bot(cfg(mode="live"), clk, cl)
    path = m15.M15_DIR / "markets-2026-11-09.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {k: "" for k in m15.COLS}
    row.update(ticker=TK, series="KXBTC15M", mode="live", close=iso(CLOSE), fav_side="yes",
               quotes=1, filled=2, cost=1.60, fee=0.03, result="no", won=0, pnl=-1.63)
    with open(path, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=m15.COLS)
        wr.writeheader()
        wr.writerow(row)
    b.state["pnl"]["live_total"] = -1.63
    cl.kfills = [{"ticker": TK, "side": "yes", "count": 1, "yes_price_dollars": "0.80"}]
    sent = {}
    b.uploader = lambda files: sent.update(files)
    b.retrue_history()
    r = list(csv.DictReader(open(path)))[0]
    assert r["filled"] == "1" and float(r["pnl"]) == pytest.approx(-0.80)
    assert b.state["pnl"]["live_total"] == pytest.approx(-0.80)
    assert b.state["series"]["KXBTC15M"]["fills"] == 1
    assert path.name in sent and b.state["retrued"] == 1
    b.retrue_history()                              # runs once only
class QClient(Client):
    """Fake Kalshi that also answers queue-position requests."""

    def __init__(self):
        super().__init__()
        self.queue = {}

    def queue_positions(self, tickers):
        return {"queue_positions": [{"order_id": k, "market_ticker": TK, "queue_position": v}
                                    for k, v in self.queue.items()]}


def fill_rows(b, ts):
    path = m15.M15_DIR / f"fills-{b.day(ts)}.csv"
    return list(csv.DictReader(open(path))) if path.exists() else []


def test_fill_quality_log_moves_and_queue(monkeypatch):
    w = World(monkeypatch)
    clk = Clock()
    cl = QClient()
    b = bot(cfg(mode="live"), clk, cl)
    cl.queue = {"o0": 7}
    b.step()                                            # place at 80c, 7 contracts ahead
    assert cl.placed == [(TK, "yes", 0.80, 1)]
    clk.t += 6
    cl.queue = {"o0": 2}
    b.step()
    cl.orders["o0"].update(fill_count=1, remaining_count=0, status="executed")
    w.book = {"yes_bid": 0.78, "yes_ask": 0.80}         # filled as the bid dropped
    clk.t += 6
    b.step()
    fill_t = clk.t
    assert len(b.state["fillq"]) == 1
    for dt, book in ((10, (0.76, 0.78)), (60, (0.83, 0.85)), (300, (0.90, 0.92))):
        w.book = {"yes_bid": book[0], "yes_ask": book[1]}
        clk.t = fill_t + dt
        b.step()
    assert fill_rows(b, fill_t) == []                    # waits for the settlement
    w.result = "yes"
    clk.t = CLOSE + 30
    b.step()
    rows = fill_rows(b, fill_t)
    assert len(rows) == 1 and b.state["fillq"] == []
    r = rows[0]
    assert r["price"] == "0.8" and r["nth"] == "1" and r["bid"] == "0.8" and r["spread"] == "2"  # last book seen at the fill
    assert r["queue_first"] == "7.0" and r["queue_last"] == "2.0"
    assert float(r["move_10s"]) == pytest.approx(-3.0)   # mid 77c vs our 80c
    assert float(r["move_60s"]) == pytest.approx(4.0)
    assert float(r["move_300s"]) == pytest.approx(11.0)
    assert r["settled_h"] == "" and r["won"] == "1" and float(r["pnl_ct"]) == pytest.approx(20.0)
    assert r["rest_s"] == "12.0" and int(r["ttc_s"]) == int(CLOSE - fill_t) and r["src"] == "order"


def test_fill_quality_late_fill_uses_settlement_and_no_queue_endpoint(monkeypatch):
    w = World(monkeypatch, yb=0.88, ya=0.90)
    clk = Clock(CLOSE - 100)
    cl = Client()                                       # no queue endpoint: logging still works
    b = bot(cfg(mode="live"), clk, cl)
    b.step()
    cl.orders["o0"].update(fill_count=1, remaining_count=0, status="executed")
    clk.t += 5
    b.step()
    fill_t = clk.t
    clk.t = fill_t + 10
    b.step()
    w.result = "no"
    clk.t = CLOSE + 30
    b.step()
    r = fill_rows(b, fill_t)[0]
    assert r["settled_h"] == "300" and r["queue_first"] == "" and r["move_60s"] == ""  # 60s mark missed
    assert float(r["move_300s"]) == pytest.approx(-88.0) and r["won"] == "0"


def test_fill_quality_skips_paper(monkeypatch):
    w = World(monkeypatch)
    clk = Clock()
    b = bot(cfg(mode="paper"), clk)
    b.step()
    clk.t += 30
    w.tape = [(clk.t - 5, 0.78, 10, "no")]
    b.step()
    assert b.state["markets"][TK]["filled"] == 1 and b.state["fillq"] == []


def test_fill_quality_logs_fair_value_when_shadow_present(monkeypatch):
    w = World(monkeypatch)
    clk = Clock()
    cl = Client()
    b = bot(cfg(mode="live"), clk, cl)

    class FV:
        def fair_value(self, m, now):
            return {"fv_yes": 0.83, "index": 101234.5, "venues": 3, "sigma": 0.001}
    b.fv = FV()
    b.step()
    cl.orders["o0"].update(fill_count=1, remaining_count=0, status="executed")
    clk.t += 6
    b.step()
    fill_t = clk.t
    w.result = "yes"
    clk.t = CLOSE + 30
    b.step()
    r = fill_rows(b, fill_t)[0]
    assert r["fv_yes"] == "0.83" and r["index_px"] == "101234.5"


class FakeFV:
    """Stand-in for m15_fv.FairShadow with a settable fair value for YES."""

    def __init__(self, y):
        self.y = y

    def fair_value(self, m, now):
        return None if self.y is None else {"fv_yes": self.y, "fv_cf": None, "index": 1, "venues": 3,
                                            "sigma": 1e-4}

    def step(self, bot, now):
        pass

    def files(self, now):
        return {}


def fv_bot(monkeypatch, y, **kw):
    World(monkeypatch)                       # favourite = YES, bid 0.80
    clk = Clock()
    b = m15.Bot(cfg(mode="paper", **kw), http=None, clock=clk, fetch_json=lambda url: {},
                fv=FakeFV(y))
    return b, clk


def test_fv_filter_off_by_default(monkeypatch):
    b, _ = fv_bot(monkeypatch, 0.50)        # model hates it, but the filter is off
    b.step()
    assert b.state["markets"][TK]["order"]


def test_fv_filter_skips_and_pulls_when_fair_below_bid(monkeypatch):
    b, clk = fv_bot(monkeypatch, 0.90, fv_skip=True)
    b.step()
    m = b.state["markets"][TK]
    assert m["order"]                        # fair 0.90 >= bid 0.80: bid as usual
    b.fv.y = 0.70
    clk.t += 5
    b.step()
    assert m["order"] is None and m["why"] == "fair value below bid" and m["fv_skips"] >= 1
    b.fv.y = 0.85
    clk.t += 5
    b.step()
    assert m["order"]                        # back above: bids again


def test_fv_filter_fails_open_and_respects_scope(monkeypatch):
    b, _ = fv_bot(monkeypatch, None, fv_skip=True)         # no fair value -> as before
    b.step()
    assert b.state["markets"][TK]["order"]
    b2, _ = fv_bot(monkeypatch, 0.5, fv_skip=True, fv_skip_series=["KXETH15M"])
    b2.step()
    assert b2.state["markets"][TK]["order"]                # BTC not in scope
    b3, clk = fv_bot(monkeypatch, 0.5, fv_skip=True, fv_skip_min_left_s=900)
    b3.step()
    assert b3.state["markets"][TK]["order"]                # 600 s left < min_left: too late to trust
    b4, _ = fv_bot(monkeypatch, 0.78, fv_skip=True, fv_skip_margin=0.03)
    b4.step()
    assert b4.state["markets"][TK]["order"]                # 0.78 within the 3c margin of 0.80


def test_fv_filter_switches_on_by_itself_at_n_contracts(monkeypatch):
    b, clk = fv_bot(monkeypatch, 0.50, fv_skip=False, fv_skip_after_contracts=500,
                    early_after_contracts=0)         # rule E has its own switch-on test
    alerts = []
    monkeypatch.setattr(b, "alert", lambda t, body, p=3: alerts.append(t))
    b.state["series"] = {"KXBTC15M": {"fills": 499, "pnl": 0.0}}
    b.step()
    m = b.state["markets"][TK]
    assert m["order"] and not alerts                 # 499 real contracts: still off
    b.state["series"]["KXBTC15M"]["fills"] = 500
    clk.t += 5
    b.step()
    assert m["order"] is None and m["why"] == "fair value below bid"
    assert b.state["fv_skip_on"]["KXBTC15M"] and len(alerts) == 1
    clk.t += 5
    b.step()
    assert len(alerts) == 1                          # alerts once


def early_bot(monkeypatch, yb=0.82, ya=0.84, **kw):
    w = World(monkeypatch, yb, ya)
    clk = Clock(CLOSE - 800)                         # 13:20 left
    cl = Client()
    kw.setdefault("e2_series", [])                  # plain rule E unless a test turns E2 on
    b = bot(cfg(mode="live", live_series=["KXBTC15M"], **kw), clk, cl)
    return b, clk, cl, w


def test_early_window_off_by_default_waits_for_last_ten_minutes(monkeypatch):
    b, clk, cl, _ = early_bot(monkeypatch)
    b.step()
    assert cl.placed == []                           # today's rule: BTC bids only in the last 10 min


def test_early_window_bids_14_to_10_min_left_80_95(monkeypatch):
    b, clk, cl, w = early_bot(monkeypatch, early_on=True, extra_contracts=1, early_contracts=0)
    b.step()
    assert len(cl.placed) == 1 and cl.placed[0][3] == 1      # 1 contract, no extra at 82c
    m = b.state["markets"][TK]
    clk.t = CLOSE - 590                              # past 10 min left: pull, never bid again
    b.step()
    assert m["order"] is None and len(cl.placed) == 1


def test_early_window_skips_window_without_80c_favourite(monkeypatch):
    b, clk, cl, w = early_bot(monkeypatch, yb=0.75, ya=0.77, early_on=True)
    b.step()
    assert cl.placed == []                           # 75c: below the 80c floor
    w.book.update(yes_bid=0.85, yes_ask=0.87)
    clk.t = CLOSE - 500                              # too late for rule E
    b.step()
    assert cl.placed == []


def test_early_window_switches_on_by_itself_at_n_contracts(monkeypatch):
    b, clk, cl, _ = early_bot(monkeypatch, early_after_contracts=500)
    alerts = []
    monkeypatch.setattr(b, "alert", lambda t, body, p=3: alerts.append(t))
    b.state["series"] = {"KXBTC15M": {"fills": 499, "pnl": 0.0}}
    b.step()
    assert cl.placed == [] and not alerts            # 499: still today's rule
    b.state["series"]["KXBTC15M"]["fills"] = 500
    clk.t += 5
    b.step()
    assert len(cl.placed) == 1 and b.state["early_on"]["KXBTC15M"] and len(alerts) == 1
    clk.t += 5
    b.step()
    assert len(alerts) == 1


def test_early_window_contracts_setting(monkeypatch):
    b, clk, cl, w = early_bot(monkeypatch, early_on=True, early_contracts=3, extra_contracts=1)
    b.step()
    assert len(cl.placed) == 1 and cl.placed[0][3] == 3


def test_series_floor_skips_bids_below_it(monkeypatch):
    w = World(monkeypatch, yb=0.76, ya=0.78)
    clk = Clock()
    b = bot(cfg(mode="paper", series_min_fav={"KXBTC15M": 0.80}), clk)
    b.step()
    m = b.state["markets"][TK]
    assert m.get("order") is None and m["why"] == "out of band"
    w.book = {"yes_bid": 0.81, "yes_ask": 0.83}
    clk.t += 10
    b.step()
    assert m["order"]["q"] == 0.81


def test_e2_rule_helpers():
    assert m15.e2_ok("yes", -50.0) and not m15.e2_ok("yes", 50.0)
    assert m15.e2_ok("no", 12.5) and not m15.e2_ok("no", -12.5)
    assert not m15.e2_ok("yes", None) and not m15.e2_ok("no", 0.0)
    # 2:30 PM ET Nov 13, 2026 = 19:30 UTC
    assert m15.window_ticker("KXBTC15M", 1794598200) == "KXBTC15M-26NOV131430-30"


def test_e2_bids_only_against_last_hours_move(monkeypatch):
    b, clk, cl, w = early_bot(monkeypatch, early_on=True, early_contracts=1, e2_series=["KXBTC15M"])
    monkeypatch.setattr(b, "hour_move", lambda m: 120.0)       # index rose; YES favourite = with it
    b.step()
    m = b.state["markets"][TK]
    assert cl.placed == [] and m["why"] == "E2: with the trend" and m["rule"] == "E2" and m["e2_skip"]
    monkeypatch.setattr(b, "hour_move", lambda m: None)
    clk.t += 5
    b.step()
    assert cl.placed == [] and m["why"] == "E2: last hour unknown"
    monkeypatch.setattr(b, "hour_move", lambda m: -80.0)       # index fell; YES favourite = against
    clk.t += 5
    b.step()
    assert len(cl.placed) == 1 and cl.placed[0][3] == 1


def test_e2_hour_move_from_strike_history(monkeypatch):
    b, clk, cl, w = early_bot(monkeypatch, early_on=True)
    m = {"ticker": TK, "series": "KXBTC15M", "close": CLOSE, "strike": 100500.0}
    b.state["strikes"] = {"KXBTC15M": {str(int(CLOSE) - 3600): 100000.0}}
    assert b.hour_move(m) == 500.0
    calls = []
    monkeypatch.setattr(m15.kalshi, "get_market", lambda http, t: calls.append(t) or {"floor_strike": 99000})
    b.state["strikes"] = {}
    assert b.hour_move(m) == 1500.0 and calls == [m15.window_ticker("KXBTC15M", int(CLOSE) - 3600)]


def test_e2_money_cap_moves_series_to_paper(monkeypatch):
    b, clk, cl, w = early_bot(monkeypatch, early_on=True, e2_stop_usd=8.0, e2_loss_seed=-6.5)
    alerts = []
    monkeypatch.setattr(b, "alert", lambda t, body, p=3: alerts.append((t, p)))
    m = {"series": "KXBTC15M", "rule": "E2", "close": CLOSE, "filled": 1}
    b.e2_check(m, 0.20)
    assert "KXBTC15M" not in b.state.get("cut", {}) and not alerts
    b.e2_check(m, -0.85)
    b.e2_check(m, -0.85)                             # -6.5 + 0.2 - 1.7 = -8.0
    assert b.state["cut"]["KXBTC15M"]["why"] == "E2 money cap" and alerts[0][1] == 4
    b.e2_check({**m, "rule": ""}, -5)                # non-E2 windows don't count
    assert b.state["e2_live"]["n"] == 3 and len(alerts) == 1


def test_e2_session_cap_counts_from_zero_per_tag(monkeypatch):
    b, clk, cl, w = early_bot(monkeypatch, early_on=True, e2_stop_usd=8.0, e2_loss_seed=-4.0,
                              e2_session="t1", e2_session_stop_usd=2.5)
    alerts = []
    monkeypatch.setattr(b, "alert", lambda t, body, p=3: alerts.append((t, p)))
    m = {"series": "KXBTC15M", "rule": "E2", "close": CLOSE, "filled": 1}
    b.e2_check(m, -0.85)
    b.e2_check(m, 0.20)
    assert "KXBTC15M" not in b.state.get("cut", {})
    b.e2_check(m, -0.85)
    b.e2_check(m, -0.85)                             # session -2.35: still on
    assert "KXBTC15M" not in b.state.get("cut", {})
    b.e2_check(m, -0.20)                             # session -2.55 -> paper
    assert b.state["cut"]["KXBTC15M"]["why"] == "E2 session cap" and alerts[0][1] == 4
    assert b.state["e2_session"]["n"] == 5 and b.state["e2_session"]["won"] == 1
