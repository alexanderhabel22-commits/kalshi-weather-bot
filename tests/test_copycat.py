import json

from wxbot import copycat


class Resp:
    def __init__(self, js, status=200):
        self._js = js
        self.status_code = status

    def json(self):
        return self._js


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.calls = []
        self.trades = []

    def get(self, url, params=None, timeout=None):
        params = params or {}
        self.calls.append((url, dict(params)))
        if url.endswith("/leaderboard"):
            return Resp({"rank_list": [{"nickname": "sharp", "is_anonymous": False},
                                       {"nickname": "hidden", "is_anonymous": False},
                                       {"nickname": "small", "is_anonymous": False}]})
        if url.endswith("/social/trades"):
            n = params.get("nickname")
            if n == "sharp":
                return Resp({"trades": self.trades[: params.get("limit", 50)]})
            if n == "small":
                return Resp({"trades": [{"trade_id": "x"}]})
            return Resp({"trades": [], "visibility_state": "hidden"})
        if url.endswith("/profile/metrics"):
            pnl = 900_000 if params.get("nickname") == "sharp" else 50
            return Resp({"metrics": {"pnl": pnl * 10_000, "volume": 5, "num_markets_traded": 3}})
        if url.endswith("/profile"):
            return Resp({"social_profile": {"top_categories": ["Sports"], "joined_at": "2024-01-01"}})
        if url.endswith("/series"):
            return Resp({"series": [{"ticker": "KXNBAGAME", "category": "Sports"},
                                    {"ticker": "SENATEKS", "category": "Elections"}]})
        if "/series/" in url:
            return Resp({"series": {"category": "Politics"}})
        if url.endswith("/markets"):
            asked = params["tickers"].split(",")
            return Resp({"markets": [{"ticker": t, "result": "yes" if t.startswith("DONE") else ""} for t in asked]})
        if "/markets/" in url:
            return Resp({"market": {"yes_bid_dollars": "0.60", "yes_ask_dollars": "0.62",
                                    "last_price_dollars": "0.61", "status": "active"}})
        raise AssertionError(url)


def trade(tid, taker=None, maker=None, side="yes", px="0.55", t="2026-11-10T20:00:00Z"):
    return {"trade_id": tid, "ticker": "KXTEST-1", "price_dollars": px, "count_fp": "10.00",
            "taker_side": side, "taker_action": "buy", "taker_nickname": taker or "",
            "maker_nickname": maker or "", "create_date": t}


def rows(tmp_path):
    out = []
    for p in sorted(tmp_path.glob("copycat-*.jsonl")):
        out += [json.loads(x) for x in p.read_text().splitlines()]
    return out


def test_sharp_side_taker_and_maker():
    assert copycat.sharp_side(trade("a", taker="s", side="yes"), "s") == ("taker", "yes")
    assert copycat.sharp_side(trade("a", maker="s", side="yes"), "s") == ("maker", "no")
    sell = dict(trade("a", taker="s", side="yes"), taker_action="sell")
    assert copycat.sharp_side(sell, "s") == ("taker", "no")
    assert copycat.sharp_side(trade("a", taker="x"), "s") == (None, None)


def test_watch_keeps_only_public_and_profitable(tmp_path):
    s = FakeSession()
    s.trades = [trade("t1", taker="sharp")]
    sl = copycat.Copycat(tmp_path, session=s, clock=lambda: 1_000_000.0, req_gap=0)
    sl.build_watch()
    assert list(sl.watch) == ["sharp"]
    assert sl.watch["sharp"]["pnl"] == 900_000


def test_new_fill_logged_and_snapshotted_backfill_not(tmp_path):
    now = [1_762_800_000.0]
    s = FakeSession()
    s.trades = [trade("old", taker="sharp")]
    sl = copycat.Copycat(tmp_path, session=s, clock=lambda: now[0], req_gap=0)
    sl.watch = {"sharp": {}}
    sl.watch_next = 10 ** 12
    sl.step()                                   # first poll: history is backfill, no snaps
    assert not sl.snaps
    s.trades = [trade("new", maker="sharp", side="no", px="0.40"), trade("old", taker="sharp")]
    now[0] += 30
    sl.step()
    fills = [r for r in rows(tmp_path) if r["kind"] == "fill"]
    assert [(f["trade_id"], f["backfill"]) for f in fills] == [("old", True), ("new", False)]
    new = fills[1]
    assert new["role"] == "maker" and new["side"] == "yes" and new["yes_price"] == 0.40
    snaps = [r for r in rows(tmp_path) if r["kind"] == "snap"]
    assert [x["delay"] for x in snaps] == [0]  # the +0 snapshot runs in the same step
    assert len(sl.snaps) == len(copycat.SNAP_DELAYS) - 1
    now[0] += 400
    sl.step()
    assert sorted(r["delay"] for r in rows(tmp_path) if r["kind"] == "snap") == [0, 60, 300]


def test_never_touches_order_endpoints(tmp_path):
    s = FakeSession()
    s.trades = [trade("t1", taker="sharp")]
    sl = copycat.Copycat(tmp_path, session=s, clock=lambda: 1_000_000.0, req_gap=0)
    sl.step()
    assert not any("/portfolio" in u or "/orders" in u for u, _ in s.calls)
    assert not hasattr(s, "post")


