import json

from wxbot import copycat
from wxbot.copycat_boss import Boss, digest
from wxbot.copycat_live import BANKROLL_URL, CONTROL_URL, SWITCH_URL, Live


class FakeClient:
    def __init__(self, held=0.0, fill=None, cash=1000.0):
        self.held, self.orders = held, []
        self.fill = fill
        self.cash = cash

    def shard_balances(self):
        return {0: self.cash, 2: 5.0}

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
    lv, rows = mk(tmp_path, bankroll=12.5)
    lv.alert = lambda t, b: alerts.append(t)
    lv.maybe_copy(ACCT, FILL, SNAP, 1000.0)          # 2 yes at 62c + 2c fee = $1.26
    done = lv.settle({"KXFOO-1": "no"}, 2000.0)
    assert done and lv.state["pnl"]["ace"] == -1.26
    assert "ace" in lv.benched()                      # -$1.26 < 10% of $12.50
    assert lv.state["halted"] is None                 # total stop is 25% = $3.13
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
        {"nick": "ace", "fills": 120, "settled": 40, "score": 88.0, "grade": "B+", "verdict": "passes",
         "detail": {"maker_pct": 80, "latest": [{"their_c": 40, "pay_c": 43}]}},
        {"nick": "busy", "fills": 9000, "settled": 3, "score": None, "verdict": "watching"}]}


def review(text):
    return {"summary": "ok", "accounts": [
        {"nick": "ace", "note": "Trades weather; fine.", "call": "skip", "source": "market_making",
         "predicted_c": -2.0, "market_maker": "yes", "mm_filter": "skip_maker_fills", "reason": "maker fills"}]}


def test_boss_shadow_logs_calls_never_benches(tmp_path):
    t = [100000.0]
    texts = []

    def ask(model, text, schema):
        texts.append(text)
        return review(text)

    b = Boss(tmp_path, ask=ask, clock=lambda: t[0])
    assert b.step(boss_status(), [], None)
    assert "- ace:" in texts[0] and "- busy:" not in texts[0]          # near-bar traders, not the busiest
    assert "our pay minus their price 3c" in texts[0]
    assert b.benches(t[0]) == {} and b.state["notes"]["ace"].startswith("Trades")
    c = b.state["calls"][-1]
    assert c["call"] == "skip" and c["mm_filter"] == "skip_maker_fills" and c["settled"] == 40
    assert c["plain_mm"] and c["rule_skip"].startswith("market maker")         # 120 fills, 80% maker
    assert b.summary(boss_status())["plain_mm"]["nicks"] == ["ace"]
    t[0] += 4000
    assert not b.step(boss_status(), [], None)                          # once a day
    t[0] += 23 * 3600
    assert b.step(boss_status(), [], None) and len(texts) == 2
    assert b.summary()["shadow"] and len(b.summary()["calls"]) == 2


def test_boss_cut_off_answer_is_an_error_and_retried(tmp_path):
    from types import SimpleNamespace
    t = [100000.0]
    resp = SimpleNamespace(stop_reason="max_tokens", usage=SimpleNamespace(input_tokens=100, output_tokens=8000),
                           content=[SimpleNamespace(type="text", text='{"summary": "cut')])
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: resp))
    b = Boss(tmp_path, client=client, clock=lambda: t[0])
    assert not b.step(boss_status(), [], None)
    assert "cut off" in b.state["last_error"] and b.budget.spent() > 0   # billed, and logged
    t[0] += 3 * 3600 + 1
    good = SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=100, output_tokens=50),
                           content=[SimpleNamespace(type="text", text=json.dumps(review("")))])
    client.messages.create = lambda **kw: good
    assert b.step(boss_status(), [], None) and b.state["last_error"] == ""


def test_boss_v1_state_is_reset(tmp_path):
    (tmp_path / "boss.json").write_text(json.dumps({"summary": "old", "benches": {"x": {"until": 1e12}},
                                                    "notes": {"x": "old"}}))
    b = Boss(tmp_path, ask=lambda *a: {})
    assert b.state["summary"] == "" and b.state["notes"] == {} and b.benches(0) == {}


