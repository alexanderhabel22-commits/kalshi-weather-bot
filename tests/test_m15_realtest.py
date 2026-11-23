"""Natgas small real test (Nov 23): control-gated, 1 real contract, paper twin, hard $ cap."""
import csv
from datetime import datetime, timezone

from wxbot import kalshi, m15
from wxbot.util import load_config

from test_m15 import Client, Clock, iso

T0 = datetime(2026, 11, 23, 18, 0, tzinfo=timezone.utc).timestamp()
CLOSE = T0 + 240                        # inside the last 5 minutes
NG = "KXNATGAS15M"
TK = "KXNATGAS15M-26NOV231304-04"


def cfg(**kw):
    c = load_config()
    c.raw["m15"].update({"series": [NG], "guard": False, "early_on": False, "e2_series": [],
                         "real_test_tag": "t1"})
    c.raw["m15"].update(kw)
    return c


class World:
    def __init__(self, monkeypatch, yb=0.82, ya=0.84):
        self.book = {"yes_bid": yb, "yes_ask": ya}
        self.tape, self.result, self.open = [], "", True
        monkeypatch.setattr(kalshi, "list_open_markets", lambda http, s: [
            {"ticker": TK, "close_time": iso(CLOSE)}] if self.open else [])
        monkeypatch.setattr(kalshi, "get_markets", lambda http, tks: {
            TK: {"ticker": TK, "yes_bid_dollars": str(self.book["yes_bid"]),
                 "yes_ask_dollars": str(self.book["yes_ask"]), "result": self.result}})
        monkeypatch.setattr(m15.Bot, "trades", lambda bot, m: list(self.tape))


def mk(c, clk, cl, ctl):
    def fetch(url):
        if "m15_control" in url:
            return ctl
        return {"mode": "live", "halted": {}} if "trader_state" in url else {"total": 50.0}
    return m15.Bot(c, http=None, client=cl, clock=clk, fetch_json=fetch)


def settle(b, w, clk, result):
    w.open = False
    clk.t = CLOSE + 30
    w.result = result
    b.step()


def test_config_ships_natgas_only_real():
    s = m15.settings(load_config())
    assert s["live_series"] == [NG]
    assert s["control_gated"] == [NG] and s["twin_series"] == [NG]
    assert s["live_max_contracts"] == {NG: 1} and s["real_caps"] == {NG: 3.0}


def test_no_real_order_unless_switch_on(monkeypatch):
    World(monkeypatch)
    clk, cl = Clock(T0), Client()
    ctl = {"mode": "live"}                       # no "real" entry -> natgas stays paper
    b = mk(cfg(), clk, cl, ctl)
    b.step()
    assert cl.placed == []
    tw = b.state["markets"][f"{TK}|paper"]
    assert tw["twin"] and tw["order"]["q"] == 0.82          # the twin quotes on paper
    assert b.state["markets"][TK]["why"] == "paper runs on the twin"
    ctl["real"] = {NG: "on"}
    clk.t += 61
    b.step()
    assert cl.placed == [(TK, "yes", 0.82, 1)]
    ctl["real"] = {NG: "off"}                    # fast kill for this test alone
    clk.t += 61
    b.step()
    assert cl.cancelled == ["o0"]


