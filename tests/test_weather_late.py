from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from wxbot.strats import weather_late as wl

UTC = timezone.utc
NOW = datetime(2026, 11, 12, 21, 0, tzinfo=UTC)
CLOSE = (NOW + timedelta(hours=9)).isoformat()


def order(tk="KXLOWTNYC-26NOV12-B42.5", metric="low", kind="need", side="no", price="0.81", age_min=3, status="open"):
    return {"id": f"{tk}|{side}", "placed_at": (NOW - timedelta(minutes=age_min)).isoformat(), "ticker": tk,
            "event": "KXLOWTNYC-26NOV12", "city": "NYC", "metric": metric, "date": "2026-11-12",
            "bracket": "42° to 43°", "kind": kind, "side": side, "price": price, "extreme": "45.1",
            "hours_left": "9.0", "close": CLOSE, "status": status}


def ctx(orders=(), mode="live", bankroll=60.0, **params):
    return SimpleNamespace(now=NOW, params=params, mode=mode, orders=list(orders), http=None,
                           cfg=SimpleNamespace(bankroll=bankroll))


def setup(monkeypatch, paper, bid=0.10, ask=0.20, archived=0.0):
    monkeypatch.setattr(wl, "load_orders", lambda: paper)
    monkeypatch.setattr(wl, "archived_realized", lambda mode, strategy: archived)
    monkeypatch.setattr(wl.kalshi, "get_market", lambda http, tk: {
        "ticker": tk, "yes_bid_dollars": f"{bid:.2f}", "yes_ask_dollars": f"{ask:.2f}"})


def test_mirrors_only_fresh_low_need_orders(monkeypatch):
    paper = [order(), order(tk="H", metric="high"), order(tk="Y", kind="holds", side="yes", price="0.56"),
             order(tk="OLD", age_min=45), order(tk="DONE", status="filled")]
    setup(monkeypatch, paper)
    got = wl.propose(ctx())
    assert [(i.strategy, i.ticker, i.side, i.price, i.count) for i in got] == \
        [("weather_late", "KXLOWTNYC-26NOV12-B42.5", "no", 0.81, 1)]
    assert "low so far 45.1" in got[0].note


def test_skips_an_order_that_would_cross_the_spread(monkeypatch):
    setup(monkeypatch, [order()], bid=0.19, ask=0.24)      # we'd offer YES at 19c into a 19c bid
    assert wl.propose(ctx()) == []
    setup(monkeypatch, [order()], bid=0.18, ask=0.24)
    assert len(wl.propose(ctx())) == 1


def test_own_loss_stop_blocks_new_real_orders(monkeypatch):
    setup(monkeypatch, [order()])
    lost = [{"status": "settled", "strategy": "weather_late", "mode": "live", "pnl": "-0.90"} for _ in range(7)]
    assert wl.propose(ctx(lost)) == []                      # -$6.30 <= -10% of $60
    assert len(wl.propose(ctx(lost[:6]))) == 1              # -$5.40: still trading
    assert len(wl.propose(ctx(lost, mode="paper"))) == 1    # paper is never stopped
    setup(monkeypatch, [order()], archived=-6.50)           # losses already archived count too
    assert wl.propose(ctx()) == []


def test_runs_through_the_trader_on_paper(monkeypatch, tmp_path):
    """End to end: the trader accepts the order, sizes it at 1 contract and never exits it."""
    import test_trader as tt
    from wxbot import trader
    now = tt.NOW
    o = order()
    o["placed_at"] = (now - timedelta(minutes=3)).isoformat()
    o["close"] = tt.CLOSE
    monkeypatch.setattr(wl, "load_orders", lambda: [o])
    monkeypatch.setattr(wl, "archived_realized", lambda mode, strategy: 0.0)
    real = wl.kalshi.get_market
    monkeypatch.setattr(wl.kalshi, "get_market", lambda http, tk: (
        {"ticker": tk, "yes_bid_dollars": "0.10", "yes_ask_dollars": "0.20", "close_time": tt.CLOSE}
        if tk == o["ticker"] else real(http, tk)))
    c = tt.cfg(enabled=False)                       # mentions off; every strategy on paper
    out = trader.run(c, now=now, http=tt.Http([]))
    mine = [r for r in trader.load() if r["strategy"] == "weather_late"]
    assert len(mine) == 1, out
    assert (mine[0]["mode"], mine[0]["side"], mine[0]["price"], mine[0]["count"]) == ("paper", "no", "0.81", "2")
    assert trader.strategy_settings(c, "weather_late", trader.settings(c))["exit"] == "hold"


def test_contracts_by_sizes_lows_up_and_keeps_others_at_base(monkeypatch):
    paper = [order(), order(tk="H", metric="high"), order(tk="Y", kind="holds", side="yes", price="0.15")]
    setup(monkeypatch, paper)
    c = ctx()
    c.params.update(metrics=["low", "high"], kinds=["need", "holds"], contracts=1,
                    contracts_by={"low/need": 2})
    got = {i.ticker: i.count for i in wl.propose(c)}
    assert got == {"KXLOWTNYC-26NOV12-B42.5": 2, "H": 1, "Y": 1}