def test_boss_digest_has_real_pnl_and_rule():
    from wxbot import copycat_boss
    txt = digest(boss_status(), [], {"pnl": {"ace": 1.5}, "total_pnl": 1.5, "open_cost": 0, "pool": 8})
    assert "$1.50" in txt and "maker 80%" in txt
    st = {"accounts": [{"nick": f"a{i}", "score": 75 + i / 100} for i in range(200)]}
    assert len(digest(st, [], None)) <= copycat_boss.MAX_DIGEST_CHARS
    assert copycat_boss.rule_skip({"detail": {"bands": {"other": 15, "5-25c": 5}}}) == "mostly <=5c or >=95c"


def test_scoreboard_compares_boss_with_plain_rule():
    sys_path = __import__("sys").path
    from pathlib import Path
    sys_path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import boss_scoreboard as sb
    calls = [{"nick": "bad", "at": 0, "call": "skip", "rule_skip": ""},
             {"nick": "good", "at": 0, "call": "keep", "rule_skip": ""}]
    trades = {"bad": [{"ok": True, "c": -10.0, "ts": 5, "role": "maker"}] * 150,
              "good": [{"ok": True, "c": 2.0, "ts": 5}] * 150}
    calls[0]["mm_filter"] = "skip_maker_fills"
    r = sb.score(sb.forward(calls, trades))
    assert r["boss_gap_c"] == 12.0 and r["verdict"].startswith("PASS")
    assert r["mm_filter"]["boss_removed"] == {"copies": 150, "c": -10.0}
    calls[0]["rule_skip"] = "market maker"                         # the plain rule saw it too
    assert sb.score(sb.forward(calls, trades))["verdict"].startswith("FAIL")


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


def test_boss_hard_daily_cap_and_failed_review_counts(tmp_path):
    t = [100000.0]
    b = Boss(tmp_path, ask=lambda m, x, s: (_ for _ in ()).throw(RuntimeError("timeout")), clock=lambda: t[0])
    assert not b.step(boss_status(), [], None)
    assert b.state["deep_n"] == 1                     # a failed (billed) review still counts
    assert b.budget.spent() > 0
    (tmp_path / "x").mkdir()
    b2 = Boss(tmp_path / "x", ask=lambda m, x, s: review(x), clock=lambda: t[0])
    b2.budget.cap = 0.0001
    assert not b2.step(boss_status(), [], None)       # over the cap: no call at all
    assert b2.budget.state["refused"] >= 1 and b2.budget.state["calls"] == 0



def test_hard_dollar_account_stop(tmp_path):
    alerts = []
    lv, _ = mk(tmp_path, bankroll=1000.0)            # 10% = $100, so the $5 cap is what bites
    lv.alert = lambda t, b: alerts.append(b)
    lv.state["pnl"]["ace"] = -4.99
    lv.check_stops(1000.0)
    assert "ace" not in lv.benched()
    lv.state["pnl"]["ace"] = -5.0
    lv.check_stops(1000.0)
    assert "ace" in lv.benched() and len(alerts) == 1 and "paper" in alerts[0]
    lv.check_stops(1000.0)
    assert len(alerts) == 1                           # one push only


def test_trader_limit_counts_open_copies_and_one_copy_per_event(tmp_path):
    lv, _ = mk(tmp_path, bankroll=1000.0)            # pool $150, so only the $5 trader limit bites
    acct = dict(ACCT, size=3)
    assert lv.maybe_copy(acct, dict(FILL, ticker="KXG-A-X"), SNAP, 1000.0) == "copied"      # 3 x 62c + fee = $1.88
    assert lv.maybe_copy(acct, dict(FILL, ticker="KXG-A-Y"), SNAP, 1000.0) == "already copied this event"
    assert lv.maybe_copy(acct, dict(FILL, ticker="KXG-B-X"), SNAP, 1000.0) == "copied"      # $3.76 at risk
    assert lv.maybe_copy(acct, dict(FILL, ticker="KXG-C-X"), SNAP, 1000.0) == "copied"      # cut to 1 contract
    assert lv.client.orders[-1][3] == 1 and 4.3 < lv.at_risk("ace") <= 5.0
    assert lv.maybe_copy(acct, dict(FILL, ticker="KXG-D-X"), SNAP, 1000.0).startswith("trader limit")
    assert lv.maybe_copy(dict(acct, nick="bee"), dict(FILL, nick="bee", ticker="KXG-D-X"), SNAP, 1000.0) == "copied"
    lv.settle({"KXG-A-X": "no"}, 1100.0)             # a settled loss still counts; the event frees up
    assert lv.maybe_copy(acct, dict(FILL, ticker="KXG-E-X"), SNAP, 1101.0).startswith("trader limit")
    lv.settle({"KXG-B-X": "yes", "KXG-C-X": "yes"}, 1200.0)   # wins: net settled loss is now $0.40
    assert round(lv.at_risk("ace"), 2) == 0.4
    lv.state["pnl"]["ace"] = 2.0                     # banked real profit = earned room: limit $7
    assert lv.account_limit("ace") == 7.0 and lv.account_limit("bee") == 5.0
    assert lv.maybe_copy(acct, dict(FILL, ticker="KXG-A-Y"), SNAP, 1201.0) == "copied"


