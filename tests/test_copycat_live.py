import json

from wxbot import copycat
from wxbot.copycat_boss import Boss, digest
from wxbot.copycat_live import BANKROLL_URL, CONTROL_URL, SWITCH_URL, Live


class FakeClient:
    def __init__(self, held=0.0, fill=None):
        self.held, self.orders = held, []
        self.fill = fill

    def position(self, ticker):
        return self.held

    def buy_now(self, ticker, side, price, n, coid):
        self.orders.append((ticker, side, price, n))
        return self.fill if self.fill is not None else {"fill_count_fp": str(n), "order_id": "o1",
                                                        "taker_fill_cost_dollars": str(round(price * n, 2)),
                                                        "taker_fees_dollars": "0.02"}


def mk(tmp_path, control=None, switch=None, bankroll=50.0, client=None):
    data = {CONTROL_URL: {"mode": "live", **(control or {})}, SWITCH_URL: switch or {"mode": "live"},
            BANKROLL_URL: {"total": bankroll}}
    rows = []
    lv = Live(tmp_path, client=client or FakeClient(), fetch_json=lambda u: data[u], clock=lambda: 1000.0,
              write=rows.append)
    lv.refresh(1000.0, force=True)
    return lv, rows


ACCT = {"nick": "ace", "verdict": "passes", "size": 2}
FILL = {"nick": "ace", "ticker": "KXFOO-1", "side": "yes", "yes_price": 0.60, "lag_s": 12, "trade_id": "t1"}
SNAP = {"bid": 0.59, "ask": 0.61}


def test_copies_passing_account(tmp_path):
    lv, rows = mk(tmp_path)
    assert lv.maybe_copy(ACCT, FILL, SNAP, 1000.0) == "copied"
    assert lv.client.orders == [("KXFOO-1", "yes", 0.62, 2)]
    assert lv.state["positions"]["KXFOO-1"]["n"] == 2
    assert lv.maybe_copy(ACCT, dict(FILL, trade_id="t2"), SNAP, 1001.0) == "already copied this market"
    assert json.loads((tmp_path / "live_state.json").read_text())["positions"]


def test_rails(tmp_path):
    lv, _ = mk(tmp_path)
    assert lv.maybe_copy(dict(ACCT, verdict="slump"), FILL, SNAP, 1000.0) == "not passing"
    assert lv.maybe_copy(ACCT, dict(FILL, lag_s=90), SNAP, 1000.0) == "too late"
    assert lv.maybe_copy(ACCT, FILL, {"bid": 0.66, "ask": 0.67}, 1000.0).startswith("price ran")
    assert lv.maybe_copy(ACCT, dict(FILL, ticker="KXBTC15M-26NOV"), SNAP, 1000.0) == "excluded series"
    assert lv.maybe_copy(ACCT, FILL, {"bid": None, "ask": None}, 1000.0) == "no quote"
    # NO side pays 1 - bid
    assert lv.maybe_copy(ACCT, dict(FILL, side="no", yes_price=0.40), {"bid": 0.59, "ask": 0.61}, 1000.0) == "copied"
    assert lv.client.orders[-1] == ("KXFOO-1", "no", 0.62, 2)


def test_switches_off(tmp_path):
    assert mk(tmp_path, control={"mode": "off"})[0].maybe_copy(ACCT, FILL, SNAP, 1000.0) == "off"
    assert mk(tmp_path, switch={"mode": "off"})[0].maybe_copy(ACCT, FILL, SNAP, 1000.0) == "off"
    assert mk(tmp_path, switch={"mode": "live", "halted": {"live": "x"}})[0].maybe_copy(ACCT, FILL, SNAP, 1000.0) == "off"
    lv, _ = mk(tmp_path)
    assert lv.mode(1000.0 + 700) == "off"          # control file not read for 10+ min
    lv2, _ = mk(tmp_path, control={"bench": ["ace"]})
    assert lv2.maybe_copy(ACCT, FILL, SNAP, 1000.0) == "benched"


def test_held_elsewhere_and_pool(tmp_path):
    lv, _ = mk(tmp_path, client=FakeClient(held=1))
    assert lv.maybe_copy(ACCT, FILL, SNAP, 1000.0) == "we already hold this market"
    lv, _ = mk(tmp_path, bankroll=8.0)               # pool = $1.20: 2 x 62c too much, 1 fits
    assert lv.maybe_copy(ACCT, FILL, SNAP, 1000.0) == "copied"
    assert lv.client.orders[-1][3] == 1
    assert lv.maybe_copy(ACCT, dict(FILL, ticker="KXBAR-1"), SNAP, 1000.0) == "pool full"


