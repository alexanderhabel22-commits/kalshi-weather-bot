from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from wxbot import daybefore, digest, trader
from wxbot.util import load_config

UTC = timezone.utc
ET = ZoneInfo("America/New_York")


def test_schedule():
    st = {}
    # 8 AM ET: nothing yet; 10 AM: daily only; noon on the 1st: monthly (+ weekly on a Monday)
    assert digest.due(datetime(2026, 12, 1, 13, 0, tzinfo=UTC), ET, st) == []
    assert [k for k, *_ in digest.due(datetime(2026, 12, 1, 15, 0, tzinfo=UTC), ET, st)] == ["daily"]
    kinds = [k for k, *_ in digest.due(datetime(2026, 12, 1, 17, 0, tzinfo=UTC), ET, st)]
    assert kinds == ["daily"] + (["weekly"] if date(2026, 12, 1).weekday() == 0 else []) + ["monthly"]
    w = digest.due(datetime(2026, 11, 9, 17, 0, tzinfo=UTC), ET, {})   # Monday noon ET
    assert ("weekly", date(2026, 11, 2), date(2026, 11, 8), "2026-W45") in w
    assert digest.due(datetime(2026, 11, 9, 17, 0, tzinfo=UTC), ET, {"daily": "2026-11-08",
                                                                     "weekly": "2026-W45"}) == []


def test_reports_cover_paper_and_live_and_get_written(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    cfg = load_config()
    daybefore.save_orders([{"id": "a", "placed_at": "2026-11-05T17:00:00+00:00", "ticker": "T1",
                            "event": "E", "series": "KXHIGHNY", "city": "NYC", "metric": "high",
                            "date": "2026-11-06", "bracket": "60-61", "side": "yes", "price": "0.30",
                            "blend_p": "0.40", "status": "filled", "pnl": "0.70",
                            "settled_at": "2026-11-07T14:00:00+00:00"}])
    trader.save([{"id": "b", "mode": "live", "strategy": "mentions", "ticker": "M1", "event": "EV",
                  "title": "Will Trump say crypto?", "side": "no", "price": "0.85", "count": 2,
                  "filled": 2, "status": "settled", "pnl": "0.30",
                  "settled_at": "2026-11-07T03:00:00+00:00"}])
    paths = digest.run_due(cfg, datetime(2026, 11, 9, 17, 0, tzinfo=UTC), None)
    names = sorted(p.parent.name for p in paths)
    assert names == ["daily", "weekly"]
    weekly = next(p for p in paths if p.parent.name == "weekly").read_text()
    assert "Weather day-before | practice, no money | 1 | 1 | +$0.70" in weekly
    assert "Mentions NO | REAL money | 1 | 1 | +$0.30" in weekly
    assert "Real money" in weekly and "## Reasoning by strategy" in weekly and "## Biggest results" in weekly
    # already sent: nothing due on the next run
    assert digest.run_due(cfg, datetime(2026, 11, 9, 15, 5, tzinfo=UTC), None) == []


def _live_rows():
    return [{"id": "b", "mode": "live", "strategy": "mentions", "ticker": "M1", "event": "EV",
             "title": "t", "side": "no", "price": "0.85", "count": 2, "filled": 2, "status": "settled",
             "pnl": "0.30", "settled_at": "2026-11-07T03:00:00+00:00"},
            {"id": "c", "mode": "live", "strategy": "favorites", "ticker": "F1", "event": "EF",
             "title": "t", "side": "yes", "price": "0.90", "count": 1, "filled": 1, "status": "settled",
             "result": "exit", "pnl": "-0.10", "settled_at": "2026-11-07T20:00:00+00:00"},
            {"id": "d", "mode": "live", "strategy": "weather", "ticker": "W1", "event": "EW",
             "title": "t", "side": "no", "price": "0.50", "count": 2, "filled": 2, "status": "filled"},
            {"id": "e", "mode": "paper", "strategy": "mentions", "ticker": "M2", "event": "EV",
             "title": "t", "side": "no", "price": "0.80", "count": 1, "filled": 1, "status": "settled",
             "pnl": "0.20", "settled_at": "2026-11-07T20:00:00+00:00"}]


def test_daily_money_summary_covers_every_live_strategy():
    from wxbot import moneysum
    title, body = moneysum.format_summary(_live_rows(), datetime(2026, 11, 7, 5, 0, tzinfo=UTC),
                                          "Weather alerts (old, no money): 0 sent today, record 0-2")
    assert "money summary" in title
    assert "Real P&L today: -$0.10 (0-1, 1 sold early)" in body       # mentions settled before midnight
    assert "Real P&L all time: +$0.20 (1-1, 1 sold early)" in body
    assert "mentions: today +$0.00 · all time +$0.30" in body and "favorites: today -$0.10" in body
    assert "weather: today +$0.00" in body and "$1.00 open" in body
    assert "Open: 1 positions + 0 waiting orders, $1.00 at risk" in body
    assert "Practice (no money) all time: +$0.20" in body
    assert body.splitlines()[-1].startswith("Weather alerts (old")


def test_weekly_phone_text_leads_with_real_money_by_strategy(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    trader.save(_live_rows())
    tz = ET
    res = digest.collect(tz)
    _, phone = digest.build("weekly", date(2026, 11, 2), date(2026, 11, 8), res, load_config())
    lines = phone.splitlines()
    assert lines[0].startswith("REAL money: +$0.20 (1-1)")
    assert any("Mentions" in x and "all time +$0.30" in x for x in lines)
    assert any(x.startswith("Open: $1.00 at risk") for x in lines)


def test_money_summary_sent_once_from_trade_run_after_9pm(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    trader.save(_live_rows())

    class H:
        def __init__(self):
            self.posts = []

        def post_json(self, url, payload, headers=None):
            self.posts.append(payload)
            return {}

    cfg = load_config()
    h = H()
    assert digest.run_money_summary(cfg, datetime(2026, 11, 7, 20, 0, tzinfo=UTC), h) is False  # 3 PM ET
    assert digest.run_money_summary(cfg, datetime(2026, 11, 8, 2, 5, tzinfo=UTC), h)            # 9:05 PM ET
    assert digest.run_money_summary(cfg, datetime(2026, 11, 8, 2, 10, tzinfo=UTC), h) is False  # once a day
    assert len(h.posts) == 1 and "money summary" in h.posts[0]["title"]