def test_cash_floor_keeps_cash_for_main_bot(tmp_path):
    lv, _ = mk(tmp_path, client=FakeClient(cash=10.50))
    assert lv.maybe_copy(ACCT, FILL, SNAP, 1000.0).startswith("cash floor")
    lv2, _ = mk(tmp_path, client=FakeClient(cash=11.0))
    assert lv2.maybe_copy(ACCT, FILL, SNAP, 1000.0) == "copied"
    assert lv2.client.orders[-1][3] == 1                # trimmed to 1 contract to stay above the floor


def test_total_dollar_halt(tmp_path):
    alerts = []
    lv, _ = mk(tmp_path, bankroll=1000.0)               # 25% = $250, so the $8 cap is what bites
    lv.alert = lambda t, b: alerts.append(t)
    lv.state["total"] = -7.99
    lv.check_stops(1000.0)
    assert not lv.state["halted"]
    lv.state["total"] = -8.0
    lv.check_stops(1000.0)
    assert lv.state["halted"] and alerts == ["Copycat halted"] and lv.mode(1000.0) == "off"


def test_earn_back_and_second_bench_is_final(tmp_path):
    from wxbot.copycat_live import EARN_FINAL_S
    lv, _ = mk(tmp_path)
    lv.state["pnl"]["ace"] = -5.0
    lv.check_stops(1000.0)
    assert "ace" in lv.benched() and lv.earn_back_open(1000.0) == {"ace": 1000.0}
    lv.unbench("ace", 5000.0)
    assert "ace" not in lv.benched() and lv.state["pnl"]["ace"] == 0.0 and lv.state["pnl_hist"]["ace"] == -5.0
    lv.state["pnl"]["ace"] = -5.0                       # benched again 1 day after coming back: final
    lv.check_stops(5000.0 + 86400)
    assert "ace" in lv.benched() and lv.earn_back_open(5000.0 + 86400) == {}
    assert lv.earn_back_open(5000.0 + 86400 + EARN_FINAL_S + 1) == {"ace": 5000.0 + 86400}


# ---- two stages (Nov 15): trial bar onto real money, then real P&L decides
def _status_cc(tmp_path, lv):
    cc = copycat.Copycat(tmp_path, clock=lambda: 1000.0, req_gap=0, live=lv)
    cc.watch = {n: {} for n in ("t1", "t2", "t3", "t4", "t5", "full")}
    return cc


def test_trial_bar_admits_up_to_slots_and_paper_never_drops(tmp_path):
    lv, _ = mk(tmp_path)
    cc = _status_cc(tmp_path, lv)
    accts = [{"nick": f"t{i}", "verdict": "ready", "score": 78.0, "settled": 25, "fills": 30, "grade": "C+"}
             for i in range(1, 6)] + [{"nick": "full", "verdict": "passes", "score": 81.0, "settled": 60,
                                       "fills": 80, "grade": "B-", "size": 1}]
    for a in accts:
        cc.judge(a)
    on = lv.state["on_real"]
    assert sorted(on) == ["full", "t1", "t2", "t3", "t4"]          # 4 trial slots; the full bar is not limited
    assert on["t1"]["tier"] == "trial" and on["t1"]["budget"] == 1.8 and on["full"]["budget"] == 5.0
    assert accts[0]["verdict"] == "passes" and accts[4]["verdict"] == "ready"
    # paper turns bad: an admitted trader gets no strike and is never dropped by paper
    bad = {"nick": "t1", "verdict": "fails", "score": 60.0, "settled": 200, "fills": 300, "grade": "F"}
    for _ in range(5):
        cc.judge(bad)
    assert "t1" not in cc.dropped and bad["verdict"] == "fails" and bad["on_real"]["budget"] == 1.8
    # its real budget also caps what it can have at risk
    assert lv.account_limit("t1") == 1.8 and lv.account_limit("nobody") == 5.0


