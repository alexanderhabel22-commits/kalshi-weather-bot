import gzip
import json
import os
import re
from pathlib import Path

from wxbot import wsfeed

CSV = """id,mode,strategy,ticker,event,side,price,count,filled,status
a,live,weather_late,KXLOWTNYC-26NOV21-B40.5,KXLOWTNYC-26NOV21,no,0.88,2,0,resting
b,live,favorites,KXRT-X-80,KXRT-X,yes,0.90,8,8,filled
c,paper,weather_late,KXLOWTMIA-26NOV21-B70.5,KXLOWTMIA-26NOV21,no,0.88,2,0,resting
d,live,spx_range,KXINX-26NOV21-B6000,KXINX-26NOV21,no,0.75,2,0,cancelled
e,live,weather_late,KXLOWTNYC-26NOV21-B40.5,KXLOWTNYC-26NOV21,no,0.87,2,0,resting
"""


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def rows(d: Path):
    out = []
    for p in sorted(d.glob("wsfeed-*.jsonl")):
        out += [json.loads(x) for x in p.read_text().splitlines()]
    return out


def test_open_orders_real_and_open_only():
    resting, held, events = wsfeed.open_orders(CSV)
    assert resting == ["KXLOWTNYC-26NOV21-B40.5"]
    assert held == ["KXRT-X-80"]
    assert events == ["KXLOWTNYC-26NOV21", "KXRT-X"]      # paper and cancelled rows ignored


def test_pick_priority_and_caps():
    tick, books = wsfeed.pick(["R1"], ["H1"], ["R1", "S1", "S2"], ["A1"], cap=4, book_cap=1)
    assert tick == ["R1", "H1", "S1", "S2"]                # our orders first, deduped, capped
    assert books == ["R1"]


def test_watch_list_uses_orders_siblings_and_always_series(tmp_path):
    now = 1_795_200_000.0
    soon = "2026-11-21T21:00:00Z"    # within 36 h of `now`
    far = "2026-12-30T21:00:00Z"

    def get(url, params):
        if url == wsfeed.ORDERS_URL:
            return CSV
        if params.get("event_ticker") == "KXLOWTNYC-26NOV21":
            return {"markets": [{"ticker": "KXLOWTNYC-26NOV21-B40.5"}, {"ticker": "KXLOWTNYC-26NOV21-B42.5"}]}
        if params.get("series_ticker") == "KXINX":
            return {"markets": [{"ticker": "KXINX-A", "close_time": soon}, {"ticker": "KXINX-B", "close_time": far}]}
        return {"markets": []}

    f = wsfeed.WsFeed(tmp_path, get=get, clock=Clock(now))
    tick, books = f.watch_list(now)
    assert tick == ["KXLOWTNYC-26NOV21-B40.5", "KXRT-X-80", "KXLOWTNYC-26NOV21-B42.5", "KXINX-A"]
    assert books == ["KXLOWTNYC-26NOV21-B40.5"]


def test_watch_list_keeps_old_list_when_orders_unreadable(tmp_path):
    def get(url, params):
        raise RuntimeError("down")
    f = wsfeed.WsFeed(tmp_path, get=get)
    f.tickers, f.books = ["X"], ["X"]
    assert f.watch_list(0) == (["X"], ["X"])