def test_grade_follow_and_settle():
    rows_ = [
        {"kind": "fill", "trade_id": "a", "nick": "sharp", "ticker": "T", "side": "yes",
         "yes_price": 0.50, "backfill": False, "lag_s": 20},
        {"kind": "snap", "trade_id": "a", "ticker": "T", "delay": 0, "bid": 0.52, "ask": 0.54},
        {"kind": "snap", "trade_id": "a", "ticker": "T", "delay": 300, "bid": 0.60, "ask": 0.62},
        {"kind": "fill", "trade_id": "b", "nick": "sharp", "ticker": "T", "side": "no",
         "yes_price": 0.50, "backfill": True},
    ]
    out = copycat.grade(rows_, {"T": "yes"})
    o = out["sharp"]
    assert len(o["lag"]) == 1                          # backfill ignored
    assert round(o["move_300"][0], 1) == 11.0          # mid 0.61 - 0.50
    assert round(o["follow_300"][0], 1) == 5.0         # mid 0.61 - ask 0.54 - 2c taker fee
    assert o["settle"] == []                           # ask ran 4c past their price: real money would
    assert o["trades"][0]["c"] == 44.0 and o["trades"][0]["ok"] is False   # skip it, so it is not scored
    rows_[1] = dict(rows_[1], bid=0.50, ask=0.52)      # within 2c: scored
    o = copycat.grade(rows_, {"T": "yes"})["sharp"]
    assert round(o["settle"][0], 1) == 46.0 and o["settle_ev"] == ["T"]   # 1.00 - 0.52 - 2c fee


def test_status_verdicts(tmp_path):
    rows_ = []
    for i in range(120):
        tid = f"t{i}"
        rows_ += [{"kind": "fill", "trade_id": tid, "nick": "good", "ticker": f"T{i}", "side": "yes",
                   "yes_price": 0.50, "backfill": False, "lag_s": 9, "ts": 1000.0 + i, "et": "x"},
                  {"kind": "snap", "trade_id": tid, "ticker": f"T{i}", "delay": 0, "bid": 0.50, "ask": 0.52}]
    results = {f"T{i}": ("yes" if i % 10 < 8 else "no") for i in range(120)}   # 80% at 52c
    s = copycat.build_status(rows_, results, {"good": {"pnl": 1}, "new": {"pnl": 2}}, now=2000.0)
    acc = {a["nick"]: a for a in s["accounts"]}
    assert acc["good"]["fills"] == 120 and acc["good"]["verdict"] == "passes"
    assert acc["new"]["verdict"] == "watching"
    assert len(s["recent"]) == 40
    bad = {f"T{i}": ("yes" if i % 10 < 4 else "no") for i in range(120)}
    s2 = copycat.build_status(rows_, bad, {"good": {}}, now=2000.0)
    assert s2["accounts"][0]["verdict"] == "watching"   # weak at 120: tested until 150 (B+ fast-tracks at 100)


def test_update_status_writes_file(tmp_path):
    s = FakeSession()
    cc = copycat.Copycat(tmp_path, session=s, clock=lambda: 1_000_000.0, req_gap=0)
    cc.watch = {"sharp": {"pnl": 5}}
    st_ = cc.update_status(1_000_000.0)
    assert (tmp_path / "status.json").exists() and st_["mode"] == "paper only"


def test_scout_adds_profitable_names_from_global_feed(tmp_path):
    s = FakeSession()
    orig = s.get

    def get(url, params=None, timeout=None):
        params = params or {}
        if url.endswith("/social/trades") and "nickname" not in params:
            s.calls.append((url, dict(params)))
            return Resp({"trades": [trade("g1", taker="sharp", maker="small")], "cursor": ""})
        return orig(url, params, timeout)
    s.get = get
    cc = copycat.Copycat(tmp_path, session=s, clock=lambda: 1_000_000.0, req_gap=0)
    cc.scout()
    assert list(cc.watch) == ["sharp"] and cc.watch["sharp"]["src"] == "scout"
    assert "small" in cc.checked
    n_calls = len(s.calls)
    cc.scout()                                   # already checked: no new lookups
    assert len(s.calls) == n_calls + 1


def test_rolling_window_drops_then_readmits(tmp_path):
    def rows_for(outcomes):
        out, res = [], {}
        for i, win in enumerate(outcomes):
            tid = f"t{i}"
            out += [{"kind": "fill", "trade_id": tid, "nick": "a", "ticker": f"T{i}", "side": "yes",
                     "yes_price": 0.50, "backfill": False, "ts": 1000.0 + i},
                    {"kind": "snap", "trade_id": tid, "ticker": f"T{i}", "delay": 0, "bid": 0.48, "ask": 0.50}]
            res[f"T{i}"] = "yes" if win else "no"
        return out, res
    good = [i % 10 < 8 for i in range(200)]
    bad = [i % 10 < 2 for i in range(50)]
    v = lambda o: copycat.build_status(*rows_for(o), {"a": {}}, 2000.0)["accounts"][0]  # noqa: E731
    a = v(good[:150])
    assert a["score"] >= 90 and a["grade"].startswith("A") and a["verdict"] == "passes" and a["size"] == 2
    a = v(good[:150] + bad)                      # strong record, awful last 50: benched as a slump
    assert a["verdict"] == "slump" and a["size"] == 0
    a = v(good[:150] + bad + good[:50])          # recovers: back in
    assert a["verdict"] == "passes"
    a = v(bad + bad)                             # bad at 100 copies: no fast track, still testing
    assert a["verdict"] == "watching"
    a = v(bad + bad + bad)                       # judged at 150: fails
    assert a["grade"] == "F" and a["score"] < 60 and a["verdict"] == "fails"