def test_admitted_trader_stays_live_in_status_without_category_blocks():
    rows_, res = [], {}
    for i in range(60):
        tid = f"x{i}"
        rows_ += [{"kind": "fill", "trade_id": tid, "nick": "ace", "ticker": f"KXS-{i}", "side": "yes",
                   "yes_price": 0.50, "backfill": False, "lag_s": 9, "ts": 1000.0 + i, "et": "x"},
                  {"kind": "snap", "trade_id": tid, "ticker": f"KXS-{i}", "delay": 0, "bid": 0.50, "ask": 0.52}]
        res[f"KXS-{i}"] = "no"                                          # loses every paper copy
    cats = {"KXS": "Sports"}
    a = copycat.build_status(rows_, res, {"ace": {}}, now=2000.0, cats=cats)["accounts"][0]
    assert a["verdict"] == "fails" and a["blocked_cats"] == ["Sports"]
    on = {"ace": {"tier": "trial", "budget": 1.0}}
    a = copycat.build_status(rows_, res, {"ace": {}}, now=2000.0, cats=cats, on_real=on)["accounts"][0]
    assert a["verdict"] == "passes" and a["blocked_cats"] == [] and a["size"] == 1
    a = copycat.build_status(rows_, res, {"ace": {}}, now=2000.0, cats=cats, on_real=on,
                             account_stops={"ace": "real budget used"})["accounts"][0]
    assert a["verdict"] == "benched"


def test_real_budget_sends_back_to_paper(tmp_path):
    lv, _ = mk(tmp_path)
    cc = _status_cc(tmp_path, lv)
    lv.state["on_real"] = {"t1": {"tier": "trial", "budget": 1.0}, "full": {"tier": "full", "budget": 5.0}}
    lv.state["pnl"] = {"t1": -1.02, "full": -3.0}
    cc.real_budget_check(1000.0)
    assert "t1" in lv.benched() and "t1" not in lv.state["on_real"]
    assert "full" in lv.state["on_real"] and "full" not in lv.benched()
    assert copycat.real_budget(77.5) == 1.0 and copycat.real_budget(80) == 5.0 and copycat.real_budget(70) == 1.0


def test_restore_once_brings_back_paper_dropped_traders(tmp_path):
    lv, _ = mk(tmp_path)
    cc = copycat.Copycat(tmp_path, clock=lambda: 1000.0, req_gap=0, live=lv)
    cc.dropped = {"olekbannick7": {"at": 900, "reason": "failed its paper test 3 times", "score": 76.5},
                  "DocDahmer": {"at": 900, "reason": "long-run loser"}, "other": {"at": 900, "reason": "failed its paper test 3 times"}}
    cc.restore_once()
    assert "olekbannick7" in cc.watch and "olekbannick7" not in cc.dropped
    assert lv.state["on_real"]["olekbannick7"]["restored"] and "DocDahmer" in cc.dropped and "other" in cc.dropped
    lv.state["on_real"].clear()
    cc.dropped["olekbannick7"] = {"at": 950, "reason": "failed its paper test 3 times"}
    cc.restore_once()                                                  # one time only
    assert "olekbannick7" in cc.dropped


def test_first_real_copy_push_once_per_trader_ever(tmp_path):
    from datetime import datetime, timezone

    from tests.test_copycat import FakeSession, trade
    now = datetime(2026, 11, 10, 20, 0, 15, tzinfo=timezone.utc).timestamp()
    pushes = []
    lv, _ = mk(tmp_path)
    lv.state["pnl"] = {"oldie": 0.5}                   # copied before this existed: already told

    def run(cc, tid, ticker, nick="sharp"):
        t = dict(trade(tid, taker=nick, px="0.61", t="2026-11-10T20:00:05Z"), ticker=ticker)
        cc.hot, cc.acct = {nick}, {nick: {"nick": nick, "verdict": "passes", "size": 1}}
        cc.based.add(nick)
        cc.ingest(nick, [t], now, src="feed")

    cc = copycat.Copycat(tmp_path, session=FakeSession(), clock=lambda: now, req_gap=0, live=lv,
                         alert=lambda t, b: pushes.append((t, b)))
    run(cc, "a", "KXA-1")
    run(cc, "b", "KXB-1")                               # second copy of the same trader: no push
    run(cc, "c", "KXC-1", nick="oldie")
    assert len(pushes) == 1 and "sharp" in pushes[0][1] and "KXA-1" in pushes[0][1] and "65c" in pushes[0][1]
    cc2 = copycat.Copycat(tmp_path, session=FakeSession(), clock=lambda: now, req_gap=0, live=lv,
                          alert=lambda t, b: pushes.append((t, b)))     # restart
    run(cc2, "d", "KXD-1")
    assert len(pushes) == 1 and lv.state["first_told"] == ["oldie", "sharp"]