def test_handle_logs_raw_and_flags_jumps(tmp_path):
    c = Clock(1_795_200_000.0)
    f = wsfeed.WsFeed(tmp_path, clock=c)
    tk = "KXLOWTNYC-26NOV21-B40.5"
    f.handle(json.dumps({"type": "ticker", "sid": 1, "msg": {"market_ticker": tk, "yes_bid_dollars": "0.10",
                                                              "yes_ask_dollars": "0.12"}}))
    c.t += 20
    f.handle(json.dumps({"type": "ticker", "sid": 1, "msg": {"market_ticker": tk, "yes_bid_dollars": "0.20",
                                                              "yes_ask_dollars": "0.22"}}))
    c.t += 5
    f.handle(json.dumps({"type": "ticker", "sid": 1, "msg": {"market_ticker": tk, "yes_bid_dollars": "0.30",
                                                              "yes_ask_dollars": "0.32"}}))   # no 2nd jump within 60 s
    f.handle(json.dumps({"type": "fill", "sid": 2, "msg": {"order_id": "o1", "count_fp": "2"}}))
    f.handle(json.dumps({"type": "subscribed", "msg": {"sid": 3}}))
    f.handle("not json")
    r = rows(tmp_path)
    assert [x.get("type") or x.get("kind") for x in r] == ["ticker", "ticker", "jump", "ticker", "fill"]
    j = r[2]
    assert j["ticker"] == tk and j["from"] == 0.11 and j["to"] == 0.21
    assert r[-1]["msg"]["order_id"] == "o1"


def test_book_messages_skipped_over_hour_cap(tmp_path):
    f = wsfeed.WsFeed(tmp_path, clock=Clock(1_795_200_000.0), hour_cap_bytes=50)
    for _ in range(3):
        f.handle(json.dumps({"type": "orderbook_delta", "msg": {"market_ticker": "X", "price": 10, "delta": 1}}))
    f.handle(json.dumps({"type": "trade", "msg": {"market_ticker": "X"}}))
    kinds = [x.get("type") or x.get("kind") for x in rows(tmp_path)]
    assert kinds == ["orderbook_delta", "cap", "trade"]


def test_upload_finished_hours_only_and_prune(tmp_path):
    sent = {}
    c = Clock(1_795_200_000.0)
    f = wsfeed.WsFeed(tmp_path, uploader=lambda p, b: sent.__setitem__(p, b), clock=c)
    f.write({"kind": "a"})
    c.t += 3600
    f.write({"kind": "b"})
    f.upload_done_hours(c.t)
    assert len(sent) == 1
    (name, blob), = sent.items()
    assert name.startswith("wsfeed/wsfeed-") and name.endswith(".jsonl.gz")
    assert json.loads(gzip.decompress(blob))["kind"] == "a"
    f.upload_done_hours(c.t)
    assert len(sent) == 1                                   # not re-sent
    old = sorted(tmp_path.glob("wsfeed-*.jsonl"))[0]
    os.utime(old, (c.t - 3 * 86400, c.t - 3 * 86400))
    f.upload_done_hours(c.t)
    assert not old.exists() and len(list(tmp_path.glob("*.jsonl"))) == 1


def test_subscribe_new_only_once(tmp_path):
    sent = []

    class WS:
        def send(self, s):
            sent.append(json.loads(s))
    f = wsfeed.WsFeed(tmp_path, clock=Clock(1_795_200_000.0))
    f.ws = WS()
    f.tickers, f.books = ["A", "B"], ["A"]
    f.subscribe_new()
    f.subscribe_new()
    assert [m["params"]["channels"] for m in sent] == [["ticker", "trade"], ["orderbook_delta"]]
    f.tickers.append("C")
    f.subscribe_new()
    assert sent[-1]["params"]["market_tickers"] == ["C"]


def test_recorder_has_no_order_code():
    src = Path(wsfeed.__file__).read_text()
    for bad in ("place_limit", "buy_now", "sell_now", "cancel(", "KalshiClient(", "/portfolio/orders",
                "shard_transfer"):
        assert bad not in src, bad
    assert not re.search(r"\.post\(", src)


def test_service_wiring():
    root = Path(wsfeed.__file__).parents[1]
    upd = (root / "deploy/m15/update.sh").read_text()
    assert "install-wsfeed.sh" in upd
    no_restart = re.search(r"NO_RESTART='([^']+)'", upd).group(1)
    for p in ("wxbot/wsfeed.py", "deploy/m15/install-wsfeed.sh", "deploy/m15/kalshi-wsfeed.service"):
        assert re.match(no_restart, p), p              # changing the recorder never restarts the 15-min bot
    assert "bot.py wsfeed" in (root / "deploy/m15/kalshi-wsfeed.service").read_text()
