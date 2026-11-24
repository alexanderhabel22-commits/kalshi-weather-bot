import ast
import json
import math
from collections import deque
from pathlib import Path

import pytest

from wxbot import spotlag
from wxbot.spotlag import Book, SpotLag, fee1, sigma_1m

T0 = 1795500000.0            # an arbitrary 2026 timestamp
CLOSE = T0 + 600             # 10 minutes left at T0
TK = "KXDOGE15M-26NOV240100-00"


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class FakeSpot:
    def __init__(self, fv_yes=0.80):
        self.fv_yes = fv_yes
        self.cf = []

    def on_cf(self, msg, now):
        self.cf.append(msg)

    def sample(self, now):
        pass

    def fair(self, coin, strike, close, now):
        return None if self.fv_yes is None else {"src": "cf", "px": 0.15, "sigma": 1e-4, "fv_yes": self.fv_yes}

    def health(self, now):
        return {}


def markets_get(result=""):
    def get(url, params=None):
        if url.endswith("/markets"):
            if params["series_ticker"] != "KXDOGE15M":
                return {"markets": []}
            return {"markets": [{"ticker": TK, "close_time": "2026-11-24T06:00:00Z", "floor_strike": 0.15}]}
        if "/markets/" in url:
            return {"market": {"ticker": TK, "result": result}}
        return {"mode": "paper"}
    return get


def make(tmp_path, fv=0.80, result="yes"):
    clk = Clock(T0)
    up = []
    sl = SpotLag(tmp_path, FakeSpot(fv), get=markets_get(result), status_up=up.append, clock=clk)
    sl.markets[TK] = {"coin": "DOGE", "close": CLOSE, "strike": 0.15}
    return sl, clk, up


def snap(no_bids, yes_bids=(), seq=1):
    return json.dumps({"type": "orderbook_snapshot", "sid": 2, "seq": seq,
                       "msg": {"market_ticker": TK,
                               "no_dollars_fp": [[f"{p:.4f}", f"{q:.2f}"] for p, q in no_bids],
                               "yes_dollars_fp": [[f"{p:.4f}", f"{q:.2f}"] for p, q in yes_bids]}})


def delta(side, p, d, seq):
    return json.dumps({"type": "orderbook_delta", "sid": 2, "seq": seq,
                       "msg": {"market_ticker": TK, "price_dollars": f"{p:.4f}", "delta_fp": f"{d:.2f}",
                               "side": side}})


def test_fee():
    assert fee1(0.60) == 0.02
    assert fee1(0.50) == 0.02
    assert fee1(0.95) == 0.01


def test_book_snapshot_delta_and_ask():
    b = Book()
    b.snapshot({"no_dollars_fp": [["0.3000", "5.00"], ["0.4000", "2.00"]], "yes_dollars_fp": [["0.5500", "3"]]}, T0)
    assert b.ask("yes") == (0.6, 2.0)          # best NO bid 40c -> YES ask 60c, 2 ct
    assert b.ask("no") == (0.45, 3.0)
    b.delta({"side": "no", "price_dollars": "0.4000", "delta_fp": "-2.00"}, T0)
    assert b.ask("yes") == (0.7, 5.0)
    b.delta({"side": "no", "price": 45, "delta": 1}, T0)     # legacy cents format
    assert b.ask("yes") == (0.55, 1.0)
    b2 = Book()
    b2.snapshot({"no": [[30, 5]]}, T0)                        # legacy snapshot in cents
    assert b2.ask("yes") == (0.7, 5.0)


