import json

from tools import grade_copycat
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
            pnl = 900_000 if params.get("nickname") == "sharp" else 1_000
            return Resp({"metrics": {"pnl": pnl * 10_000, "volume": 5, "num_markets_traded": 3}})
        if url.endswith("/profile"):
            return Resp({"social_profile": {"top_categories": ["Sports"], "joined_at": "2024-01-01"}})
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
    out = grade_copycat.grade(rows_, {"T": "yes"})
    o = out["sharp"]
    assert len(o["lag"]) == 1                          # backfill ignored
    assert round(o["move_300"][0], 1) == 11.0          # mid 0.61 - 0.50
    assert round(o["follow_300"][0], 1) == 7.0         # mid 0.61 - ask 0.54
    assert round(o["settle"][0], 1) == 46.0            # 1.00 - 0.54
