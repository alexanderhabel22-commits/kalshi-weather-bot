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

    texts = []

    def ask(model, text, schema):
        calls.append(model)
        texts.append(text)
        if "TRIAGE" in text:
            return {"review_now": True, "why": "x"}
        n = sum("FULL REVIEW" in x for x in texts)
        act = {1: "bench", 2: "none"}.get(n, "unbench")
        return {"summary": "ok", "alert": "", "accounts": [
            {"nick": "ace", "note": "Trades weather; fine.", "action": act, "reason": "market maker"}]}

    b = Boss(tmp_path, ask=ask, clock=lambda: t[0])
    assert b.step(boss_status(), [], None)            # first: changed -> straight to a review
    assert calls == ["claude-haiku-5-5"]              # Haiku since Nov 12 (Opus cost $24/day)
    assert "ace" in b.benches(t[0]) and b.state["notes"]["ace"].startswith("Trades")
    assert not b.step(boss_status(), [], None)        # too soon
    t[0] += 4000
    assert b.step(boss_status(), [], None)            # unchanged -> Haiku triage asks for review
    assert "TRIAGE" in texts[1] and "ace" in b.benches(t[0])
    t[0] += 4000
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


def test_hot_accounts_get_every_other_poll(tmp_path):
    cc = copycat.Copycat(tmp_path, clock=lambda: 1.0, req_gap=0)
    cc.watch = {f"a{i}": {} for i in range(20)}
    cc.hot = {"a7"}
    cc.watch_next = cc.scout_next = 10**12
    polled = []
    cc.poll = lambda nick, now: polled.append(nick)
    for _ in range(10):
        cc.step()
    assert polled.count("a7") >= 5


def test_sports_and_unknown_category_not_copied(tmp_path):
    lv, _ = mk(tmp_path)
    assert lv.maybe_copy(ACCT, FILL, dict(SNAP, category="Sports"), 1000.0) == "excluded category (Sports)"
    assert lv.maybe_copy(ACCT, FILL, dict(SNAP, category="unknown"), 1000.0).startswith("excluded")
    assert lv.maybe_copy(ACCT, FILL, dict(SNAP, category="Politics"), 1000.0) == "copied"


def test_global_feed_logs_watched_accounts_fast(tmp_path):
    from tests.test_copycat import FakeSession, trade, Resp

    class Feed(FakeSession):
        feed = []

        def get(self, url, params=None, timeout=None):
            if url.endswith("/social/trades") and not (params or {}).get("nickname"):
                self.calls.append((url, dict(params or {})))
                return Resp({"trades": self.feed, "cursor": None})
            return super().get(url, params, timeout)

    s = Feed()
    cc = copycat.Copycat(tmp_path, session=s, clock=lambda: 1.0, req_gap=0)
    cc.watch = {"sharp": {}}
    s.feed = [trade("a", taker="sharp"), trade("z", taker="stranger")]
    cc.poll_feed(1.0)                                # sharp not polled on its own yet: skipped
    s.trades = [trade("a", taker="sharp")]
    cc.poll("sharp", 2.0)                            # backfill
    s.feed = [trade("b", maker="sharp"), trade("a", taker="sharp")]
    cc.poll_feed(3.0)
    rows = [json.loads(x) for p in tmp_path.glob("copycat-*.jsonl") for x in p.read_text().splitlines()]
    fills = [r for r in rows if r["kind"] == "fill"]
    assert [(r["trade_id"], r["backfill"]) for r in fills] == [("a", True), ("b", False)]
    assert fills[1]["role"] == "maker"
    s.trades = [trade("b", maker="sharp"), trade("a", taker="sharp")]
    cc.poll("sharp", 4.0)                            # account poll doesn't double-log
    rows = [json.loads(x) for p in tmp_path.glob("copycat-*.jsonl") for x in p.read_text().splitlines()]
    assert len([r for r in rows if r["kind"] == "fill"]) == 2