def test_leeway_grows_with_banked_profit_and_score():
    assert copycat.leeway_c(0) == 5 and copycat.leeway_c(-3) == 5
    assert copycat.leeway_c(8) == 13 and copycat.leeway_c(100) == 20
    assert copycat.leeway_c(100, 99) == 25 and copycat.leeway_c(0, 70) == 5


def test_score_letters():
    assert copycat.score_grade([1.0] * 10) == (None, "-")
    sc, lt = copycat.score_grade([0.0, 2.0] * 50)       # mean +1c, tight: low just under +1
    assert 80 <= sc < 83 and lt == "B-"
    sc, lt = copycat.score_grade([-20.0, 10.0] * 50)    # mean -5c
    assert lt in ("D-", "F", "D", "D+") and sc < 70


def test_taker_fee():
    assert copycat.taker_fee_c(0.50) == 2.0
    assert copycat.taker_fee_c(0.95) == 1.0
    assert copycat.taker_fee_c(0.99) == 1.0


def _fail_rows(cc, nick, n, t0):
    for i in range(n):
        tid = f"{nick}{i}"
        cc.write({"kind": "fill", "v": copycat.ROW_V, "trade_id": tid, "nick": nick, "ticker": f"{nick}T{i}",
                  "side": "yes", "yes_price": 0.50, "backfill": False, "lag_s": 5, "ts": t0 + i})
        cc.write({"kind": "snap", "trade_id": tid, "nick": nick, "ticker": f"{nick}T{i}", "delay": 0,
                  "bid": 0.48, "ask": 0.50, "ts": t0 + i})
        cc.results[f"{nick}T{i}"] = "yes" if i % 10 < 2 else "no"


def test_failed_test_drops_account_then_readmits_from_zero(tmp_path):
    now = [1_000_000.0]
    cc = copycat.Copycat(tmp_path, session=FakeSession(), clock=lambda: now[0], req_gap=0)
    cc.watch = {"loser": {"pnl": 500, "since": 0}, "sharp": {"pnl": 900_000, "since": 0}}
    _fail_rows(cc, "loser", 150, now[0] - 5000)
    st_ = cc.update_status(now[0])                           # 1st fail: a second chance, re-test at 200
    a = next(x for x in st_["accounts"] if x["nick"] == "loser")
    assert "loser" in cc.watch and a["chances_left"] == 2 and a["retest_at"] == 200
    cc.update_status(now[0])                                 # same 150 copies: not judged again
    assert cc.watch["loser"]["fails_in_row"] == 1
    for k in (1, 2):
        for i in range(50):
            tid = f"loser-{k}-{i}"
            cc.write({"kind": "fill", "v": copycat.ROW_V, "trade_id": tid, "nick": "loser", "ticker": tid,
                      "side": "yes", "yes_price": 0.50, "backfill": False, "lag_s": 5, "ts": now[0] - 2000 + 100 * k + i})
            cc.write({"kind": "snap", "trade_id": tid, "nick": "loser", "ticker": tid, "delay": 0,
                      "bid": 0.48, "ask": 0.50, "ts": now[0]})
            cc.results[tid] = "no"
        st_ = cc.update_status(now[0])
    assert "loser" not in cc.watch and cc.dropped["loser"]["reason"] == "failed its paper test 3 times"
    assert all(a["nick"] != "loser" for a in st_["accounts"])
    assert st_["scan"]["dropped"] == 1 and st_["dropped"][0]["grade"] == "F"
    saved = json.loads((tmp_path / "watch.json").read_text())
    assert "loser" in saved["dropped"]                       # survives a restart
    cc.poll_feed(now[0])                                     # feed / scout never queue it again
    assert "loser" not in cc.scout_queue and cc.is_dropped("loser")
    now[0] += copycat.DROP_DAYS * 86400 + 1                  # 30 days later: eligible again
    assert not cc.is_dropped("loser") and "loser" not in cc.dropped
    cc.watch["loser"] = {"pnl": 500, "since": now[0]}        # re-scouted: old copies don't count
    a = {x["nick"]: x for x in cc.update_status(now[0])["accounts"]}["loser"]
    assert a["fills"] == 0 and a["verdict"] == "watching"


