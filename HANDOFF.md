# Project handoff: Kalshi Weather Alerts

Paste this into the Claude project's instructions so new chats start with context.

## What this is
Alex's alert bot for Kalshi daily temperature markets. Started Nov 3, 2026 with
a $35 bankroll. It sends phone alerts (ntfy) with an exact trade; Alex places
trades manually in the Kalshi app. It never trades on its own.

## Where it runs
GitHub Actions, every 5 minutes (`.github/workflows/scan.yml`). Secrets:
`NTFY_TOPIC` (required), `NTFY_TOKEN` (optional). Variable: `BANKROLL`.
Ledger and state are committed to `data/`; forecasts cached in `cache/`.

## Code map
- `wxbot/kalshi.py`: public API, bracket parsing (greater = value > floor,
  less = value < cap, between = inclusive), fees (round_up(0.07·C·P·(1−P))).
- `wxbot/stations.py`: 20 cities → settlement stations (Chicago = Midway,
  Houston = Hobby, Dallas = DFW), CLI day in local standard time, daily
  auto-discovery of series from Kalshi.
- `wxbot/weather.py`: NWS observations with METAR T-group / 6-hr max-min
  parsing, NWS gridpoint highs/lows, Open-Meteo ensembles.
- `wxbot/model.py`: Monte Carlo of settlement value; deterministic "lock"
  logic with safety margins (0.4°F precise, 1.5°F noisy readings).
- `wxbot/strategy.py`: edge after fees, 25% market blend for forecast calls,
  quarter-Kelly sizing with caps (10% lock / 5% model / 12% city-day / 40% open);
  RISK_LEVEL=aggressive raises lock sizing (half Kelly, 20% / 25% / 60%).
- `wxbot/scanner.py`: one scan; one new alert per city-day per scan; dedupe;
  quiet hours; 9 PM summary/heartbeat; outage alert after 3 failed scans.
- `wxbot/ledger.py`: paper record in `data/alerts.csv`, auto-settlement,
  Brier score model vs market.

## Decisions so far
- Weather markets chosen because public data (station readings, forecasts)
  can beat casual traders; LOCK alerts are the main strategy, EDGE alerts are
  experimental until the record proves them.
- Alerts only, manual trading. Auto-trading only after a proven record.
- Model parameters are educated guesses, not yet fitted to history.

## What happened on day one (Nov 3, 2026)
The first live scan sent 11 day-ahead EDGE alerts. All had the same shape:
the market put ~50-60% on one 2°F bracket while the model gave it ~15-35%
(e.g. NYC low 80% range 47-59°F vs NWS 54). The model's day-ahead spread is
too wide and/or biased cold, so EDGE alerts were switched to silent "shadow"
mode (logged + graded, not sent). Those 11 were moved to shadow in the ledger.
The scan log (`gh run view --log`) now prints a "model ..." line per city-day
(median, 80% range, NWS, obs, bias, sigma) for calibration.

## Next steps
1. After 2–3 weeks: review `data/alerts.csv` (`report` command). Compare
   `pred_median` vs `cli_value` to tune `sigma_base` / `sigma_per_hour`.
2. Calibrate the day-ahead model against the shadow record (likely: anchor
   harder to the NWS forecast, shrink sigma). Turn `edge_alerts` "on" only if
   the shadow Brier score beats the market's.
3. Possible additions: backtest on Kalshi history, rain markets, Kalshi API
   key to read the real balance and fills.
