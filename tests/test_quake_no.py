from datetime import datetime, timezone
from types import SimpleNamespace

from wxbot.strats import quake_no as q

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)          # 6 h left in the UTC day


def mkt(x=7.0, bid=0.07, day="20NOV26"):
    return {"ticker": f"KXBIGGESTQUAKE-{day}-{x}", "event_ticker": f"KXBIGGESTQUAKE-{day}",
            "floor_strike": x, "yes_bid_dollars": f"{bid:.4f}", "yes_ask_dollars": f"{bid + 0.03:.4f}",
            "close_time": "2026-11-20T23:59:59Z", "volume_fp": "300"}


P = dict(q.DEFAULTS)


def test_climatology_matches_replay():
    assert abs(q.pday(7.0) - 130 / 3653) < 1e-9 and abs(q.pday(6.6) - 309 / 3653) < 1e-9
    assert q.pday(6.0) == 0.0
    assert q.fee1(0.93) == 0.01 and q.fee1(0.50) == 0.02


def test_buys_no_late_in_quiet_day():
    out = q.candidates([mkt(7.0, 0.07), mkt(6.6, 0.12)], NOW, 5.9, P)
    assert [i.ticker.rsplit("-", 1)[-1] for i in out] == ["7.0", "6.6"]
    i = out[0]
    assert i.side == "no" and i.price == 0.93 and i.count == 2 and i.taker and i.strategy == "quake_no"


def test_edge_threshold_and_time():
    # 18 h left: model YES for 7.0 is ~2.7%, NO 93c + 1c fee leaves ~2c < 5c -> no trade
    early = datetime(2026, 11, 20, 6, 0, tzinfo=UTC)
    assert q.candidates([mkt(7.0, 0.07)], early, 5.0, P) == []
    # before its UTC day starts (markets open 21:00Z the day before): never
    pre = datetime(2026, 11, 19, 23, 0, tzinfo=UTC)
    assert q.candidates([mkt(7.0, 0.30)], pre, 0.0, P) == []


def test_skips_other_strikes_happened_and_band():
    assert q.candidates([mkt(6.4, 0.05)], NOW, 5.0, P) == []             # not 6.6/7.0
    assert q.candidates([mkt(7.0, 0.07)], NOW, 7.1, P) == []             # already happened
    assert q.candidates([mkt(7.0, 0.07)], NOW, 6.9, P) == []             # could be revised up to 7.0
    assert len(q.candidates([mkt(7.0, 0.07)], NOW, 6.8, P)) == 1
    assert q.candidates([mkt(7.0, 0.01)], NOW, 5.0, P) == []             # NO 99c, outside band
    held = {"KXBIGGESTQUAKE-20NOV26": {"KXBIGGESTQUAKE-20NOV26-7.0"}}
    assert q.candidates([mkt(7.0, 0.07)], NOW, 5.0, P, held) == []       # one entry per market


def row(status="settled", pnl="", price="0.93", filled=2, mode="live", strat="quake_no"):
    return {"strategy": strat, "mode": mode, "status": status, "pnl": pnl, "price": price,
            "count": filled, "filled": filled, "ticker": "x", "event": "e"}


def ctx(orders=(), mode="live"):
    return SimpleNamespace(now=NOW, orders=list(orders), mode=mode, http=None, cfg=None, params={})


def test_budget_counts_open_positions(monkeypatch):
    monkeypatch.setattr(q, "archived_realized", lambda *a, **k: 0.0)
    room, settled = q.budget(ctx([row(pnl="-1.88"), row(status="filled")]), P)
    assert settled == -1.88 and abs(room - (4.0 - 1.88 - 1.86)) < 1e-9
    # worst case can never pass $4: 0.26 left fits nothing at 93c
    out = q.fit_budget(q.candidates([mkt(7.0, 0.07)], NOW, 5.0, P), room)
    assert out == []
    # $2.16 room: first 2-ct order fits (1.88), the second shrinks to 0
    out = q.fit_budget(q.candidates([mkt(7.0, 0.07), mkt(6.6, 0.12)], NOW, 5.0, P), 2.16)
    assert [i.count for i in out] == [2]
    assert sum(i.count * (i.price + q.fee1(i.price)) for i in out) <= 2.16


def test_stop_after_cap_used(monkeypatch, tmp_path):
    monkeypatch.setattr(q, "STATE", tmp_path / "s.json")
    sent = []
    monkeypatch.setattr(q.notify, "send", lambda *a, **k: sent.append(a))
    c = ctx()
    assert q.stopped(c, P, -1.88) is False            # one loss: still room for one more order
    assert q.stopped(c, P, -3.76) is True and len(sent) == 1
    assert q.stopped(c, P, -3.76) is True and len(sent) == 1     # one push only


