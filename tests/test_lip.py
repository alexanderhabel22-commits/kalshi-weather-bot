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
    # most tests pin the original "front" pricing (1c above the best bid); test_ref_* cover "ref"
    data = {CONTROL_URL: {"mode": "live", "price_mode": "front", **(control or {})},
            SWITCH_URL: switch or {"mode": "live"}}
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
    pl = plan_market(parse_orderbook(BOOK), PROG, dict(lip.DEFAULTS, price_mode="front"), 2.0)
    assert pl["side"] == "yes" and pl["px"] == 0.03 and pl["n"] == 66
    # default "ref": AT the reference price (first 200 = target/5 contracts from the top: 2c)
    pr = plan_market(parse_orderbook(BOOK), PROG, dict(lip.DEFAULTS), 2.0)
    assert pr["px"] == 0.02 and pr["n"] == 100 and pr["mid"] == 0.04
    assert pl["est_day"] > 0
    # too expensive: cheap side's best already at 8c
    b = {"yes": [(0.08, 1200)], "no": [(0.90, 1200)]}
    assert plan_market(b, PROG, dict(lip.DEFAULTS, price_mode="front"), 2.0) is None
    assert plan_market({"yes": [(0.09, 1200)], "no": [(0.90, 1200)]}, PROG, dict(lip.DEFAULTS), 2.0) is None
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


def test_ref_pricing_joins_reference_and_follows_it(tmp_path):
    c = Client(cash=30.0)
    http = Http()
    bot, data, clock, _ = mk(tmp_path, client=c, http=http, control={"price_mode": "ref"})
    bot.cycle()
    o = bot.state["orders"]["KXA-1"]
    assert o["px"] == 0.02 and o["n"] == 125            # $2.50 at 2c (float-safe)
    # someone stacks 500 at 3c: reference moves to 3c, so we'd only get half credit at 2c
    http.books["KXA-1"] = {"orderbook_fp": {"yes_dollars": [["0.0100", "900"], ["0.0200", str(300 + 125)],
                                                            ["0.0300", "500"]],
                                            "no_dollars": [["0.9300", "800"], ["0.9400", "400"]]}}
    clock[0] += 300
    bot.cycle()
    assert bot.state["orders"]["KXA-1"]["px"] == 0.02   # waits ref_requote_s (10 min)
    clock[0] += 600
    bot.cycle()
    assert bot.state["orders"]["KXA-1"]["px"] == 0.03
    assert len(c.orders) == 1


def test_ref_moves_down_when_overpaying_and_reselect(tmp_path):
    c = Client(cash=30.0)
    bot, data, clock, _ = mk(tmp_path, client=c, control={"price_mode": "front"})
    bot.cycle()
    assert bot.state["orders"]["KXA-1"]["px"] == 0.03
    data[CONTROL_URL] = {"mode": "live", "price_mode": "ref", "reselect": "nov11-ref"}
    clock[0] += 700
    bot.cycle()
    assert bot.state["orders"]["KXA-1"]["px"] == 0.02
    assert bot.state["reselect_seen"] == "nov11-ref"
    assert any(e["kind"] == "select" and e["t"] == round(clock[0]) for e in bot.state["events"])


def test_move_guard_pulls_and_pauses(tmp_path):
    c = Client(cash=30.0)
    http = Http()
    bot, data, clock, _ = mk(tmp_path, client=c, http=http, control={"price_mode": "ref"})
    bot.cycle()
    assert bot.state["orders"]["KXA-1"]["mid0"] == 0.04
    # news: the favorite side jumps, the longshot mid drops 3c+
    http.books["KXA-1"] = {"orderbook_fp": {"yes_dollars": [["0.0100", "1300"]],
                                            "no_dollars": [["0.9900", "1200"]]}}
    clock[0] += 300
    bot.cycle()
    assert not c.orders and "KXA-1" not in bot.state["orders"]
    clock[0] += 3600
    bot.cycle()
    assert not c.orders                                  # still paused (6 h)


def test_busy_market_skipped(tmp_path):
    c = Client(cash=30.0)
    bot, _, _, _ = mk(tmp_path, client=c, http=Http(markets={"KXA-1": dict(MKT, volume_24h_fp="5000")}))
    bot.cycle()
    assert not c.orders


# -- daily shadow re-pick (wxbot/lip_rank.py)
from wxbot import lip_rank  # noqa: E402


def _tr(side_long, yp, n, t):
    # taker bought `side_long` (a hit on resting bids of the other side)
    return {"taker_side": side_long, "taker_action": "buy", "yes_price_dollars": str(yp),
            "count_fp": str(n), "created_time": iso(t)}


def test_fill_flow_counts_only_hits_on_our_bid():
    since = NOW - 7 * DAY
    ts = lambda t: lip._ts(t["created_time"])  # noqa: E731
    trades = [_tr("no", 0.02, 70, NOW - DAY),       # sold YES at 2c: reaches a 2c YES bid
              _tr("no", 0.05, 500, NOW - DAY),      # sold YES at 5c: filled better bids first
              _tr("yes", 0.01, 900, NOW - DAY),     # bought YES: not a hit on bids
              _tr("no", 0.01, 7, NOW - 9 * DAY),    # too old
              {"taker_side": "yes", "taker_action": "sell", "yes_price_dollars": "0.01",
               "count_fp": "14", "created_time": iso(NOW - DAY)}]   # sold YES (as a sell)
    assert lip_rank.fill_flow(trades, "yes", 0.02, since, ts) == pytest.approx((70 + 14) / 7)
    # NO side: our price is 1 - yes price; buying YES at 0.97 = selling NO at 3c
    assert lip_rank.fill_flow([_tr("yes", 0.97, 35, NOW - DAY)], "no", 0.03, since, ts) == pytest.approx(5)


