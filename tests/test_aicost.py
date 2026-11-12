from datetime import date

from wxbot import aicost, digest
from wxbot.util import load_config


def test_period_and_report_line(monkeypatch):
    data = {"ai": {"2026-11-11": 24.15, "2026-11-12": 0.4}, "m15": {"2026-11-11": -8.58, "2026-11-12": 1.0}}
    p = aicost.period(data, date(2026, 11, 11), date(2026, 11, 11))
    assert p == {"ai": 24.15, "m15": -8.58}
    text, phone = digest.build("daily", date(2026, 11, 11), date(2026, 11, 11), [], load_config(), data)
    assert "Claude API $24.15" in text and "net after AI" in phone
    text, _ = digest.build("daily", date(2026, 11, 11), date(2026, 11, 11), [], load_config())
    assert "After AI cost" not in text


def test_load_keeps_known_days(monkeypatch):
    monkeypatch.setattr(aicost, "_get", lambda p: {"ai_history": {"2026-11-12": 0.3}} if "watch" in p else None)
    d = aicost.load()
    assert d["ai"]["2026-11-11"] == 24.15 and d["ai"]["2026-11-12"] == 0.3 and d["m15"] == {}