def test_recheck_drops_long_run_losers(tmp_path):
    s = FakeSession()
    orig = s.get

    def get(url, params=None, timeout=None):
        params = params or {}
        if url.endswith("/profile/metrics"):
            s.calls.append((url, dict(params)))
            pnl = -40 if params.get("since_day_before") == 30 else 5000
            return Resp({"metrics": {"pnl": pnl * 10_000}})
        return orig(url, params, timeout)
    s.get = get
    now = [1_000_000.0]
    cc = copycat.Copycat(tmp_path, session=s, clock=lambda: now[0], req_gap=0)
    cc.watch = {"cold": {"pnl": 5000, "checked": now[0] - copycat.RECHECK_S - 1},
                "fresh": {"pnl": 5000, "checked": now[0]}}
    cc.recheck()
    assert cc.watch["cold"]["strikes"] == 1 and cc.watch["cold"]["pnl_30d"] == -40   # one bad month: kept
    assert "strikes" not in cc.watch["fresh"]                                        # not due yet
    now[0] += copycat.RECHECK_S + 1
    cc.recheck()                                                                     # still losing a week later
    assert "cold" not in cc.watch and cc.dropped["cold"]["reason"] == "long-run loser"


def test_status_keeps_posting_during_long_jobs(tmp_path):
    now = [1_000_000.0]
    ups = []
    cc = copycat.Copycat(tmp_path, session=FakeSession(), clock=lambda: now[0], req_gap=0,
                         uploader=lambda path, data: ups.append(path))
    cc.tick()
    assert "copycat/status.json" in ups
    st_ = json.loads((tmp_path / "status.json").read_text())
    assert "scan" in st_ and "fresh_since" in st_
    n = len(ups)
    now[0] += 10
    cc.tick()                                    # not again within 5 minutes
    assert len(ups) == n


def test_dropped_accounts_still_logged_from_feed_without_snapshots(tmp_path):
    s = FakeSession()
    orig = s.get

    def get(url, params=None, timeout=None):
        params = params or {}
        if url.endswith("/social/trades") and "nickname" not in params:
            return Resp({"trades": [trade("d1", taker="mm"), trade("d2", maker="mm")], "cursor": ""})
        return orig(url, params, timeout)
    s.get = get
    cc = copycat.Copycat(tmp_path, session=s, clock=lambda: 1_000_000.0, req_gap=0)
    cc.drop("mm", "failed its paper test")
    cc.feed_seen = {"older"}                     # past the boot read
    cc.poll_feed(1_000_000.0)
    fills = [r for r in rows(tmp_path) if r.get("kind") == "fill"]
    assert {r["trade_id"] for r in fills} == {"d1", "d2"}
    assert all(r["dropped"] and not r["snap"] and not r["backfill"] for r in fills)
    assert not cc.snaps and "mm" not in cc.watch
    assert all(a["nick"] != "mm" for a in cc.update_status(1_000_000.0)["accounts"])


def test_feed_delay_stats_and_source(tmp_path):
    s = FakeSession()
    orig = s.get

    def get(url, params=None, timeout=None):
        params = params or {}
        if url.endswith("/social/trades") and "nickname" not in params:
            return Resp({"trades": [trade("f1", taker="sharp", t="2026-11-10T20:00:00Z")], "cursor": ""})
        return orig(url, params, timeout)
    s.get = get
    from datetime import datetime, timezone
    now = datetime(2026, 11, 10, 20, 0, 12, tzinfo=timezone.utc).timestamp()
    cc = copycat.Copycat(tmp_path, session=s, clock=lambda: now, req_gap=0)
    cc.watch = {"sharp": {"pnl": 5}}
    cc.feed_seen = {"older"}
    cc.poll_feed(now)
    assert cc.feed_lags == [12.0]
    f = [r for r in rows(tmp_path) if r.get("kind") == "fill"]
    assert f[0]["src"] == "feed"
    sc = cc.update_status(now)["scan"]
    assert sc["feed_lag_s"]["p50"] == 12.0 and sc["copy_lag_s"]["feed"]["n"] == 1


def test_feed_reads_each_snapshot_once_back_to_last_seen(tmp_path):
    s = FakeSession()
    orig = s.get
    snap = {"pages": []}

    def get(url, params=None, timeout=None):
        params = params or {}
        if url.endswith("/social/trades") and "nickname" not in params:
            s.calls.append((url, dict(params)))
            i = int(params.get("cursor") or 0)
            pages = snap["pages"]
            return Resp({"trades": pages[i], "cursor": str(i + 1) if i + 1 < len(pages) else ""})
        return orig(url, params, timeout)
    s.get = get
    cc = copycat.Copycat(tmp_path, session=s, clock=lambda: 1_000_000.0, req_gap=0)
    cc.watch = {"sharp": {}}
    snap["pages"] = [[trade("o1", taker="sharp")], [trade("o0")]]
    cc.poll_feed(1_000_000.0)                    # boot: remembered, not logged (may be minutes old)
    assert not [r for r in rows(tmp_path) if r.get("kind") == "fill"]
    n = len(s.calls)
    cc.poll_feed(1_000_000.0)                    # snapshot unchanged: one cheap read
    assert len(s.calls) == n + 1
    snap["pages"] = [[trade("n3", taker="sharp")], [trade("n2"), trade("n1", maker="sharp")],
                     [trade("o1", taker="sharp")], [trade("o0")]]
    cc.poll_feed(1_000_000.0)                    # refreshed: reads new pages back to o1, then stops
    assert len(s.calls) == n + 1 + 3
    ids = [r["trade_id"] for r in rows(tmp_path) if r.get("kind") == "fill"]
    assert sorted(ids) == ["n1", "n3"]