def test_score_penalises_fills_and_news():
    pl = {"n": 100, "px": 0.05, "est_day": 1.0, "pool": 70.0, "days": 7.0}
    quiet = lip_rank.score(pl, [(0.05, 100), (0.06, 50)], 0.0, 0.0, False)
    assert quiet["net_day"] == pytest.approx(1.0) and quiet["fill_loss_day"] == 0
    # conservative share: everyone at/above our price shares pro-rata: 100 / (150 + 100)
    assert quiet["share_cons"] == pytest.approx(0.4) and quiet["reward_cons_day"] == pytest.approx(2.0)
    busy = lip_rank.score(pl, [(0.05, 100)], 200.0, 0.0, False)
    assert busy["fill_ct_day"] == pytest.approx(100 * (1 - 2.718281828 ** -1), rel=1e-3)
    assert busy["net_day"] < quiet["net_day"]
    jumpy = lip_rank.score(pl, [(0.05, 100)], 0.0, 0.04, False)
    assert jumpy["news_factor"] == 0.5 and jumpy["net_day"] == pytest.approx(0.5)
    assert lip_rank.score(pl, [], 0.0, None, True)["news_factor"] == 0.5


def test_pick_caps_events_and_keeps_held_markets():
    rows = [{"ticker": f"E1-{i}", "event": "E1", "net_day": 3.0 - i * 0.1} for i in range(4)]
    rows += [{"ticker": "E2-a", "event": "E2", "net_day": 1.0}, {"ticker": "E3-a", "event": "E3", "net_day": 0.9},
             {"ticker": "E4-a", "event": "E4", "net_day": -0.2}]
    got = [r["ticker"] for r in lip_rank.pick(rows, 4, set())]
    assert got == ["E1-0", "E1-1", "E2-a", "E3-a"]          # 2 per event; losers never picked
    # a held market that just missed stays unless the newcomer is clearly better
    rows2 = [{"ticker": "N", "event": "a", "net_day": 1.0}, {"ticker": "H", "event": "b", "net_day": 0.9}]
    assert [r["ticker"] for r in lip_rank.pick(rows2, 1, {"H"})] == ["H"]
    rows3 = [{"ticker": "N", "event": "a", "net_day": 2.0}, {"ticker": "H", "event": "b", "net_day": 0.9}]
    assert [r["ticker"] for r in lip_rank.pick(rows3, 1, {"H"})] == ["N"]


class TapeHttp(Http):
    def __init__(self, trades, **kw):
        super().__init__(**kw)
        self.tape = trades

    def get_json(self, url, params=None, headers=None):
        if url.endswith("/markets/trades"):
            return {"trades": self.tape.get(params["ticker"], [])}
        return super().get_json(url, params, headers)


def test_shadow_logs_and_moves_nothing(tmp_path):
    import datetime as dt
    now = dt.datetime(2026, 11, 13, 11, 0, tzinfo=dt.timezone.utc).timestamp()   # 6 AM ET
    progs = [dict(PROG, start_date=iso(now - DAY), end_date=iso(now + 6 * DAY)),
             dict(PROG, market_ticker="KXB-1", start_date=iso(now - DAY), end_date=iso(now + 6 * DAY))]
    mkts = {"KXA-1": dict(MKT, close_time=iso(now + 200 * DAY)),
            "KXB-1": dict(MKT, ticker="KXB-1", event_ticker="KXB", close_time=iso(now + 200 * DAY))}
    # KXB has a heavy sell tape at 2c: lots of fills -> ranked below KXA
    tape = {"KXB-1": [_tr("no", 0.02, 5000, now - DAY)]}
    http = TapeHttp(tape, progs=progs, books={"KXA-1": BOOK, "KXB-1": BOOK}, markets=mkts)
    c = Client(cash=30.0)
    bot, _, clock, _ = mk(tmp_path, client=c, http=http, now=now, control={"markets": 1, "price_mode": "ref"})
    bot.cycle()
    sh = bot.state["shadow"]
    assert sh["day"] == "2026-11-13" and sh["scored"] == 2
    assert sh["picks"] == ["KXA-1"]
    byt = {r["ticker"]: r for r in sh["top"]}
    assert byt["KXB-1"]["fill_loss_day"] > 0 and byt["KXA-1"]["fill_loss_day"] == 0
    assert (tmp_path / "shadow-2026-11-13.json").exists()
    assert "shadow" in bot.status()
    # only the live selection placed orders (one market); the shadow placed/cancelled nothing extra
    placed = [e for e in bot.state["events"] if e["kind"] == "place"]
    assert len(placed) == len(c.orders) == 1
    # once a day
    clock[0] += 300
    n_ev = len([e for e in bot.state["events"] if e["kind"] == "shadow"])
    bot.cycle()
    assert len([e for e in bot.state["events"] if e["kind"] == "shadow"]) == n_ev


def test_shadow_waits_for_morning_after_first_day(tmp_path):
    import datetime as dt
    now = dt.datetime(2026, 11, 14, 3, 0, tzinfo=dt.timezone.utc).timestamp()    # 10 PM ET Nov 13
    bot, _, _, _ = mk(tmp_path, now=now)
    bot.refresh()
    assert bot.shadow_due()                       # never ran: run now
    bot.state["shadow"] = {"day": "2026-11-13"}
    assert not bot.shadow_due()                   # new UTC day but before 10:00 UTC (5 AM ET)