def test_settle_and_stops(tmp_path):
    alerts = []
    lv, rows = mk(tmp_path, bankroll=10.0)
    lv.alert = lambda t, b: alerts.append(t)
    lv.maybe_copy(ACCT, FILL, SNAP, 1000.0)          # 2 yes at 62c + 2c fee = $1.26
    done = lv.settle({"KXFOO-1": "no"}, 2000.0)
    assert done and lv.state["pnl"]["ace"] == -1.26
    assert "ace" in lv.benched()                      # -$1.26 < 10% of $10
    assert lv.state["halted"] is None                 # total stop is 25% = $2.50
    assert alerts == ["Copycat benched an account"]
    assert any(r["kind"] == "settle" for r in rows)


def test_add_real_keeps_paper_and_real_apart(tmp_path):
    lv, _ = mk(tmp_path)
    lv.maybe_copy(ACCT, FILL, SNAP, 1000.0)
    lv.settle({"KXFOO-1": "yes"}, 2000.0)
    status = {"accounts": [{"nick": "ace"}, {"nick": "bob"}]}
    copycat.add_real(status, lv.state)
    a, b = status["accounts"]
    assert a["real"]["copies"] == 1 and a["real"]["won"] == 1 and a["real"]["pnl"] == 0.74
    assert b["real"]["copies"] == 0 and b["real"]["pnl"] == 0


def boss_status():
    return {"generated": "now", "mode": "live", "accounts": [
        {"nick": "ace", "fills": 120, "settled": 40, "score": 88.0, "grade": "B+", "verdict": "passes"}]}


def test_boss_benches_and_unbenches(tmp_path):
    t = [100000.0]
    calls = []

    def ask(model, text, schema):
        calls.append(model)
        if "TRIAGE" in text:
            return {"review_now": True, "why": "x"}
        n = calls.count("claude-opus-5-5")
        act = {1: "bench", 2: "none"}.get(n, "unbench")
        return {"summary": "ok", "alert": "", "accounts": [
            {"nick": "ace", "note": "Trades weather; fine.", "action": act, "reason": "market maker"}]}

    b = Boss(tmp_path, ask=ask, clock=lambda: t[0])
    assert b.step(boss_status(), [], None)            # first: changed -> straight to Opus
    assert calls == ["claude-opus-5-5"]
    assert "ace" in b.benches(t[0]) and b.state["notes"]["ace"].startswith("Trades")
    assert not b.step(boss_status(), [], None)        # too soon
    t[0] += 1000
    assert b.step(boss_status(), [], None)            # unchanged -> Haiku triage asks for review
    assert calls[1] == "claude-haiku-5-5" and "ace" in b.benches(t[0])
    t[0] += 1000
    b.step(boss_status(), [], None)
    assert "ace" not in b.benches(t[0])
    assert json.loads((tmp_path / "boss.json").read_text())["deep_n"] == 3


def test_boss_bench_expires_and_digest(tmp_path):
    b = Boss(tmp_path, ask=lambda *a: {})
    b.state["benches"]["ace"] = {"reason": "x", "until": 50}
    assert b.benches(10) and not b.benches(60)
    rows = [{"kind": "fill", "nick": "ace", "ticker": "KXHIGHNY-1", "side": "no", "yes_price": 0.2,
             "count": 3, "role": "taker"}]
    txt = digest(boss_status(), rows, {"pnl": {"ace": 1.5}, "total_pnl": 1.5, "open_cost": 0, "pool": 8}, {})
    assert "KXHIGHNY" in txt and "real P&L $1.50" in txt and "median 80c" in txt


def test_poll_copies_new_fill_of_hot_account(tmp_path):
    from datetime import datetime, timezone

    from tests.test_copycat import FakeSession, trade
    now = datetime(2026, 11, 10, 20, 0, 15, tzinfo=timezone.utc).timestamp()
    s = FakeSession()
    s.trades = [trade("a", taker="sharp")]
    lv, _ = mk(tmp_path)
    cc = copycat.Copycat(tmp_path, session=s, clock=lambda: now, req_gap=0, live=lv)
    cc.poll("sharp", now)                            # first poll = backfill, never copied
    cc.hot = {"sharp"}
    cc.acct = {"sharp": {"nick": "sharp", "verdict": "passes", "size": 1}}
    s.trades = [trade("b", taker="sharp", px="0.61", t="2026-11-10T20:00:05Z")] + s.trades
    cc.poll("sharp", now)
    assert lv.client.orders == [("KXTEST-1", "yes", 0.63, 1)]
    rows = [json.loads(x) for p in tmp_path.glob("copycat-*.jsonl") for x in p.read_text().splitlines()]
    assert [r["what"] for r in rows if r["kind"] == "copy"] == ["copied"]