def test_big_bets_by_new_accounts_logged_and_scored(tmp_path):
    s = FakeSession()
    orig = s.get

    def get(url, params=None, timeout=None):
        params = params or {}
        if url.endswith("/profile/metrics") and params.get("nickname") in ("newbie", "vet"):
            return Resp({"metrics": {"pnl": 0, "num_markets_traded": 2 if params["nickname"] == "newbie" else 400}})
        if url.endswith("/profile") and params.get("nickname") in ("newbie", "vet"):
            return Resp({"social_profile": {"joined_at": "2024-01-01T00:00:00Z"}})
        return orig(url, params, timeout)
    s.get = get
    cc = copycat.Copycat(tmp_path, session=s, clock=lambda: 1_780_000_000.0, req_gap=0)
    big = dict(trade("w1", taker="newbie", side="no", px="0.30"), count_fp="1000.00")   # $700 on NO at 70c
    vet = dict(trade("w2", taker="vet", px="0.60"), count_fp="1000.00", ticker="KXV-1")  # $600 on YES
    small = trade("w3", taker="newbie", px="0.50")                                     # $5: ignored
    for t in (big, vet, small):
        cc.spot_whale(t, 1_780_000_000.0)
    cc.run_whales()
    w = {r["nick"]: r for r in rows(tmp_path) if r.get("kind") == "whale"}
    assert set(w) == {"newbie", "vet"} and w["newbie"]["new"] and not w["vet"]["new"]
    assert w["newbie"]["side"] == "no" and w["newbie"]["usd"] == 700
    sm = copycat.whale_summary(list(w.values()), {"KXTEST-1": "no", "KXV-1": "no"}, 1_780_000_000.0)
    assert sm["new_count"] == 1 and sm["new"]["settled"] == 1 and sm["new"]["avg_c"] > 25   # 100 - 70 - fee
    assert sm["old"]["avg_c"] < -55
    cc.spot_whale(big, 1_780_000_001.0)
    n = len(s.calls)
    cc.run_whales()                              # profile cached: only the market price check for a NEW account
    assert [c[0].rsplit("/", 1)[-1] for c in s.calls[n:]] == ["KXTEST-1"]



def test_big_anonymous_bets_logged_without_lookups(tmp_path):
    s = FakeSession()
    cc = copycat.Copycat(tmp_path, session=s, clock=lambda: 1_000_000.0, req_gap=0)
    big = dict(trade("a1", side="yes", px="0.80"), count_fp="3000.00")     # $2,400 anonymous YES
    small = dict(trade("a2", side="yes", px="0.80"), count_fp="1000.00")   # $800 anonymous: under the bar
    cc.spot_whale(big, 1_000_000.0)
    cc.spot_whale(small, 1_000_000.0)
    w = [r for r in rows(tmp_path) if r.get("kind") == "whale"]
    assert len(w) == 1 and w[0]["anon"] and w[0]["usd"] == 2400 and not s.calls and not cc.whale_queue
    sm = copycat.whale_summary(w, {"KXTEST-1": "yes"}, 1_000_000.0)
    assert sm["anon_count"] == 1 and sm["anon"]["settled"] == 1 and sm["count"] == 0



def test_full_feed_rolling_log(tmp_path):
    import gzip as gz
    s = FakeSession()
    orig = s.get
    page = {"t": []}

    def get(url, params=None, timeout=None):
        params = params or {}
        if url.endswith("/social/trades") and "nickname" not in params:
            return Resp({"trades": page["t"], "cursor": ""})
        return orig(url, params, timeout)
    s.get = get
    cc = copycat.Copycat(tmp_path, session=s, clock=lambda: 1_000_000.0, req_gap=0)
    page["t"] = [trade("b0")]
    cc.poll_feed(1_000_000.0)                    # boot: not logged
    page["t"] = [trade("b2", taker="x,y"), trade("b1"), trade("b0")]
    cc.poll_feed(1_000_000.0)
    cc.feed_fh.close()
    f = list((tmp_path / "feed").glob("feed-*.csv.gz"))
    lines = gz.open(f[0], "rt").read().splitlines()
    assert [ln.split(",")[8] for ln in lines] == ["b1", "b2"] and "x y" in lines[1]



def test_big_order_split_into_many_fills_counts_as_one_bet(tmp_path):
    fills = [dict(trade(f"s{i}", side="no", px="0.10", maker=f"m{i}"), count_fp="600.00") for i in range(4)]
    o = copycat.orders(fills)
    taker = [x for x in o if not x["maker_nickname"]]
    assert len(taker) == 1 and taker[0]["count_fp"] == 2400 and taker[0]["fills"] == 4
    assert abs(taker[0]["price_dollars"] - 0.10) < 1e-9
    assert len([x for x in o if x["maker_nickname"]]) == 4          # each named maker on its own
    cc = copycat.Copycat(tmp_path, session=FakeSession(), clock=lambda: 1_000_000.0, req_gap=0)
    for x in o:
        cc.spot_whale(x, 1_000_000.0)
    w = [r for r in rows(tmp_path) if r.get("kind") == "whale"]
    assert len(w) == 1 and w[0]["anon"] and w[0]["usd"] == 2160 and w[0]["fills"] == 4   # $2,160 on NO at 90c