def test_score_80_does_not_use_a_trial_slot(tmp_path):
    lv, _ = mk(tmp_path)
    cc = _status_cc(tmp_path, lv)
    lv.state["on_real"] = {f"t{i}": {"tier": "trial", "budget": 1.0} for i in range(1, 5)}
    lv.state["on_real"]["t1"]["budget"] = 5.0
    cc.judge({"nick": "t1", "verdict": "passes", "score": 81.0, "settled": 59, "fills": 90, "grade": "B-"})
    assert lv.state["on_real"]["t1"]["tier"] == "full"                 # upgraded: slot freed
    a = {"nick": "t5", "verdict": "ready", "score": 78.0, "settled": 25, "fills": 30, "grade": "C+"}
    cc.judge(a)
    assert lv.state["on_real"]["t5"]["tier"] == "trial" and a["verdict"] == "passes"
    b = {"nick": "full", "verdict": "ready", "score": 80.5, "settled": 40, "fills": 60, "grade": "B-"}
    cc.judge(b)                                                        # slots full, but a full-bar score gets in
    assert lv.state["on_real"]["full"]["tier"] == "full" and b["verdict"] == "passes"


def test_trial_promoted_to_full_on_real_results(tmp_path):
    pushes = []
    lv, _ = mk(tmp_path)
    cc = copycat.Copycat(tmp_path, clock=lambda: 5000.0, req_gap=0, live=lv, alert=lambda t, b: pushes.append(t))
    cc.watch = {"tr": {}}
    lv.state["on_real"] = {"tr": {"tier": "trial", "budget": 1.0, "at": 100}}
    acct = {"nick": "tr", "verdict": "fails", "score": 70.0, "settled": 80, "fills": 100, "grade": "C-"}

    def give(n, win_every):
        lv.state["positions"] = {f"KXE{i}-A": {"nick": "tr", "side": "yes", "n": 1, "cost": 0.5, "ts": 200 + i,
                                               "settled": 300 + i, "pnl": 0.5 if i % win_every == 0 else -0.5}
                                 for i in range(n)}
    give(2, 1)                                   # 2 lucky wins: not enough
    cc.judge(dict(acct))
    assert lv.state["on_real"]["tr"]["tier"] == "trial" and not pushes
    give(16, 2)                                  # 16 copies, 8 won 8 lost: P&L $0, not promoted
    cc.judge(dict(acct))
    assert lv.state["on_real"]["tr"]["tier"] == "trial"
    give(16, 1)                                  # 16 events all won: real money earned it
    cc.judge(dict(acct))
    o = lv.state["on_real"]["tr"]
    assert o["tier"] == "full" and o["budget"] == 5.0 and o["real_check"]["promote"] and len(pushes) == 1
    assert copycat.real_promotion([])["promote"] is False


def test_promotion_push_carries_boss_review(tmp_path):
    import threading as th
    pushes, done = [], th.Event()

    class B:
        def review_promotion(self, nick, rec, acct):
            return "looks like real skill on NBA spreads"

    lv, _ = mk(tmp_path)
    cc = copycat.Copycat(tmp_path, clock=lambda: 1.0, req_gap=0, live=lv,
                         alert=lambda t, b: (pushes.append(b), done.set()))
    cc.boss = B()
    cc.tell_promotion("tr", {"copies": 16, "events": 16, "pnl": 8.0, "c": 50.0, "shrunk_c": 7.0}, {})
    assert done.wait(5) and len(pushes) == 1 and "Boss: looks like" in pushes[0] and "tr: 16 real copies" in pushes[0]


