"""trade.yml must save each strategy's own state file (data/<name>_state.json).

Nov 23: data/weather_late_state.json was never committed, so the high/holds -$3
stop forgot it had already pushed and sent a phone alert on every 5-minute run."""
from pathlib import Path

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "trade.yml"


def test_trade_workflow_saves_strategy_state_files():
    text = WF.read_text()
    save = text[text.index("Save orders"):]
    assert "data/*_state.json" in save