def test_whale_rows_survive_load_rows(tmp_path):
    cc = copycat.Copycat(tmp_path, session=FakeSession(), clock=lambda: 1_000_000.0, req_gap=0)
    cc.spot_whale(dict(trade("w1", side="yes", px="0.80"), count_fp="3000.00"), 1_000_000.0)
    got = [r for r in copycat.load_rows(tmp_path) if r.get("kind") == "whale"]
    assert len(got) == 1 and copycat.whale_summary(got, {}, 1_000_000.0)["anon_count"] == 1


def test_load_rows_folds_snaps_across_hours_and_skips_unscored(tmp_path):
    import gzip as gz
    import json as js
    a = [{"kind": "fill", "v": 2, "nick": "n", "trade_id": "t1", "ticker": "K", "side": "yes", "yes_price": 0.5,
          "snap": True, "ts": 1.0},
         {"kind": "fill", "v": 2, "nick": "n", "trade_id": "t2", "ticker": "K", "snap": False, "backfill": True},
         {"kind": "fill", "v": 1, "nick": "n", "trade_id": "t0", "ticker": "K", "side": "yes", "yes_price": 0.5},
         {"kind": "snap", "trade_id": "t1", "ticker": "K", "delay": 0, "bid": 0.48, "ask": 0.52, "result": None}]
    b = [{"kind": "snap", "trade_id": "t1", "ticker": "K", "delay": 60, "bid": 0.6, "ask": 0.62, "result": "yes"}]
    with gz.open(tmp_path / "copycat-2026-11-11-01.jsonl.gz", "wt") as fh:
        fh.write("\n".join(js.dumps(r, separators=(",", ":")) for r in a) + "\n")
    (tmp_path / "copycat-2026-11-11-02.jsonl").write_text(js.dumps(b[0]) + "\n{torn")
    rows = copycat.load_rows(tmp_path)
    fills = [r for r in rows if r["kind"] == "fill"]
    assert [f["trade_id"] for f in fills] == ["t1"] and fills[0]["_s"] == {0: (0.48, 0.52), 60: (0.6, 0.62)}
    res = {}
    g = copycat.grade(rows, res)
    assert res == {"K": "yes"} and len(g["n"]["settle"]) == 1


def test_memory_cap_keeps_newest_copies_per_account(tmp_path, monkeypatch):
    import json as js
    monkeypatch.setattr(copycat, "KEEP_COPIES", 3)
    copycat._ROW_CACHE.clear()
    for h in ("01", "02"):
        rows = [{"kind": "fill", "v": 2, "nick": "n", "trade_id": f"{h}-{i}", "ticker": "K", "side": "yes",
                 "yes_price": 0.5, "ts": 1.0} for i in range(2)]
        rows.append({"kind": "whale", "ticker": "K", "ts": 1.0})
        (tmp_path / f"copycat-2026-11-11-{h}.jsonl").write_text("\n".join(js.dumps(r) for r in rows))
    now = 10 * 86400.0
    got = copycat.load_rows(tmp_path, cache_done_hours=True, now=now)
    assert [r["trade_id"] for r in got if r["kind"] == "fill"] == ["01-1", "02-0", "02-1"]
    assert not [r for r in got if r["kind"] == "whale"]
    assert len(copycat.load_rows(tmp_path, cache_done_hours=True, now=now)) == 3
    copycat._ROW_CACHE.clear()


def test_restart_does_not_relog_known_accounts_history(tmp_path):
    cc = copycat.Copycat(tmp_path, session=FakeSession(), clock=lambda: 1_000_000.0, req_gap=0)
    cc.logged_before = {"alice"}
    cc.ingest("alice", [trade("h1", taker="alice")], 1_000_000.0)
    assert not [r for r in rows(tmp_path) if r.get("kind") == "fill"] and "h1" in cc.seen["alice"]


def test_new_account_whale_scored_at_our_price():
    r = [{"kind": "whale", "nick": "x", "ticker": "K", "side": "yes", "paid": 0.50, "ours": 0.60, "new": True,
          "usd": 600, "count": 1200}]
    sm = copycat.whale_summary(r, {"K": "yes"}, 1.0)
    assert sm["new"]["avg_c"] > sm["new_ours"]["avg_c"] and sm["new_ours"]["settled"] == 1


