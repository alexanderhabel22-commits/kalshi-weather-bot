"""Scale-up kit (wxbot/scaleup.py): off changes nothing; on, tiers only tighten, steps 1->2->4."""
from datetime import datetime, timezone

from wxbot import m15, scaleup, trader
from wxbot.util import load_config

from tests.test_m15 import CLOSE, TK, Client, Clock, World, bot, cfg as m15_cfg


def on(c, **kw):
    c.raw["scaleup"] = dict(c.raw.get("scaleup", {}), enabled=True, **kw)
    return c


def test_config_ships_switched_off():
    c = load_config()
    assert c.raw["scaleup"]["enabled"] is False
    assert scaleup.params(c)["enabled"] is False
    # config tiers parse and are sorted by bankroll
    names = [t["name"] for t in c.raw["scaleup"]["tiers"]]
    assert names[0] == "test money" and len(names) == 4


def test_off_changes_nothing_main_bot(monkeypatch):
    monkeypatch.delenv("TRADING_MODE", raising=False)
    c = load_config()
    c.raw["account"]["auto_bankroll"] = False
    c.raw["account"]["bankroll"] = 2000.0
    t = trader.settings(c)
    assert t["max_open_pct"] == 1.00 and t["daily_loss_pct"] == 0.10 and t["total_loss_pct"] == 0.25
    assert t["scaleup_tier"] is None


def test_tiers_tighten_main_bot_by_bankroll(monkeypatch):
    monkeypatch.delenv("TRADING_MODE", raising=False)
    c = on(load_config())
    c.raw["account"]["auto_bankroll"] = False
    c.raw["account"]["bankroll"] = 60.0
    t = trader.settings(c)
    assert t["scaleup_tier"] == "test money" and t["max_open_pct"] == 1.00   # test money: unchanged
    c.raw["account"]["bankroll"] = 500.0
    t = trader.settings(c)
    assert t["scaleup_tier"] == "small real"
    assert t["max_open_pct"] == 0.50 and t["max_open_dollars"] == 250.0
    assert t["daily_loss_stop"] == -40.0 and t["total_loss_stop"] == -100.0
    c.raw["account"]["bankroll"] = 6000.0
    assert trader.settings(c)["total_loss_pct"] == 0.10


def test_tiers_never_loosen():
    p = scaleup.params(on(load_config()))
    t, _ = scaleup.main_limits({"max_open_pct": 0.30, "daily_loss_pct": 0.02, "total_loss_pct": 0.05}, p, 500)
    assert t == {"max_open_pct": 0.30, "daily_loss_pct": 0.02, "total_loss_pct": 0.05}
    s, _ = scaleup.m15_limits({"daily_loss_pct": 0.35, "total_loss_pct": 0.50, "stop_guard": False}, p, 500)
    assert s["daily_loss_pct"] == 0.15 and s["total_loss_pct"] == 0.25 and s["stop_guard"] is True
    s, _ = scaleup.m15_limits({"daily_loss_pct": 0.35, "stop_guard": True}, p, 50)
    assert s["daily_loss_pct"] == 0.35 and s["stop_guard"] is True        # test tier: no change


def feed(p, st, n, per_ct, now, bankroll=1000.0, cut=False):
    """n graded one-contract markets earning per_ct each; returns the moves."""
    moves = []
    for i in range(n):
        mv = scaleup.record_step(p, st, 1, per_ct, now + i, bankroll, cut)
        if mv:
            moves.append(mv)
    return moves


