from datetime import datetime, timedelta, timezone

import pytest

from wxbot import radar
from wxbot.util import HttpError, load_config

UTC = timezone.utc


class Http:
    def __init__(self, events, markets=None, trades=None, results=None):
        self.events = events
        self.markets = markets or {}
        self.trades = trades or {}
        self.results = results or {}
        self.posts = []

    def get_json(self, url, params=None, headers=None):
        if url.endswith("/events"):
            return {"events": self.events, "cursor": ""}
        if url.endswith("/markets"):
            return {"markets": self.markets.get(params["series_ticker"], [])}
        if url.endswith("/markets/trades"):
            return {"trades": self.trades.get(params["ticker"], [])}
        if "/markets/" in url:
            tk = url.rsplit("/", 1)[-1]
            return {"market": {"ticker": tk, "result": self.results.get(tk, "")}}
        raise HttpError(404, url)

    def post_json(self, url, payload, headers=None):
        self.posts.append(payload)
        return {}


def ev(series, cat="Politics", n=1):
    return [{"event_ticker": f"{series}-E{i}", "series_ticker": series, "category": cat,
             "title": f"{series} title"} for i in range(n)]


def mk(tk, bid, ask, close, strike="", event=None):
    return {"ticker": tk, "event_ticker": event or tk.rsplit("-", 1)[0], "yes_bid_dollars": str(bid),
            "yes_ask_dollars": str(ask), "close_time": close.isoformat().replace("+00:00", "Z"),
            "status": "active", "strike_type": strike, "volume_fp": "5"}


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(radar, "RADAR", tmp_path / "radar.json")
    monkeypatch.setenv("NTFY_TOPIC", "t")
    return tmp_path


def test_baseline_then_watched_first_listing_pushes_once(data):
    cfg = load_config()
    now = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
    http = Http(ev("KXOLD") + ev("KXGAME", "Sports"))
    db = radar.run(http, cfg, now)
    assert db["baseline_at"] and not db["last_new"] and not http.posts

    close = now + timedelta(hours=10)
    http = Http(ev("KXOLD") + ev("KXGAME", "Sports") + ev("KXCORND", "Commodities") + ev("KXNEWSPORT", "Sports"),
                {"KXCORND": [mk("KXCORND-26NOV21-T1", 0.05, 0.08, close),
                             mk("KXCORND-26NOV21-T2", 0.90, 0.93, close)]})
    db = radar.run(http, cfg, now + timedelta(hours=1))
    assert set(db["last_new"]) == {"KXCORND", "KXNEWSPORT"}
    assert db["listings"]["KXCORND"]["watched"]
    assert len(http.posts) == 1 and "KXCORND" in http.posts[0]["message"]
    rules = {(o["rule"], o["ticker"]) for o in db["paper"]}
    assert ("longshot_no", "KXCORND-26NOV21-T1") in rules
    assert ("fav_24h", "KXCORND-26NOV21-T2") in rules
    lo = next(o for o in db["paper"] if o["rule"] == "longshot_no")
    assert lo["side"] == "no" and lo["price"] == 0.93           # best NO bid 0.92 + 1c
    # sports listings are recorded but never tested
    assert not any(o["series"] == "KXNEWSPORT" for o in db["paper"])

    n = len(db["paper"])
    db = radar.run(http, cfg, now + timedelta(hours=2))      # same orders not placed twice
    assert len(db["paper"]) == n and len(http.posts) == 1
    text = "\n".join(radar.digest_lines(db, now + timedelta(hours=2)))
    assert "KXCORND" in text and "WATCHED" in text and "longshot_no" in text


def test_fill_only_on_trade_through_then_settle_and_missed(data):
    now = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
    close = now + timedelta(hours=5)
    db = {"baseline_at": "x", "paper": [
        {"rule": "longshot_no", "ticker": "A", "series": "S", "side": "no", "price": 0.90,
         "placed_at": now.isoformat(), "checked_at": now.isoformat(), "status": "resting",
         "close": close.isoformat()},
        {"rule": "longshot_no", "ticker": "B", "series": "S", "side": "no", "price": 0.90,
         "placed_at": now.isoformat(), "checked_at": now.isoformat(), "status": "resting",
         "close": close.isoformat()}]}
    http = Http([], trades={"A": [{"yes_price_dollars": "0.1100", "created_time": now.isoformat()}],
                            "B": [{"yes_price_dollars": "0.1000", "created_time": now.isoformat()}]},
                results={"A": "no", "B": "no"})
    radar.update_paper(http, db, now + timedelta(hours=1))
    a, b = db["paper"]
    assert a["status"] == "filled" and b["status"] == "resting"   # a touch at our price is not a fill
    radar.update_paper(http, db, close + timedelta(hours=1))
    a, b = sorted(db["paper"], key=lambda o: o["ticker"])
    assert a["status"] == "settled" and a["cents"] == pytest.approx(100 * (0.10 - radar.maker_fee(0.90)))
    assert b["status"] == "settled-unfilled" and b["cents"] > 0
    st = radar.rule_stats(db["paper"])
    assert st["fills"] == 1 and st["missed"] == 1 and st["fill_rate"] == 0.5