def test_old_whale_rows_never_loaded_and_rows_share_strings(tmp_path):
    """Crash loop Nov 12-13: the start-up load held days of big-bet rows before trimming them."""
    import json as js
    from datetime import datetime, timezone
    copycat._ROW_CACHE.clear()
    now = datetime(2026, 11, 13, 14, tzinfo=timezone.utc).timestamp()
    for name, ts in (("copycat-2026-11-09-01.jsonl", now - 4.5 * 86400), ("copycat-2026-11-13-10.jsonl", now - 4 * 3600)):
        rows = [{"kind": "whale", "ticker": "KXA-1", "ts": ts, "anon": True},
                {"kind": "fill", "v": 2, "nick": "n", "trade_id": name, "ticker": "KXA-1", "side": "yes",
                 "yes_price": 0.5, "ts": ts}]
        (tmp_path / name).write_text("\n".join(js.dumps(r) for r in rows) + "\n")
    got = copycat.load_rows(tmp_path, cache_done_hours=True, now=now)
    whales = [r for r in got if r["kind"] == "whale"]
    fills = [r for r in got if r["kind"] == "fill"]
    assert len(whales) == 1 and whales[0]["ts"] == now - 4 * 3600
    assert len(fills) == 2                                  # copies are kept whatever their age
    assert fills[0]["ticker"] is fills[1]["ticker"]         # interned: one string shared by every row
    copycat._ROW_CACHE.clear()


def test_needs_40_settled_before_real(tmp_path):
    rows_ = []
    for i in range(160):
        tid = f"t{i}"
        rows_ += [{"kind": "fill", "trade_id": tid, "nick": "good", "ticker": f"T{i}", "side": "yes",
                   "yes_price": 0.50, "backfill": False, "lag_s": 9, "ts": 1000.0 + i, "et": "x"},
                  {"kind": "snap", "trade_id": tid, "ticker": f"T{i}", "delay": 0, "bid": 0.50, "ask": 0.52}]
    few = {f"T{i}": ("yes" if i % 10 < 8 else "no") for i in range(39)}       # 160 seen, 39 settled
    a = copycat.build_status(rows_, few, {"good": {}}, now=2000.0)["accounts"][0]
    assert a["fills"] == 160 and a["settled"] == 39 and a["verdict"] == "watching"
    more = {f"T{i}": ("yes" if i % 10 < 8 else "no") for i in range(40)}
    a = copycat.build_status(rows_, more, {"good": {}}, now=2000.0)["accounts"][0]
    assert a["verdict"] == "passes"


def _cat_rows(n, ticker_fn):
    rows_ = []
    for i in range(n):
        tid = f"t{i}"
        rows_ += [{"kind": "fill", "trade_id": tid, "nick": "good", "ticker": ticker_fn(i), "side": "yes",
                   "yes_price": 0.50, "backfill": False, "lag_s": 9, "ts": 1000.0 + i, "et": "x"},
                  {"kind": "snap", "trade_id": tid, "ticker": ticker_fn(i), "delay": 0, "bid": 0.50, "ask": 0.52}]
    return rows_


def test_category_grades_block_a_clearly_bad_category():
    tk = lambda i: (f"KXNBA-{i}" if i % 5 == 0 else f"KXBTC-{i}")             # 1 in 5 is sports
    rows_ = _cat_rows(200, tk)
    res = {tk(i): ("no" if i % 5 == 0 else ("yes" if i % 10 < 9 else "no")) for i in range(200)}
    a = copycat.build_status(rows_, res, {"good": {}}, now=2000.0,
                             cats={"KXNBA": "Sports", "KXBTC": "Crypto"})["accounts"][0]
    assert a["verdict"] == "passes"
    assert a["cat_grades"]["Sports"]["n"] == 40 and a["cat_grades"]["Sports"]["blocked"]
    assert not a["cat_grades"]["Crypto"]["blocked"] and a["blocked_cats"] == ["Sports"]


def test_real_lag_sends_account_back_to_paper():
    rows_ = _cat_rows(160, lambda i: f"T{i}")
    res = {f"T{i}": ("yes" if i % 10 < 8 else "no") for i in range(160)}
    st_ = copycat.build_status(rows_, res, {"good": {}}, now=2000.0)
    assert st_["accounts"][0]["verdict"] == "passes"
    # 20 real copies of the same trades, but real lost every one (paper says ~+28c)
    pos = {f"T{i}": {"nick": "good", "side": "yes", "n": 1, "cost": 0.53, "settled": 1, "pnl": -0.53,
                     "trade_id": f"t{i}"} for i in range(20)}
    copycat.add_real(st_, {"positions": pos, "pnl": {"good": -10.6}})
    a = st_["accounts"][0]
    assert "_paper_c" not in st_
    assert a["verdict"] == "real-lag" and a["size"] == 0 and a["real"]["gap_n"] == 20 and a["real"]["gap_c"] < -3


def test_real_close_to_paper_keeps_copying():
    rows_ = _cat_rows(160, lambda i: f"T{i}")
    res = {f"T{i}": ("yes" if i % 10 < 8 else "no") for i in range(160)}
    st_ = copycat.build_status(rows_, res, {"good": {}}, now=2000.0)
    pos = {f"T{i}": {"nick": "good", "side": "yes", "n": 1, "cost": 0.54, "settled": 1,
                     "pnl": (0.46 if i % 10 < 8 else -0.54), "trade_id": f"t{i}"} for i in range(20)}
    copycat.add_real(st_, {"positions": pos, "pnl": {}})
    assert st_["accounts"][0]["verdict"] == "passes"


