from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from wxbot import trader
from wxbot.util import load_config
from wxbot.strats import fade_leads as f
from wxbot.strats import fade_leads_paper as fp

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 19, 0, tzinfo=UTC)


def mkts():
    close = (NOW + timedelta(days=10)).isoformat().replace("+00:00", "Z")
    return [{"ticker": f"KXU3-26NOV-{k}", "event_ticker": "KXU3-26NOV",
             "close_time": close, "yes_bid_dollars": f"{yb:.2f}", "yes_ask_dollars": f"{ya:.2f}",
             "volume": 5000} for k, yb, ya in (("T4.2", 0.12, 0.15), ("T4.6", 0.08, 0.10), ("T4.0", 0.80, 0.81))]


def P():
    return {**f.DEFAULTS, **load_config().raw["trading"]["fade_leads"]}


def row(status="settled", pnl="", price="0.86", n=2, mode="live", strat="fade_leads"):
    return {"strategy": strat, "mode": mode, "status": status, "pnl": pnl, "price": price,
            "count": n, "filled": n, "ticker": "x", "event": "e"}


def ctx(orders=(), mode="live"):
    return SimpleNamespace(now=NOW, orders=list(orders), mode=mode, http=None, cfg=None, params=P())


def test_config_real_two_contracts_with_cap_and_paper_twin():
    c = load_config()
    p, pp = c.raw["trading"]["fade_leads"], c.raw["trading"]["fade_leads_paper"]
    assert trader.strategy_mode("live", p) == "live" and trader.strategy_mode("live", pp) == "paper"
    assert trader.strategy_mode("off", p) is None             # TRADING_MODE=off kill switch: no trading
    assert p["contracts"] == p["max_contracts"] == 2 and p["max_loss_usd"] == 6.0
    assert p["series"] == pp["series"] == ["KXU3", "KXGDP", "KXSPACEXSTARSHIP"]
    for k in ("min_price", "max_price", "min_hours", "max_hours", "min_volume", "max_markets_per_event"):
        assert p[k] == pp[k], k                                # same rule as the paper twin
    assert p["max_event_pct"] <= 0.20
    assert c.raw["radar"]["kill_rules"]["fade_leads"]["max_loss_usd"] == 6.0


def test_same_rule_as_macro_fade_relabelled():
    out = f.candidates(mkts(), P(), NOW)
    assert [i.ticker for i in out] == ["KXU3-26NOV-T4.2", "KXU3-26NOV-T4.6"]
    assert all(i.strategy == "fade_leads" and i.count == 2 and i.side == "no" for i in out)
    assert out[0].price == 0.86
    po = fp.candidates(mkts(), load_config().raw["trading"]["fade_leads_paper"], NOW)
    assert [i.ticker for i in po] == [i.ticker for i in out] and all(i.count == 1 for i in po)
    assert po[0].strategy == "fade_leads_paper"


def test_budget_counts_open_and_resting(monkeypatch):
    monkeypatch.setattr(f, "archived_realized", lambda *a, **k: 0.0)
    rows = [row(pnl="-1.72"), row(status="filled"), row(status="resting", price="0.90"),
            row(status="filled", mode="paper", strat="fade_leads_paper")]
    room, settled = f.budget(ctx(rows), P())
    assert settled == -1.72 and abs(room - (6.0 - 1.72 - 1.72 - 1.80)) < 1e-9
    out = f.fit_budget(f.candidates(mkts(), P(), NOW), room)       # 0.76 left: nothing fits at 86c
    assert out == []
    out = f.fit_budget(f.candidates(mkts(), P(), NOW), 2.70)
    assert [i.count for i in out] == [2, 1]
    assert sum(i.count * (i.price + f.FEE) for i in out) <= 2.70


def test_stop_after_cap_used_one_push(monkeypatch, tmp_path):
    monkeypatch.setattr(f, "STATE", tmp_path / "s.json")
    sent = []
    monkeypatch.setattr(f.notify, "send", lambda *a, **k: sent.append(a))
    c = ctx()
    assert f.stopped(c, P(), -5.0) is False            # $1 left: room for one 95c contract
    assert f.stopped(c, P(), -5.2) is True and len(sent) == 1
    assert f.stopped(c, P(), -5.2) is True and len(sent) == 1


def test_propose_shares_one_read_with_paper_twin(monkeypatch):
    calls = []
    monkeypatch.setattr(f, "_series_markets", lambda c, s: calls.append(s) or (mkts() if s == "KXU3" else []))
    monkeypatch.setattr(f, "archived_realized", lambda *a, **k: 0.0)
    monkeypatch.setenv("TRADE_FORCE_SCAN", "1")
    f._CACHE.clear()
    real = f.propose(ctx())
    paper = fp.propose(SimpleNamespace(now=NOW, orders=[], mode="paper", http=None, cfg=None,
                                       params=load_config().raw["trading"]["fade_leads_paper"]))
    assert len(real) == 2 and real[0].strategy == "fade_leads"
    assert len(paper) == 2 and paper[0].strategy == "fade_leads_paper"
    assert calls == ["KXU3", "KXGDP", "KXSPACEXSTARSHIP"]          # one Kalshi read per run
