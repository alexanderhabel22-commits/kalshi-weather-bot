import json

import pytest

from wxbot import lip
from wxbot.kalshi_api import OrderRejected, check_lip_order
from wxbot.lip import CONTROL_URL, SWITCH_URL, LipBot, plan_market, side_stats
from wxbot.taxledger import tag

NOW = 1_794_400_000.0           # Nov 2026
DAY = 86400


def iso(t):
    import datetime as dt
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


PROG = {"market_ticker": "KXA-1", "incentive_type": "liquidity", "period_reward": 1_000_000,  # $100
        "start_date": iso(NOW - DAY), "end_date": iso(NOW + 6 * DAY), "target_size_fp": "1000.00",
        "discount_factor_bps": 5000}
# YES bids are the cheap side (best 2c); NO bids near 95c; both sides >= 1000 contracts
BOOK = {"orderbook_fp": {"yes_dollars": [["0.0100", "900"], ["0.0200", "300"]],
                         "no_dollars": [["0.9300", "800"], ["0.9400", "400"]]}}
MKT = {"ticker": "KXA-1", "status": "active", "close_time": iso(NOW + 200 * DAY), "exchange_index": 0,
       "event_ticker": "KXA"}


class Http:
    def __init__(self, progs=None, books=None, markets=None, cat="Politics"):
        self.progs = progs if progs is not None else [PROG]
        self.books = books or {"KXA-1": BOOK}
        self.mk = markets or {"KXA-1": MKT}
        self.cat = cat

    def get_json(self, url, params=None, headers=None):
        if url.endswith("/incentive_programs"):
            return {"incentive_programs": self.progs}
        if url.endswith("/markets"):
            return {"markets": [self.mk[t] for t in params["tickers"].split(",") if t in self.mk]}
        if "/events/" in url:
            return {"event": {"category": self.cat}}
        if url.endswith("/orderbook"):
            return self.books.get(url.split("/")[-2], {})
        raise AssertionError(url)


class Client:
    def __init__(self, cash=15.0):
        self.cash, self.orders, self.cancelled, self.fills = cash, {}, [], {}

    def cash_on(self, shard):
        return self.cash

    def resting_orders(self):
        return {k: v for k, v in self.orders.items()}

    def place_lip_bid(self, tk, side, px, n, cid):
        check_lip_order(side, px, n)
        oid = f"o{len(self.orders) + len(self.cancelled)}"
        self.orders[oid] = {"ticker": tk, "client_order_id": cid, "side": side, "px": px, "n": n,
                            "fill_count_fp": "0"}
        self.cash -= px * n
        return {"order_id": oid}

    def cancel(self, oid, tk):
        o = self.orders.pop(oid, None)
        if o:
            self.cash += o["px"] * (o["n"] - float(o["fill_count_fp"]))
        self.cancelled.append(oid)

    def get_order(self, oid):
        return {"fill_count_fp": str(self.fills.get(oid, 0)), "status": "canceled"}

    def credit_history(self):
        return [{"credit_id": "c1", "type": "incentive", "amount_dollars": "1.23", "reason": "KXA-1"}]


def mk(tmp_path, control=None, switch=None, client=None, http=None, now=NOW):
    data = {CONTROL_URL: {"mode": "live", **(control or {})}, SWITCH_URL: switch or {"mode": "live"}}
    clock = [now]
    alerts = []
    bot = LipBot(tmp_path, client if client is not None else Client(), http or Http(),
                 fetch_json=lambda u: data[u], clock=lambda: clock[0], sleep=lambda s: None,
                 alert=lambda t, b: alerts.append(t))
    return bot, data, clock, alerts


def test_side_stats_scoring():
    sc, best, ok, ref = side_stats([(0.01, 900), (0.02, 300)], 1000, 0.5)
    assert best == 0.02 and ok and ref == 0.02
    assert sc == pytest.approx(300 + 700 * 0.5)       # only the first 1000 count; 1 tick below ref
    # our own size is taken out first
    sc2, _, _, _ = side_stats([(0.02, 500)], 1000, 0.5, mine=200, my_px=0.02)
    assert sc2 == pytest.approx(300)


def test_plan_front_of_cheap_side():
    from wxbot.kalshi import parse_orderbook
    pl = plan_market(parse_orderbook(BOOK), PROG, dict(lip.DEFAULTS), 2.0)
    assert pl["side"] == "yes" and pl["px"] == 0.03 and pl["n"] == 66
    assert pl["est_day"] > 0
    # too expensive: cheap side's best already at 8c
    b = {"yes": [(0.08, 1200)], "no": [(0.90, 1200)]}
    assert plan_market(b, PROG, dict(lip.DEFAULTS), 2.0) is None
    # neither side deep enough: no reward
    assert plan_market({"yes": [(0.02, 10)], "no": [(0.9, 10)]}, PROG, dict(lip.DEFAULTS), 2.0) is None


def test_lip_order_ceilings():
    check_lip_order("yes", 0.03, 200)
    with pytest.raises(OrderRejected):
        check_lip_order("yes", 0.11, 10)
    with pytest.raises(OrderRejected):
        check_lip_order("yes", 0.05, 200)       # $10 > $6 per order
    with pytest.raises(OrderRejected):
        check_lip_order("yes", 0.01, 501)


