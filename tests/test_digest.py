from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from wxbot import daybefore, digest, trader
from wxbot.util import load_config

UTC = timezone.utc
ET = ZoneInfo("America/New_York")


def test_schedule():
    st = {}
    # 8 AM ET: nothing yet; 9 AM Monday Dec 1: daily + weekly + monthly
    assert digest.due(datetime(2026, 12, 1, 13, 0, tzinfo=UTC), ET, st) == []
    kinds = [k for k, *_ in digest.due(datetime(2026, 12, 1, 15, 0, tzinfo=UTC), ET, st)]
    assert kinds == ["daily"] + (["weekly"] if date(2026, 12, 1).weekday() == 0 else []) + ["monthly"]
    w = digest.due(datetime(2026, 11, 9, 15, 0, tzinfo=UTC), ET, {})   # Monday
    assert ("weekly", date(2026, 11, 2), date(2026, 11, 8), "2026-W45") in w
    assert digest.due(datetime(2026, 11, 9, 15, 0, tzinfo=UTC), ET, {"daily": "2026-11-08",
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
    paths = digest.run_due(cfg, datetime(2026, 11, 9, 15, 0, tzinfo=UTC), None)
    names = sorted(p.parent.name for p in paths)
    assert names == ["daily", "weekly"]
    weekly = next(p for p in paths if p.parent.name == "weekly").read_text()
    assert "Weather day-before | paper | 1 | 1 | +$0.70" in weekly
    assert "Mentions NO | live | 1 | 1 | +$0.30" in weekly
    assert "Real money" in weekly and "## Reasoning by strategy" in weekly and "## Biggest results" in weekly
    # already sent: nothing due on the next run
    assert digest.run_due(cfg, datetime(2026, 11, 9, 15, 5, tzinfo=UTC), None) == []