def test_frozen_hours_windows():
    w = radar.DEFAULT_FROZEN["cattle"]
    et = radar.ET
    assert radar.is_frozen(w, datetime(2026, 11, 19, 20, 0, tzinfo=et))      # Thu 8 PM
    assert not radar.is_frozen(w, datetime(2026, 11, 19, 11, 0, tzinfo=et))  # Thu 11 AM
    assert radar.is_frozen(w, datetime(2026, 11, 22, 12, 0, tzinfo=et))      # Sunday
    assert radar.next_open(w, datetime(2026, 11, 20, 15, 0, tzinfo=et)).astimezone(et).weekday() == 0
    g = radar.DEFAULT_FROZEN["grains"]
    assert radar.is_frozen(g, datetime(2026, 11, 19, 16, 0, tzinfo=et))      # 4 PM: closed
    assert not radar.is_frozen(g, datetime(2026, 11, 19, 22, 0, tzinfo=et))  # evening session
    assert not radar.is_frozen(g, datetime(2026, 11, 22, 21, 0, tzinfo=et))  # Sunday night open


def test_frozen_bracket_needs_close_after_reopen():
    cfg = load_config()
    now = datetime(2026, 11, 19, 20, 0, tzinfo=radar.ET).astimezone(UTC)     # cattle closed
    ms = [mk("KXLCATTLED-26NOV20-B1", 0.40, 0.45, now + timedelta(hours=20), "between"),
          mk("KXLCATTLED-26NOV20-B2", 0.20, 0.25, now + timedelta(hours=20), "between"),
          mk("KXLCATTLED-26NOV19-B3", 0.40, 0.45, now + timedelta(hours=2), "between")]  # closes before reopen
    out = [c for c in radar.candidates("KXLCATTLED", ms, now, cfg, set()) if c["rule"] == "frozen_bracket"]
    by = {c["ticker"]: c for c in out}
    assert set(by) == {"KXLCATTLED-26NOV20-B1", "KXLCATTLED-26NOV20-B2"}
    assert by["KXLCATTLED-26NOV20-B1"]["taker"] and by["KXLCATTLED-26NOV20-B1"]["price"] == 0.60
    assert not by["KXLCATTLED-26NOV20-B2"]["taker"] and by["KXLCATTLED-26NOV20-B2"]["price"] == 0.65


def _orders(strategy, n, pnl, filled=1, status="settled", price="0.90"):
    return [{"mode": "live", "strategy": strategy, "status": status, "filled": str(filled),
             "pnl": str(pnl), "ticker": f"{strategy.upper()}-T{i}", "side": "no", "price": price,
             "settled_at": f"2026-11-{10 + i % 9:02d}", "placed_at": "2026-11-19T00:00:00+00:00",
             "close": "2026-11-19T00:00:00+00:00"} for i in range(n)]


def test_kill_rule_and_adverse_selection_flags(data):
    cfg = load_config()
    cfg.raw["trading"]["spx_range"]["live"] = True
    cfg.raw["trading"]["spx_range"]["enabled"] = True
    now = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
    orders = _orders("spx_range", 20, -0.08) + _orders("spx_range", 10, 0, filled=0, status="cancelled")
    orders[20:] = [{**o, "ticker": f"MISS-{i}"} for i, o in enumerate(orders[20:])]
    http = Http([], results={f"MISS-{i}": "no" for i in range(10)})
    db = {}
    f = radar.live_fade(http, db, cfg, orders, now)["spx_range"]
    assert any(x.startswith("KILL RULE") for x in f["flags"])
    assert any(x.startswith("ADVERSE") for x in f["flags"]) and f["missed"] == 10
    assert f["fill_rate"] == pytest.approx(20 / 30)
    # paper or switched-off strategies are not monitored
    cfg.raw["trading"]["spx_range"]["live"] = False
    assert "spx_range" not in radar.live_fade(http, db, cfg, orders, now)


def test_kill_push_sent_once(data):
    cfg = load_config()
    cfg.raw["trading"]["spx_range"]["live"] = True
    cfg.raw["trading"]["spx_range"]["enabled"] = True
    now = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
    http = Http(ev("KXOLD"))
    radar.run(http, cfg, now, _orders("spx_range", 20, -0.08))
    radar.run(http, cfg, now + timedelta(hours=1), _orders("spx_range", 21, -0.08))
    assert sum("kill rule" in p["title"] for p in http.posts) == 1


def test_incomplete_events_list_does_not_diff(data, monkeypatch):
    cfg = load_config()
    now = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
    radar.run(Http(ev("KXOLD")), cfg, now)
    monkeypatch.setattr(radar, "fetch_open_events", lambda http: None)
    db = radar.run(Http(ev("KXOLD") + ev("KXNEW")), cfg, now + timedelta(hours=1))
    assert db["last_new"] == [] and "KXNEW" not in db["series_seen"]
