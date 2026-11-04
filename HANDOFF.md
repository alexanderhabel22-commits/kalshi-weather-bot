# Project handoff: Kalshi Weather Alerts

Use this as the Claude project's instructions. It is kept current in the repo:
when the project changes, update this file and the project instructions together.

## Who and what
Alex (Concord, NC; works full-time in sales, builds side projects with Claude)
runs a bot that watches Kalshi daily high/low temperature markets and sends
phone alerts with an exact trade. Alex places every trade himself in the
Kalshi app. The bot never trades. It is a small real-money test (~$37 bankroll,
started Nov 3, 2026) in place of paper trading; Alex adds money only if the
record earns it. Be candid about odds and risk; no hype.

## Where everything lives
- Repo (public): **github.com/alexanderhabel22-commits/kalshi-weather-bot**,
  branch `main`. Get access in a new chat with the add_repo tool
  (owner `alexanderhabel22-commits`, repo `kalshi-weather-bot`, access `push`),
  then clone and work there.
- Runs on GitHub Actions (`.github/workflows/scan.yml`), cron `3-59/5 * * * *`
  (every 5 min). Manual runs: Actions → weather-scan → Run workflow →
  `scan` / `test-notify` / `report`. Read logs with `gh run view <id> --log`;
  list runs with `gh api repos/alexanderhabel22-commits/kalshi-weather-bot/actions/runs`
  (gh GraphQL commands like `gh workflow run` don't work from Claude sessions;
  use `gh api` REST instead).
- Secrets: `NTFY_TOPIC` (Alex's private ntfy topic; never write it in the
  repo), optional `NTFY_TOKEN`. Variables: `BANKROLL`, optional `RISK_LEVEL`.
- The bot commits `data/` after scans, so always `git pull --rebase` before
  pushing. GitHub ties the schedule to whoever last edited the workflow file;
  if scheduled runs stop after Claude edits `scan.yml`, have Alex make a tiny
  edit to it on github.com.

## How it works (code map)
- `wxbot/kalshi.py`: public market data (no key), bracket parsing
  (greater = value > floor, less = value < cap, between = inclusive, checked
  against the label), taker fee round_up(0.07·C·P·(1−P)).
- `wxbot/stations.py`: ~20 cities → settlement stations (Chicago = Midway,
  Houston = Hobby, Dallas = DFW); NWS climate day = local STANDARD time;
  daily auto-discovery of KXHIGH*/KXLOWT* series.
- `wxbot/weather.py`: NWS observations (precise METAR T-groups, 6-hr max/min
  groups; 5-min readings treated as noisy), NWS gridpoint highs/lows,
  Open-Meteo ensembles (up to 143 members).
- `wxbot/model.py`: Monte Carlo of the settlement value; deterministic LOCK
  logic with safety margins (0.4°F precise, 1.5°F noisy).
- `wxbot/strategy.py`: edge after fees; quarter-Kelly sizing with caps
  (10% per lock, 5% per EDGE, 12% per city-day, 40% open);
  `RISK_LEVEL=aggressive` raises lock sizing (half Kelly, 20%/25%/60%).
- `wxbot/scanner.py`: one scan; one new alert per city-day per scan; dedupe;
  quiet hours 0–7 AM ET; 9 PM summary (heartbeat) linking REPORT.md; outage
  alert after 3 failed scans; only delivered alerts are logged as live.
- `wxbot/ledger.py`, `wxbot/tracking.py`, `wxbot/report.py`: grading (below).
- Tests: `pytest` (34 passing at handoff), lint with `pyflakes`. Run both
  before every push.

## Alert types and current settings
- **LOCK**: observations already decide the bracket. Sent. This is the strategy.
- **EDGE**: forecast-based. **Silent** (`edge_alerts = "shadow"` in
  `config.toml`): logged and graded but not sent. Day one sent 11 EDGE alerts
  that all disagreed with the market the same way (model's day-ahead spread
  too wide, e.g. Denver low 80% range 25–40°F); they were moved to shadow.
- Risk level: normal.

## Grading data (all in `data/`, committed by the bot)
- `alerts.csv`: every alert (mode live/shadow), settlement, P&L at suggested
  size, follow-up prices (`avail_min`, `ask_15m`, `ask_60m`) = how long the
  price stayed buyable.
- `census.json`: every locked bracket per day with first and lowest ask
  (priced-in vs tradeable).
- `forecasts.csv`: model median/p10/p90 + NWS per city-day every 6 h, graded
  with the settlement value.
- `my_trades.csv`: Alex's real fills (`ticker,side,contracts,price,note`).
  When Alex reports a trade or sends a Kalshi screenshot, add it here.
- `REPORT.md`: written nightly and by the `report` command.

## "Check the bot" routine
1. add_repo, clone (or pull), read `data/REPORT.md`; refresh with
   `python bot.py report` if needed (it reads data/ locally; no network).
2. Check runs/logs for failures and the per-city `model ...` log lines.
3. Report to Alex in plain words: LOCK record and any loss explained, whether
   prices lasted, census (how many locks were cheap enough), silent EDGE
   record vs market, forecast bias/coverage, bot health. Then recommend.

## Game plan
- **Now → ~Nov 17:** LOCKs only, normal risk. Alex uses limit orders at or
  below the alert price, never chases, logs trades. Any LOCK loss = stop and
  diagnose before sizing up.
- **Nov 17 checkpoint:** locks all won and prices lasted → aggressive mode /
  add money. Locks won but prices vanished → $6/mo server with 60-second
  scans, maybe auto-buying locks. Few cheap locks → edge priced in; pivot.
  Any loss → fix first.
- **Weeks 3–4:** recalibrate the day-ahead model from `forecasts.csv` (likely
  anchor harder to NWS, shrink sigma). EDGE stays silent until 50+ settled
  shadow calls, a profit, and Brier better than market.
- **Later, if proven:** rain markets (same lock logic), then market making
  (resting orders + Kalshi liquidity rewards) with more capital and an API key.

## Expectations to keep honest
Likely 0–3 LOCK alerts a day, ~10–15¢ expected each at this bankroll; a wrong
lock costs ~$3. Order books are thin (some ~5 contracts at the alert price),
so the ceiling is tens of dollars per trade. Not financial advice.