def test_paper_twin_same_rule(monkeypatch):
    from wxbot.strats import quake_no_paper as qp
    calls = []
    monkeypatch.setattr(q, "_series_markets", lambda c, s: calls.append(s) or [mkt(7.0, 0.07)])
    monkeypatch.setattr(q, "max_so_far", lambda c, s: 5.4)
    q._CACHE.clear()
    out = qp.propose(ctx(mode="paper"))
    assert len(out) == 1 and out[0].strategy == "quake_no_paper" and out[0].count == 2
    out2 = q.propose(ctx(mode="paper"))
    assert out2[0].strategy == "quake_no" and len(calls) == 1      # one Kalshi read per run


# --- fast-quake guard (Nov 21, 2026) ---------------------------------------------------------
def test_guard_off_by_default_and_on_for_paper_twin():
    assert q.DEFAULTS["guard"] is False
    from wxbot.strats import quake_no_guard_paper as g
    assert g.NAME == "quake_no_guard_paper"


def test_parse_feeds():
    txt = ("#EventID|Time|Latitude|Longitude|Depth/km|Author|Catalog|Contributor|ContributorID|MagType|Magnitude|"
           "MagAuthor|EventLocationName|EventType\n"
           "gfz2026wtzh|2026-11-21T00:09:08.79|-10.74|-75.954|10.0|||GFZ|gfz2026wtzh|Mw|6.66||Central Peru|earthquake\n")
    assert q.parse_gfz(txt) == [("2026-11-21T00:09:08.79", 6.66)]
    js = {"features": [{"properties": {"time": "2026-11-21T00:09:21.4Z", "mag": 6.8}}, {"properties": {"mag": None}}]}
    assert q.parse_emsc(js) == [("2026-11-21T00:09:21.4Z", 6.8)]
    assert q.parse_gfz("") == [] and q.parse_emsc(None) == []


def test_guard_feed_skips_near_strike():
    out = q.candidates([mkt(7.0, 0.07), mkt(6.6, 0.12)], NOW, 5.9, P)
    p = {**P, "guard": True}
    kept = q.guard(out, [mkt(7.0, 0.07), mkt(6.6, 0.12)], NOW, 5.9, {"gfz": [("t", 6.4)], "emsc": []}, p)
    assert [i.ticker.rsplit("-", 1)[-1] for i in kept] == ["7.0"]           # 6.4 >= 6.6 - 0.3
    kept = q.guard(out, [], NOW, 5.9, {"gfz": [], "emsc": [("t", 6.8)]}, p)
    assert kept == []
    kept = q.guard(out, [], NOW, 5.9, {"gfz": [], "emsc": []}, p)            # feeds down: no block
    assert len(kept) == 2


def test_guard_market_alarm_needs_two_strikes():
    p = {**P, "guard": True}
    calm = [mkt(6.0, 0.20), mkt(6.2, 0.12), mkt(6.6, 0.08), mkt(7.0, 0.04)]
    assert q.market_alarm(calm, NOW, 5.0, p) is None
    one = [mkt(6.0, 0.85), mkt(6.2, 0.12), mkt(6.6, 0.08)]                  # a stray sweep on one strike
    assert q.market_alarm(one, NOW, 5.0, p) is None
    peru = [mkt(6.0, 0.88), mkt(6.2, 0.80), mkt(6.6, 0.15), mkt(7.0, 0.09)]
    assert q.market_alarm(peru, NOW, 5.0, p)
    out = q.candidates([mkt(7.0, 0.09), mkt(6.6, 0.15)], NOW, 5.0, P)
    assert out and q.guard(out, peru, NOW, 5.0, {"gfz": [], "emsc": []}, p) == []
    # strikes USGS already reached (or nearly) don't count: that quake is already known
    assert q.market_alarm([mkt(6.0, 0.99), mkt(6.2, 0.95)], NOW, 6.0, p) is None


def test_feed_log(monkeypatch, tmp_path):
    monkeypatch.setattr(q, "FEED_LOG", tmp_path / "f.csv")
    fd = {"gfz": [("2026-11-21T00:09:08", 6.66), ("x", 5.6)], "emsc": []}
    q.log_feeds(NOW, fd, 0.0)
    q.log_feeds(NOW, fd, 0.0)                                                # no duplicates
    lines = (tmp_path / "f.csv").read_text().splitlines()
    assert len(lines) == 2 and "gfz" in lines[1] and "6.66" in lines[1]