def test_sigma_1m_needs_30_minutes_and_matches_returns():
    pts = deque((int(T0) - 3600 + i, 100.0 * math.exp(0.001 * ((i // 60) % 2))) for i in range(3600))
    s = sigma_1m(pts, T0)
    assert s == pytest.approx(0.001 / math.sqrt(60), rel=0.05)
    short = [(int(T0) - 600 + i, 100.0) for i in range(600)]
    assert sigma_1m(short, T0) is None


def test_cf_index_ids():
    assert spotlag.cf_coin("DOGEUSD_RTI") == "DOGE"
    assert spotlag.cf_coin("NEARUSD_RTI") == "NEAR"
    assert spotlag.cf_coin("NEARUSDRTI") == "NEAR"
    assert spotlag.cf_coin("BRTI") is None


def test_signal_probes_fill_and_settle(tmp_path):
    sl, clk, up = make(tmp_path, fv=0.80, result="yes")
    sl.handle(snap([(0.40, 3)]), clk.t)             # YES ask 60c: edge 0.80 - 0.60 - 0.02 = 18c
    sl.scan(clk.t)
    assert len(sl.signals) == 1
    s = sl.signals[0]
    assert s["side"] == "yes" and s["probes"]["0"]["ask"] == 0.6 and s["probes"]["0"]["edge"] == 0.18
    clk.t += 0.5
    sl.handle(delta("no", 0.40, -3, 2), clk.t)       # the stale ask is taken
    sl.handle(delta("no", 0.33, 4, 3), clk.t)        # next ask 67c: edge 80 - 67 - 2 = 11c
    sl.run_probes(clk.t)
    clk.t += 0.5
    sl.run_probes(clk.t)
    assert s["probes"]["1"]["ask"] == 0.67 and s["fill"]["ask"] == 0.67
    sl.scan(clk.t)
    assert len(sl.signals) == 1                      # first signal per market only
    clk.t += 5
    sl.run_probes(clk.t)
    assert "todo" not in s and set(s["probes"]) == {"0", "0.5", "1", "2", "5"}
    clk.t = CLOSE + 100
    sl.settle(clk.t)
    assert s["result"] == "yes"
    assert spotlag.probe_pnl(s, "1") == pytest.approx(1 - 0.67 - 0.02)
    assert spotlag.probe_pnl(s, "0") == pytest.approx(1 - 0.60 - 0.02)
    st = sl.status(clk.t)
    assert st["paper_only"] and st["paper_fills"] == 1 and st["scores"]["1"]["n"] == 1
    c = st["catch"][0]
    assert c["signals"] == 1 and c["0.5"]["share_5c"] == 1.0 and c["0.5"]["median_edge_c"] == 11.0
    sl.upload(clk.t)
    assert "spotlag.json" in up[0] and any(k.startswith("spotlag-") for k in up[0])
    lines = [x for x in up[0][next(k for k in up[0] if k.endswith(".csv"))].splitlines() if x]
    assert len(lines) == 2 and "pnl_1s" in lines[0]


def test_no_fill_when_gap_closes(tmp_path):
    sl, clk, _ = make(tmp_path, fv=0.80, result="no")
    sl.handle(snap([(0.40, 1)]), clk.t)
    sl.scan(clk.t)
    clk.t += 1.0
    sl.handle(delta("no", 0.40, -1, 2), clk.t)
    sl.handle(delta("no", 0.24, 10, 3), clk.t)       # ask 76c: edge 80 - 76 - 2 = 2c < 5c
    sl.run_probes(clk.t)
    s = sl.signals[0]
    assert s["fill"] is None and s["probes"]["1"]["edge"] == pytest.approx(0.02)
    clk.t = CLOSE + 100
    sl.run_probes(clk.t)
    sl.settle(clk.t)
    assert spotlag.probe_pnl(s, "1") is None
    assert spotlag.probe_pnl(s, "0") == pytest.approx(-0.62)
    c = spotlag.catchability(sl.signals)[0]
    assert c["1"]["share_5c"] == 0.0


def test_no_signal_outside_window_or_small_edge(tmp_path):
    sl, clk, _ = make(tmp_path, fv=0.70)
    sl.handle(snap([(0.40, 3)]), clk.t)              # edge 70 - 60 - 2 = 8c < 10c
    sl.scan(clk.t)
    assert sl.signals == []
    sl.spot.fv_yes = 0.95
    clk.t = CLOSE - 30                               # last minute: excluded
    sl.scan(clk.t)
    assert sl.signals == []
    clk.t = CLOSE - 880                              # 14:40 left: excluded
    sl.scan(clk.t)
    assert sl.signals == []
    sl.mode = "off"
    clk.t = CLOSE - 300
    sl.scan(clk.t)
    assert sl.signals == []
    sl.mode = "paper"
    sl.scan(clk.t)
    assert len(sl.signals) == 1


def test_seq_gap_raises_resync(tmp_path):
    sl, clk, _ = make(tmp_path)
    sl.handle(snap([(0.40, 3)], seq=1), clk.t)
    sl.handle(delta("no", 0.41, 1, 2), clk.t)
    with pytest.raises(spotlag.Resync):
        sl.handle(delta("no", 0.42, 1, 4), clk.t)


def test_cf_messages_go_to_spot(tmp_path):
    sl, clk, _ = make(tmp_path)
    sl.handle(json.dumps({"type": "cfbenchmarks_value_5hz", "msg": {"index_id": "DOGEUSD_RTI", "value_usd": "0.15"}}), clk.t)
    assert sl.spot.cf and sl.counts["cf_msgs"] == 1


def test_state_survives_restart(tmp_path):
    sl, clk, _ = make(tmp_path)
    sl.handle(snap([(0.40, 3)]), clk.t)
    sl.scan(clk.t)
    sl.save()
    sl2 = SpotLag(tmp_path, FakeSpot(0.8), get=markets_get(), clock=clk)
    assert len(sl2.signals) == 1 and TK in sl2.signaled


def test_verdict_bar():
    def sig(pnl_win):
        return {"side": "yes", "result": "yes" if pnl_win else "no",
                "probes": {"1": {"ask": 0.60, "size": 1, "edge": 0.08}}}
    many = [sig(True)] * 200 + [sig(False)] * 100        # 2/3 win at 62c all-in: +4.7c
    v = spotlag.verdict(spotlag.scores(many))
    assert v["state"] == "pass"
    v = spotlag.verdict(spotlag.scores([sig(True)] * 150 + [sig(False)] * 150))
    assert v["state"] == "fail"
    assert spotlag.verdict(spotlag.scores(many[:50]))["state"] == "collecting"


def test_refresh_markets_reads_strike(tmp_path):
    sl, clk, _ = make(tmp_path)
    sl.markets = {}
    clk.t = spotlag._iso("2026-11-24T05:50:00Z")
    sl.refresh_markets(clk.t)
    assert sl.markets[TK]["strike"] == 0.15 and sl.markets[TK]["coin"] == "DOGE"


def test_paper_only_no_order_code():
    src = Path(spotlag.__file__).read_text()
    tree = ast.parse(src)
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for bad in ("create_order", "place_order", "cancel_order", "KalshiClient", "client_from_env", "post_json"):
        assert bad not in names
    assert ".post(" not in src and '"POST"' not in src


def test_real_spot_prefers_cf_after_warmup():
    from wxbot.m15_fv import Composite
    sp = spotlag.Spot(Composite(["DOGE", "NEAR"], keep_s=4000))
    t = T0 - 3600
    for i in range(3601):
        now = t + i
        p = 0.15 * math.exp(0.002 * math.sin(i / 300))
        sp.on_cf({"index_id": "DOGEUSD_RTI", "value_usd": str(p)}, now)
        sp.comp.on_price("coinbase", "DOGE", p * 1.001, now)
        sp.sample(now)
    f = sp.fair("DOGE", 0.15, T0 + 300, T0)
    assert f["src"] == "cf" and 0 < f["fv_yes"] < 1
    assert sp.fair("NEAR", 3.0, T0 + 300, T0) is None          # no NEAR data at all
    sp.comp.cf_series["DOGE"].clear()
    assert sp.fair("DOGE", 0.15, T0 + 300, T0)["src"].startswith("mix")