def test_steps_one_at_a_time_with_gates():
    p = scaleup.params(on(load_config()))
    day = 86400
    st = {"fills": 600}
    # off: records but never moves
    off = scaleup.params(load_config())
    assert feed(off, st, 400, 0.05, 0) == [] and st["step"] == 0 and st["at_step"][0] == 400
    # on, but too soon (step_min_days) -> waits; then one step up, never two at once
    st = {"fills": 600}
    assert feed(p, st, 300, 0.05, 0) == []
    assert feed(p, st, 1, 0.05, 3 * day) == ["up"] and st["step"] == 1
    assert scaleup.step_contracts(p, st, 1000, 1) == 2
    # the next step needs a fresh 300 contracts at step 2 and 3 more days
    assert feed(p, st, 299, 0.05, 3 * day + 10) == []
    assert feed(p, st, 1, 0.05, 6 * day + 10) == ["up"] and st["step"] == 2
    assert scaleup.step_contracts(p, st, 1000, 1) == 4
    # bankroll caps the step without forgetting it
    assert scaleup.step_contracts(p, st, 200, 1) == 2 and scaleup.step_contracts(p, st, 100, 1) == 1
    # losing at a step -> back one step
    assert feed(p, st, 300, -0.05, 7 * day)[-1] == "down" and st["step"] == 1


def test_step_needs_review_count_bankroll_and_not_cut():
    p = scaleup.params(on(load_config()))
    st = {"fills": 400}                                   # 500 review not reached
    assert feed(p, st, 300, 0.05, 0) == [] and feed(p, st, 1, 0.05, 4 * 86400) == []
    st = {"fills": 600}
    assert feed(p, st, 301, 0.05, 0, bankroll=100) == []
    st = {"fills": 600}
    assert feed(p, st, 301, 0.05, 0, cut=True) == []
    st = {"fills": 600}
    assert feed(p, st, 301, 0.00, 4 * 86400) == []          # below +1c/contract


def test_extra_contract_scales_with_step():
    s = {"contracts": 1, "extra_contracts": 1, "extra_min": 0.80, "extra_max": 0.87,
         "series_contracts": {"KXBTC15M": 4}, "step_scales_extra": True}
    assert m15.target_contracts(0.82, s, "KXBTC15M") == 8
    assert m15.target_contracts(0.90, s, "KXBTC15M") == 4
    assert m15.target_contracts(0.82, s, "KXSILVER15M") == 2      # no step: today's rule
    assert m15.target_contracts(0.82, dict(s, step_scales_extra=False), "KXBTC15M") == 5


def test_m15_bot_off_ignores_step(monkeypatch):
    World(monkeypatch)
    cl = Client()
    b = bot(m15_cfg(mode="live", pool_pct=1.0, stop_guard=False, early_after_contracts=0), Clock(), cl)
    b.state["series"] = {"KXBTC15M": {"fills": 900, "pnl": 40.0, "step": 2}}
    b.step()
    assert cl.placed[-1][3] == 1                           # kit off: step ignored


def test_m15_bot_on_places_step(monkeypatch):
    World(monkeypatch)
    cl2 = Client()
    c2 = on(m15_cfg(mode="live", pool_pct=1.0, stop_guard=False, early_after_contracts=0))
    b2 = bot(c2, Clock(), cl2)
    b2.paper_bankroll = 1000.0
    b2.fetch_json = lambda url: {"mode": "live", "halted": {}} if "trader_state" in url else {"total": 1000.0}
    b2.state["series"] = {"KXBTC15M": {"fills": 900, "pnl": 40.0, "step": 2}}
    b2.step()
    assert cl2.placed[-1][3] == 4
    s = b2.limits(Clock()())
    assert s["scaleup_tier"] == "real" and s["daily_loss_pct"] == 0.10 and s["stop_guard"] is True
    assert TK in b2.state["markets"] and CLOSE


def test_push_budget(monkeypatch):
    p = scaleup.params(on(load_config(), max_trade_pushes_per_day=2))
    st = {}
    now = datetime(2026, 11, 20, 15, tzinfo=timezone.utc)
    got = [scaleup.push_allowed(p, st, now, 3) for _ in range(4)]
    assert got == [(True, False), (True, False), (False, True), (False, False)]
    assert scaleup.push_allowed(p, st, now, 4) == (True, False)          # warnings always push
    tomorrow = now.replace(day=21)
    assert scaleup.push_allowed(p, st, tomorrow, 3) == (True, False)
    off = scaleup.params(load_config())
    assert all(scaleup.push_allowed(off, {}, now, 3)[0] for _ in range(100))
