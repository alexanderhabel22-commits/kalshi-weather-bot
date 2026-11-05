"""Dump one raw /historical candlestick response (format check). Read-only."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from kalshi_api import get  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
OUT.mkdir(parents=True, exist_ok=True)
tk, st = "KXNBAGAME-26JUN13NYKSAS-SAS", 1781397000
js = get(f"/historical/markets/{tk}/candlesticks", {"start_ts": st - 5400, "end_ts": st, "period_interval": 1})
(OUT / "hist_candles.json").write_text(json.dumps(js, indent=1)[:200000])
