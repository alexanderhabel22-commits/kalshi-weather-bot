from datetime import datetime, timedelta, timezone

from wxbot import balance
from wxbot.util import BANKROLL_FILE, Config, auto_bankroll, write_json

NOW = datetime(2026, 11, 5, 23, 55, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, bal, positions=None, fail=False):
        self.bal, self.positions, self.fail = bal, positions or [], fail

    def _get(self, path, params=None):
        if self.fail:
            raise RuntimeError("boom")
        if path == "/portfolio/balance":
            return self.bal
        assert path == "/portfolio/positions"
        return {"market_positions": self.positions, "cursor": ""}


def test_refresh_adds_cash_and_positions():
    info = balance.refresh(None, NOW, FakeClient({"balance": 4200, "portfolio_value": 771}))
    assert info["total"] == 49.71 and info["positions_source"] == "portfolio_value"
    assert auto_bankroll(NOW + timedelta(minutes=5)) == 49.71
    # Config reads the real clock, so it needs a balance read just now
    balance.refresh(None, datetime.now(timezone.utc), FakeClient({"balance": 4200, "portfolio_value": 771}))
    assert Config(raw={"account": {"bankroll": 10}}).bankroll == 49.71


def test_positions_fallback_and_dollar_fields():
    c = FakeClient({"balance_dollars": "40.00"},
                   [{"market_exposure": 500}, {"market_exposure_dollars": "2.50"}])
    assert balance.refresh(None, NOW, c)["total"] == 47.5


def test_failed_or_zero_read_keeps_fallback(monkeypatch):
    monkeypatch.setenv("BANKROLL", "49.71")
    assert balance.refresh(None, NOW, FakeClient({}, fail=True)) is None
    assert balance.refresh(None, NOW, FakeClient({"balance": 0})) is None
    assert not BANKROLL_FILE.exists()
    assert Config(raw={}).bankroll == 49.71


def test_stale_file_or_switched_off_falls_back(monkeypatch):
    monkeypatch.delenv("BANKROLL", raising=False)
    write_json(BANKROLL_FILE, {"total": 80.0, "at": (NOW - timedelta(hours=7)).isoformat()})
    assert auto_bankroll(NOW) is None
    write_json(BANKROLL_FILE, {"total": 80.0, "at": datetime.now(timezone.utc).isoformat()})
    assert Config(raw={"account": {"bankroll": 49.71}}).bankroll == 80.0
    assert Config(raw={"account": {"bankroll": 49.71, "auto_bankroll": False}}).bankroll == 49.71


def test_no_key_does_nothing(monkeypatch):
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    assert balance.refresh(None, NOW) is None


def test_saves_cash_per_exchange_shard():
    info = balance.refresh(None, NOW, FakeClient({"balance": 2798, "portfolio_value": 2400, "balance_breakdown": [
        {"exchange_index": 0, "balance": "14.7700"}, {"exchange_index": 2, "balance": "13.2100"}]}))
    assert info["shards"] == {"0": 14.77, "2": 13.21} and info["cash"] == 27.98


def test_cash_on_reads_one_shard():
    from wxbot.kalshi_api import KalshiClient
    c = KalshiClient.__new__(KalshiClient)
    c._get = lambda path, params=None: {"balance": 2798, "balance_breakdown": [
        {"exchange_index": 0, "balance": "14.77"}, {"exchange_index": 2, "balance": "13.21"}]}
    assert c.cash_on(0) == 14.77 and c.cash_on(2) == 13.21
    c._get = lambda path, params=None: {"balance": 2798}
    assert c.cash_on(0) == 27.98