def test_feed_keeps_running_during_snapshot_backlog(tmp_path):
    t = [0.0]
    cc = copycat.Copycat(tmp_path, clock=lambda: t[0], req_gap=0)
    feeds = []
    cc.poll_feed = lambda now: feeds.append(now)
    cc.snapshot = lambda ticker: (t.__setitem__(0, t[0] + 0.5), {})[1]   # each snapshot takes 0.5 s
    cc.snaps = [(0.0, 0, "a", f"T{i}", f"x{i}") for i in range(10)]
    cc.run_snaps(0.0)
    assert len(feeds) >= 4                         # ~every second while 5 s of snapshots ran


def test_fresh_start_ignores_old_fills(tmp_path):
    from datetime import datetime, timezone
    now = 1_762_830_000.0
    f = tmp_path / datetime.fromtimestamp(now - 3600, timezone.utc).strftime("copycat-%Y-%m-%d-%H.jsonl")
    old = {"kind": "fill", "nick": "sharp", "trade_id": "o1", "ticker": "T1", "side": "yes", "yes_price": 0.5,
           "backfill": False, "ts": now - 3600, "et": "x"}
    new = dict(old, trade_id="n1", v=copycat.ROW_V)
    snaps = [{"kind": "snap", "trade_id": t, "ticker": "T1", "delay": 0, "bid": 0.5, "ask": 0.52} for t in ("o1", "n1")]
    f.write_text("\n".join(json.dumps(r) for r in [old, new] + snaps))

    class S:
        headers = {}

        def get(self, url, params=None, timeout=None):
            from tests.test_copycat import Resp
            return Resp({"market": {}})
    cc = copycat.Copycat(tmp_path, session=S(), clock=lambda: now, req_gap=0)
    cc.watch = {"sharp": {}}
    st_ = cc.update_status(now)
    assert {a["nick"]: a["fills"] for a in st_["accounts"]}["sharp"] == 1


def test_boss_hard_daily_cap_and_failed_review_counts(tmp_path, monkeypatch):
    from wxbot import copycat_boss
    t = [100000.0]
    b = Boss(tmp_path, ask=lambda m, x, s: (_ for _ in ()).throw(RuntimeError("timeout")), clock=lambda: t[0])
    assert not b.step(boss_status(), [], None)
    assert b.state["deep_n"] == 1                     # a failed (billed) review still counts
    assert b.budget.spent() > 0
    b2 = Boss(tmp_path / "x" if (tmp_path / "x").mkdir() is None else None, ask=lambda m, x, s: {"review_now": True},
              clock=lambda: t[0])
    b2.budget.cap = 0.0001
    monkeypatch.setattr(copycat_boss, "MAX_DEEP_PER_DAY", 99)
    assert not b2.step(boss_status(), [], None)       # over the cap: no call at all
    assert b2.budget.state["refused"] >= 1 and b2.budget.state["calls"] == 0


def test_boss_digest_is_trimmed():
    from wxbot import copycat_boss
    st = {"accounts": [{"nick": f"a{i}", "fills": i + 1} for i in range(200)]}
    txt = digest(st, [], None, {})
    assert len(txt) <= copycat_boss.MAX_DIGEST_CHARS and "175 quieter accounts left out" in txt
    assert "- a199:" in txt and "- a0:" not in txt


def test_hard_dollar_account_stop(tmp_path):
    alerts = []
    lv, _ = mk(tmp_path, bankroll=1000.0)            # 10% = $100, so the $3 cap is what bites
    lv.alert = lambda t, b: alerts.append(b)
    lv.state["pnl"]["ace"] = -2.99
    lv.check_stops(1000.0)
    assert "ace" not in lv.benched()
    lv.state["pnl"]["ace"] = -3.0
    lv.check_stops(1000.0)
    assert "ace" in lv.benched() and len(alerts) == 1 and "paper" in alerts[0]
    lv.check_stops(1000.0)
    assert len(alerts) == 1                           # one push only