def test_real_is_one_contract_twin_keeps_paper_size(monkeypatch):
    w = World(monkeypatch)
    clk, cl = Clock(T0), Client()
    alerts = []
    monkeypatch.setattr(m15.Bot, "alert", lambda self, t, body, p=3: alerts.append(t))
    b = mk(cfg(contracts=1, extra_contracts=1), clk, cl, {"mode": "live", "real": {NG: "on"}})
    b.step()
    assert cl.placed == [(TK, "yes", 0.82, 1)]               # 82c is in the 80-87c double band
    tw = b.state["markets"][f"{TK}|paper"]
    assert tw["order"]["n"] == 2                              # paper twin: 1 + 1 as before
    cl.orders["o0"].update(status="executed", fill_count=1, remaining_count=0)
    w.tape = [(clk.t + 1, 0.80, 5, "no")]
    clk.t += 6
    b.step()
    live = b.state["markets"][TK]
    assert live["filled"] == 1 and tw["filled"] == 2
    assert any("first REAL" in a for a in alerts)
    clk.t += 6
    b.step()
    assert len(cl.placed) == 1                                # never a 2nd real contract
    settle(b, w, clk, "yes")
    st = b.state["realtest"][NG]
    assert st["ct"] == 1 and round(st["pnl"], 2) == 0.18 and st["paper_ct"] == 2
    rows = list(csv.DictReader(open(m15.M15_DIR / f"realtest-{b.day(CLOSE)}.csv")))
    assert rows[-1]["real_fills"] == "1@0.82" and rows[-1]["paper_fills"] == "2@0.82"
    mrows = [r for r in csv.DictReader(open(m15.M15_DIR / f"markets-{b.day(CLOSE)}.csv"))
             if r["ticker"] == TK and r["close"].startswith("2026-11-23T18:04")]
    assert sorted(r["mode"] for r in mrows) == ["live", "paper"]   # one real + one paper row


def test_paper_mode_counts_natgas_once(monkeypatch):
    w = World(monkeypatch)
    clk = Clock(T0)
    b = mk(cfg(mode="paper"), clk, None, {"mode": "paper"})
    b.step()
    w.tape = [(clk.t + 1, 0.80, 5, "no")]
    clk.t += 20
    b.step()
    settle(b, w, clk, "no")
    rows = [r for r in csv.DictReader(open(m15.M15_DIR / f"markets-{b.day(CLOSE)}.csv"))
            if r["ticker"] == TK and r["close"].startswith("2026-11-23T18:04") and r["result"] == "no"]
    assert len(rows) == 1 and rows[0]["mode"] == "paper"


def test_cap_blocks_and_cuts(monkeypatch):
    w = World(monkeypatch)
    clk, cl = Clock(T0), Client()
    alerts = []
    monkeypatch.setattr(m15.Bot, "alert", lambda self, t, body, p=3: alerts.append(t))
    b = mk(cfg(), clk, cl, {"mode": "live", "real": {NG: "on"}})
    b.realtest(NG)["pnl"] = -2.30                             # settled loss so far
    b.step()
    assert cl.placed == []                                    # 0.82 > $0.70 room
    assert b.state["markets"][TK]["why"].startswith("real-test cap")
    b.realtest(NG)["pnl"] = -2.00
    clk.t += 6
    b.step()
    assert cl.placed == [(TK, "yes", 0.82, 1)]                # $1.00 room fits
    cl.orders["o0"].update(status="executed", fill_count=1, remaining_count=0)
    clk.t += 6
    b.step()
    settle(b, w, clk, "no")                                   # loses 82c -> -$2.82
    assert NG in b.state["cut"] and b.state["cut"][NG]["why"] == "real-test cap"
    assert any("cap" in a and "PAPER" in a for a in alerts)


def test_tag_resets_record():
    clk = Clock(T0)
    b = mk(cfg(), clk, None, {})
    b.realtest(NG)["pnl"] = -1.0
    b.cfg.raw["m15"]["real_test_tag"] = "t2"
    assert b.realtest(NG)["pnl"] == 0.0


def test_shipped_config_never_moves_cash():
    class C(Client):
        moved = []

        def shard_balances(self):
            return {0: 40.0, 2: 1.0}

        def shard_transfer(self, amt, src, dst):
            self.moved.append(amt)
    clk, cl = Clock(T0), C()
    b = mk(cfg(), clk, cl, {})
    assert b.fund_shard(T0 + 1000) == 0.0 and cl.moved == [] and b.state["shards"] == {"0": 40.0, "2": 1.0}