def test_places_within_budget_and_reserve(tmp_path):
    bot, _, _, _ = mk(tmp_path, client=Client(cash=12.0))
    bot.cycle()
    o = bot.state["orders"]["KXA-1"]
    # usable = 12 - 6 reserve = $6 over 8 markets -> $0.75 at 3c -> 25 contracts
    assert o["side"] == "yes" and o["px"] == 0.03 and o["n"] == 25
    # usable $0.50 over 8 markets: too small (n < 10) -> no order
    bot2, _, _, _ = mk(tmp_path / "b", client=Client(cash=6.5))
    (tmp_path / "b").mkdir(exist_ok=True)
    bot2.cycle()
    assert bot2.state["orders"] == {}


def test_size_split_and_cost(tmp_path):
    c = Client(cash=30.0)
    bot, _, _, _ = mk(tmp_path, client=c)
    bot.cycle()
    o = bot.state["orders"]["KXA-1"]
    # usable = min(20, 30 - 6) = 20 -> $2.50 per market at 3c -> 83 contracts
    assert o["n"] == 83
    assert bot.resting_cost() == pytest.approx(2.49)
    assert bot.state["cash_check"]["bid_cost"] == pytest.approx(2.49)


def test_kill_switches_cancel(tmp_path):
    c = Client(cash=30.0)
    bot, data, clock, _ = mk(tmp_path, client=c)
    bot.cycle()
    assert c.orders
    data[SWITCH_URL] = {"mode": "off"}
    clock[0] += 300
    bot.cycle()
    assert not c.orders and bot.state["orders"] == {}
    data[SWITCH_URL] = {"mode": "live"}
    data[CONTROL_URL] = {"mode": "off"}
    clock[0] += 300
    bot.cycle()
    assert not c.orders


def test_until_ends_test(tmp_path):
    c = Client(cash=30.0)
    bot, _, _, _ = mk(tmp_path, client=c, control={"until": iso(NOW - 1)})
    bot.cycle()
    assert not c.orders


def test_stale_control_goes_off(tmp_path):
    c = Client(cash=30.0)
    bot, data, clock, _ = mk(tmp_path, client=c)
    bot.cycle()
    assert c.orders

    def boom(u):
        raise OSError("down")
    bot.fetch_json = boom
    clock[0] += lip.CONTROL_STALE_S + 10
    bot.cycle()
    assert not c.orders


def test_fill_is_held_and_fill_stop(tmp_path):
    c = Client(cash=30.0)
    bot, data, clock, alerts = mk(tmp_path, client=c, control={"fill_stop": 1.0})
    bot.cycle()
    oid = bot.state["orders"]["KXA-1"]["id"]
    # the whole bid fills and leaves the resting list
    c.fills[oid] = 83
    c.orders.pop(oid)
    clock[0] += 300
    bot.cycle()
    assert bot.state["positions"]["KXA-1"]["n"] == 83
    assert bot.filled_cost() == pytest.approx(2.49)
    assert bot.state["halted"] and "Rewards test halted" in alerts
    clock[0] += 300
    bot.cycle()
    assert not c.orders                          # halted: nothing new


def test_outbid_requotes_once_cooldown_passes(tmp_path):
    c = Client(cash=30.0)
    http = Http()
    bot, data, clock, _ = mk(tmp_path, client=c, http=http)
    bot.cycle()
    assert bot.state["orders"]["KXA-1"]["px"] == 0.03
    # someone bids 4c, above us
    http.books["KXA-1"] = {"orderbook_fp": {"yes_dollars": [["0.0100", "900"], ["0.0200", "300"],
                                                            ["0.0300", "83"], ["0.0400", "50"]],
                                            "no_dollars": [["0.9300", "800"], ["0.9400", "400"]]}}
    clock[0] += 300
    bot.cycle()
    assert bot.state["orders"]["KXA-1"]["px"] == 0.03     # cooldown (30 min) not passed
    clock[0] += 1800
    bot.cycle()
    assert bot.state["orders"]["KXA-1"]["px"] == 0.05
    assert len(c.orders) == 1


def test_filters_sports_shortdated_and_other_shards(tmp_path):
    for http in (Http(cat="Sports"),
                 Http(markets={"KXA-1": dict(MKT, close_time=iso(NOW + 5 * DAY))}),
                 Http(markets={"KXA-1": dict(MKT, exchange_index=2)}),
                 Http(progs=[dict(PROG, end_date=iso(NOW + 3600))])):
        c = Client(cash=30.0)
        d = tmp_path / str(id(http))
        d.mkdir()
        bot, _, _, _ = mk(d, client=c, http=http)
        bot.cycle()
        assert not c.orders


def test_strays_cancelled_and_credits_logged(tmp_path):
    c = Client(cash=30.0)
    c.orders["x1"] = {"ticker": "KXZ-1", "client_order_id": "lip-old", "px": 0.02, "n": 50,
                      "fill_count_fp": "0"}
    c.orders["m1"] = {"ticker": "KXZ-2", "client_order_id": "wx-main", "px": 0.5, "n": 1,
                      "fill_count_fp": "0"}
    bot, _, _, _ = mk(tmp_path, client=c)
    bot.cycle()
    assert "x1" in c.cancelled and "m1" in c.orders      # never touches other bots' orders
    assert bot.state["credits"]["c1"]["amount_dollars"] == "1.23"
    st = json.loads((tmp_path / "state.json").read_text())
    assert st["orders"]["KXA-1"]["px"] == 0.03


def test_ledger_tags_rewards_bot():
    assert tag({"client_order_id": "lip-abc", "ticker": "KXA-1", "side": "yes"}, set(), set()) == "rewards bot"