def test_trial_that_makes_nothing_ends_and_frees_slot(tmp_path):
    pushes = []
    lv, _ = mk(tmp_path)
    cc = copycat.Copycat(tmp_path, clock=lambda: 1000.0, req_gap=0, live=lv, alert=lambda t, b: pushes.append(t))
    lv.state["on_real"] = {"flat": {"tier": "trial", "budget": 5.0,
                                    "real_check": {"copies": 22, "events": 16, "pnl": -0.1}},
                           "young": {"tier": "trial", "budget": 5.0,
                                     "real_check": {"copies": 8, "events": 8, "pnl": -0.5}}}
    lv.state["pnl"] = {"flat": -0.1, "young": -0.5}
    cc.real_budget_check(1000.0)
    assert "flat" not in lv.state["on_real"] and "flat" in lv.benched() and pushes == ["Copycat: trial ended"]
    assert "young" in lv.state["on_real"]
    assert lv.earn_back_open(1000.0).get("flat") == 1000.0      # can earn its way back (trial bar on fresh copies)
    assert copycat.EARN_N == 20 and copycat.EARN_EVENTS == 15 and copycat.EARN_SCORE == 77.5


def test_boss_promotion_note_is_capped_context(tmp_path):
    seen = []
    b = Boss(tmp_path, ask=lambda m, x, s: (seen.append(x), {"note": "Looks like skill on NBA favorites. " * 20})[1])
    acct = boss_status()["accounts"][0]
    note = b.review_promotion("ace", {"copies": 22, "events": 16, "pnl": 1.2, "c": 5.4, "shrunk_c": 1.1}, acct)
    assert len(note) <= 300 and "PROMOTION NOTE" in seen[0] and "- ace:" in seen[0]
    assert b.budget.state["calls"] == 1                      # through the daily cap
    assert Boss(tmp_path / "none" if (tmp_path / "none").mkdir() is None else None).review_promotion("ace", {}, acct) is None


def test_two_contract_step_on_real_results_and_step_down(tmp_path):
    pushes = []
    lv, _ = mk(tmp_path)
    cc = copycat.Copycat(tmp_path, clock=lambda: 5000.0, req_gap=0, live=lv, alert=lambda t, b: pushes.append(t))
    cc.watch = {"pro": {}, "tr": {}}
    lv.state["on_real"] = {"pro": {"tier": "full", "budget": 5.0, "at": 100}}
    acct = {"nick": "pro", "verdict": "passes", "score": 82.0, "settled": 200, "fills": 300, "grade": "B"}

    def give(n, win):                      # n copies, one per event, settled in order; win(i) -> bool
        lv.state["positions"] = {f"KXS{i}-A": {"nick": "pro", "side": "yes", "n": 1, "cost": 0.5, "ts": 200 + i,
                                               "settled": 300 + i, "pnl": 0.5 if win(i) else -0.5} for i in range(n)}

    give(30, lambda i: i % 3 != 0)         # 30 copies: too few
    a = dict(acct); cc.judge(a)
    assert a["size"] == 1 and not pushes
    give(45, lambda i: i % 3 != 0)         # 45 copies, 2/3 won: +$7.5, ~+17c raw -> step 2
    a = dict(acct); cc.judge(a)
    assert a["size"] == 2 and pushes == ["Copycat: first 2-contract step-up"]
    give(45, lambda i: i % 3 != 0 and i < 36)   # last copies lose: P&L falls >$3 below peak -> back to 1
    a = dict(acct); cc.judge(a)
    assert a["size"] == 1 and lv.state["on_real"]["pro"]["step"] == 1
    trial = {"nick": "tr", "verdict": "ready", "score": 92.0, "settled": 60, "fills": 90, "grade": "A-", "size": 3}
    cc.judge(trial)
    assert trial["size"] == 1                # new admissions always start at 1 contract


def test_no_balance_index_series_skipped_after_reject(tmp_path):
    class Rej(FakeClient):
        def buy_now(self, ticker, side, price, n, coid):
            self.orders.append(ticker)
            raise RuntimeError('HTTP 404 {"error":{"code":"insufficient_shard_balance"}}')
    lv, _ = mk(tmp_path, client=Rej())
    assert lv.maybe_copy(ACCT, dict(FILL, ticker="KXATP-1-A"), SNAP, 1000.0).startswith("order failed")
    assert lv.maybe_copy(ACCT, dict(FILL, ticker="KXATP-2-B"), SNAP, 1001.0) == "no cash on this market's exchange index"
    assert lv.client.orders == ["KXATP-1-A"]
    lv.no_index["KXATP"] -= 7 * 3600                                    # 7 hours later: tried again
    assert lv.maybe_copy(ACCT, dict(FILL, ticker="KXATP-3-C"), SNAP, 1002.0).startswith("order failed")
