from datetime import datetime, timezone
from types import SimpleNamespace

from wxbot import trader
from wxbot.strats import ndx_range as nx

UTC = timezone.utc
CLOSE = "2026-11-16T21:00:00Z"            # Mon Nov 16 4 PM ET (EST)
EVENING = datetime(2026, 11, 15, 22, 0, tzinfo=UTC)   # Sun 5 PM ET: 23 h before the close
P = dict(nx.DEFAULTS)
EV = "KXNASDAQ100-26NOV16H1600"


def mk(strike, yb, ya, kind="between"):
    return {"ticker": f"{EV}-B{strike}", "event_ticker": EV, "strike_type": kind, "close_time": CLOSE,
            "yes_bid_dollars": f"{yb:.4f}", "yes_ask_dollars": f"{ya:.4f}", "volume_fp": "300"}


BOOK = [mk(26550, 0.05, 0.08), mk(26650, 0.12, 0.16), mk(26750, 0.20, 0.30), mk(26850, 0.14, 0.18),
        mk(26950, 0.06, 0.09), mk(27050, 0.02, 0.04), mk(26450, 0.03, 0.05), mk(26350, 0.0, 0.02),
        mk(27999, 0.0, 0.01, kind="greater"),
        {**mk(6800, 0.2, 0.3), "ticker": "KXINX-26NOV16H1600-B6800", "event_ticker": "KXINX-26NOV16H1600"}]


def test_rests_on_six_nasdaq_ranges_only():
    out = nx.candidates(BOOK, P, EVENING)
    assert len(out) == 6
    assert all(i.strategy == "ndx_range" and i.ticker.startswith("KXNASDAQ100-") for i in out)
    assert all(i.side == "no" and i.price == 0.65 and not i.taker and i.count == 1 for i in out)
    assert "26350" not in {i.ticker.rsplit("B", 1)[-1] for i in out}


def test_takes_a_yes_bid_at_or_above_35c():
    out = nx.candidates([mk(26750, 0.40, 0.44)], P, EVENING)
    assert len(out) == 1 and out[0].taker and out[0].price == 0.60


def test_window_and_cancel():
    assert nx.candidates(BOOK, P, datetime(2026, 11, 16, 13, 40, tzinfo=UTC)) == []   # 8:40 AM ET
    assert not nx.cancel_due({"close": CLOSE}, datetime(2026, 11, 16, 13, 40, tzinfo=UTC))
    assert nx.cancel_due({"close": CLOSE}, datetime(2026, 11, 16, 13, 46, tzinfo=UTC))
    assert "ndx_range" in trader.cancellers() and "spx_range" in trader.cancellers()


def test_spx_rule_unchanged_by_the_refactor():
    from wxbot.strats import spx_range as sx
    out = sx.candidates(BOOK, P, EVENING)
    assert len(out) == 1 and out[0].strategy == "spx_range" and out[0].ticker.startswith("KXINX-")


def test_dollar_stop(monkeypatch, tmp_path):
    monkeypatch.setattr(nx, "STATE", tmp_path / "s.json")
    sent = []
    monkeypatch.setattr(nx.notify, "send", lambda *a, **k: sent.append(a))
    assert nx.stop_limit(P, 58.0) == 5.0 and nx.stop_limit(P, 30.0) == 3.0
    rows = [{"strategy": "ndx_range", "mode": "live", "status": "settled", "pnl": "-0.66"}] * 8
    ctx = SimpleNamespace(orders=rows, cfg=SimpleNamespace(bankroll=58.0), now=EVENING, http=None)
    monkeypatch.setattr(nx, "archived_realized", lambda *a: 0.0)
    monkeypatch.setattr(nx, "realized", lambda orders, strategy, mode: sum(float(r["pnl"]) for r in orders))
    assert nx.stopped(ctx, P) and nx.stopped(ctx, P)
    assert len(sent) == 1                          # one push only
    ctx.orders = rows[:7]                          # -$4.62: still trading
    assert not nx.stopped(ctx, P)


def test_config_is_one_contract_live():
    from wxbot.util import load_config
    c = load_config()
    nd = c.raw["trading"]["ndx_range"]
    assert nd["live"] is True and nd["contracts"] == nd["max_contracts"] == 1
    assert nd["max_price"] == 0.65 and nd["sell_yes"] == 0.35 and nd["max_loss_usd"] == 5.0
    # both range rules together stay small: 12 resting bids at 65c
    sp = c.raw["trading"]["spx_range"]
    assert (sp["n_markets"] + nd["n_markets"]) * 0.65 <= 8.0