def test_cat_breakdown_adds_bands_series_and_unsettled_categories():
    trades = [{"ticker": f"KXNBA-{i}", "side": "yes", "pay_c": 30, "c": 10.0 if i % 2 else -5.0, "et": "2026-11-14 10:00"}
              for i in range(6)] + [{"ticker": "KXBTC-1", "side": "no", "pay_c": 80, "c": None, "et": "2026-11-14 11:00"}]
    cats = {"KXNBA": "Sports", "KXBTC": "Crypto"}
    out = copycat.cat_breakdown(trades, cats, copycat.cat_grades(trades, cats))
    sp = out["Sports"]
    assert sp["copies"] == 6 and sp["won"] == 3 and sp["lost"] == 3
    assert sp["bands"]["25-50c"] == [6, 6, 2.5] and sp["series"][0][:3] == ["KXNBA", 6, 6]
    assert out["Crypto"]["copies"] == 1 and out["Crypto"]["n"] == 0 and out["Crypto"]["all_series"] == ["KXBTC"]


def test_results_fetched_in_batches_least_recently_checked_first(tmp_path):
    ses = FakeSession()
    cc = copycat.Copycat(tmp_path, session=ses, clock=lambda: 5000.0, req_gap=0)
    cc.feed_next = 1e12
    want = {f"OPEN{i:04d}" for i in range(2500)} | {f"DONE{i}" for i in range(50)}
    cc.fetch_results(set(want), 5000.0)
    calls = [c for c in ses.calls if c[0].endswith("/markets")]
    assert len(calls) == copycat.RESULT_CALLS and all(len(c[1]["tickers"].split(",")) == 100 for c in calls)
    assert len(cc.results) == 50                     # alphabetical on the first pass: DONE* come first
    late = {f"ZLATE{i}" for i in range(10)}          # still-open markets asked before go to the back
    cc.fetch_results((want - set(cc.results)) | late, 5300.0)
    asked = {t for c in ses.calls[len(calls):] for t in c[1]["tickers"].split(",")}
    assert late <= asked and "OPEN2499" in asked and "OPEN1949" not in asked


def _acct_rows(nick, n, tick, win):
    out = []
    for i in range(n):
        out += [{"kind": "fill", "trade_id": f"{nick}{i}", "nick": nick, "ticker": tick(i), "side": "yes",
                 "yes_price": 0.50, "backfill": False, "lag_s": 9, "ts": 1000.0 + i, "et": "x"},
                {"kind": "snap", "trade_id": f"{nick}{i}", "ticker": tick(i), "delay": 0, "bid": 0.50, "ask": 0.52}]
    return out, {tick(i): ("yes" if win(i) else "no") for i in range(n)}


def test_honest_grade_counts_games_once_and_needs_30_events():
    # 160 copies, 80% winners at 52c, but all in 8 games (20 copies each, a game wins or loses whole)
    rows_, res = _acct_rows("lucky", 160, lambda i: f"KXG-G{i // 20}-M{i}", lambda i: i // 20 not in (3, 6))
    a = copycat.build_status(rows_, res, {"lucky": {}}, now=2000.0)["accounts"][0]
    assert a["events"] == 8 and a["verdict"] == "fails"          # was a pass when each copy counted alone
    assert copycat.score_grade([c for c in copycat.grade(rows_, res)["lucky"]["settle"]])[0] >= 80
    # the same record spread over 160 games passes
    rows2, res2 = _acct_rows("real", 160, lambda i: f"KXG-G{i}-M", lambda i: i % 10 < 8)
    b = copycat.build_status(rows2, res2, {"real": {}}, now=2000.0)["accounts"][0]
    assert b["events"] == 150 and b["verdict"] == "passes" and b["size"] >= 1
    # a kept account stays on real money at 1 contract whatever its score, unless its own stop benched it
    k = copycat.build_status(rows_, res, {"lucky": {}}, now=2000.0, keep={"lucky"})["accounts"][0]
    assert k["verdict"] == "passes" and k["size"] == 1 and k["kept"]
    k2 = copycat.build_status(rows_, res, {"lucky": {}}, now=2000.0, keep={"lucky"},
                              account_stops={"lucky": "own stop"})["accounts"][0]
    assert k2["verdict"] == "benched" and k2["size"] == 0


def test_load_cats_fills_category_map_from_full_series_list(tmp_path):
    s = FakeSession()
    cc = copycat.Copycat(tmp_path, session=s, clock=lambda: 1_000_000.0, req_gap=0)
    cc.load_cats(1_000_000.0)
    assert cc.cats == {"KXNBAGAME": "Sports", "SENATEKS": "Elections"}
    assert cc.cats_next == 1_000_000.0 + copycat.CATS_REFRESH_S
    assert cc.category("KXNBAGAME-26NOV14LALBOS-BOS") == "Sports"
    assert not [c for c in s.calls if "/series/" in c[0]]          # served from the map, no per-series call
    g = copycat.cat_grades([{"ticker": "KXNBAGAME-X", "c": 5.0}, {"ticker": "SENATEKS-Y", "c": -3.0}], cc.cats)
    assert set(g) == {"Sports", "Elections"}
