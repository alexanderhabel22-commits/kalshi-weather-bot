# Project handoff: Kalshi Weather Alerts

Use this as the Claude project's instructions. It is kept current in the repo:
when the project changes, update this file and the project instructions together.

## Who and what
Alex (Concord, NC; works full-time in sales, builds side projects with Claude)
runs a bot that watches Kalshi daily high/low temperature markets and sends
phone alerts with an exact trade. Alex places every trade himself in the
Kalshi app. The bot doesn't trade yet, but Alex said (Nov 4) he's fine with a
bot trading: going live (Kalshi API key as a GitHub secret, small caps) is
his call once paper results earn it. It is a small real-money test (~$37 bankroll
started Nov 3, 2026; topped up to $49.71 on Nov 5) in place of paper trading; Alex adds money only if the
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
  pushing.
- **Don't edit `scan.yml` from Claude.** GitHub ties the schedule to whoever
  last edited the workflow file, and Claude's account doesn't trigger
  scheduled runs. If it must change, have Alex commit the edit (or re-save the
  file) on github.com afterward. The "Node.js 20 is deprecated" and "Ubuntu 26"
  annotations on runs are harmless; fixing the first means bumping action
  versions in `scan.yml`, so leave it to Alex or skip it.
- ntfy topic was rotated on Nov 3 after the old one appeared in the public
  README. The topic lives only in the `NTFY_TOPIC` secret and Alex's ntfy app.

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
- **NEAR-LOCK** (added Nov 4): same-day bracket the model rates 95%+ with at
  most 9 h left in the climate day, priced 80-97¢, 2¢+ edge after fees.
  **Silent** (`near_lock_alerts = "shadow"`), graded in its own REPORT.md
  section. Idea: catch brackets that are near-certain before the market
  fully prices them, since pure LOCKs were all ≥98¢ by the time we saw them.
  Same bar as EDGE before turning on: 50+ settled, profit, Brier beats market.
- **RAIN CENSUS** (added Nov 4, Alex OK'd, ~1 week test): `wxbot/rain.py`,
  every 15 min. For the 12 monthly rain series that settle on NWS climate
  sites (KXRAIN*M), logs each "above X inches" threshold the month's rain has
  already passed: *provisional* (latest NWS CLI month-to-date + hourly station
  rain since) and *official* (CLI total), with the ask then, cheapest ask
  later, and depth. Never alerts. Goal: are rain locks ever cheap (≤95¢) or
  priced in like temperature locks? Turn off with `rain_census = false`.
- **LAUNCH WATCHER** (added Nov 5): `wxbot/launches.py`, every 30 min. Pulls
  Kalshi's full series list, notices new series and new categories, waits for
  their first open markets, and logs spread/volume plus a recommended test to
  `data/launches.json` and REPORT.md. First run is a silent baseline.
  `launch_alerts = "shadow"` (default) never pushes; `"on"` pushes one short
  note for new categories and new weather series (max 4/day, not in quiet
  hours). Turn off with `launch_watch = false`. Research: `research_launch/`.
  Nov 5 backtest of 1,839 series launched in 2026 (17M trades): makers earn
  the same in a series' first 4 weeks as later (paired diff +0.2c/contract,
  SE 0.4c), so no "be early" edge; no trading strategy built on it.
  Daily rain series (Weather Company source) are not covered.
  **Result (Nov 11):** 14 thresholds passed in 9 cities; every one was
  already $1.00 with nothing for sale, even at the provisional sighting hours
  before the official report. Rain locks are priced in. Turned off.
- Risk level: normal.

## Grading data (all in `data/`, committed by the bot)
- `alerts.csv`: every alert (mode live/shadow), settlement, P&L at suggested
  size, follow-up prices (`avail_min`, `ask_15m`, `ask_60m`) = how long the
  price stayed buyable.
- `census.json`: every locked bracket per day with first and lowest ask
  (priced-in vs tradeable).
- `rain_census.json`: rain thresholds passed, first/cheapest ask (see above).
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

## Status (Nov 4, 2026, ~10:30 AM ET)
- **Automatic runs: outside timer, live since Nov 4 2:35 PM ET.** GitHub's
  own schedule fired once in ~11 h, so Alex set up cron-job.org POSTing every
  5 min to `/repos/alexanderhabel22-commits/kalshi-weather-bot/actions/workflows/scan.yml/dispatches`
  (body `{"ref":"main","inputs":{"command":"scan"}}`), using fine-grained
  token `kalshi-timer` (this repo only, Actions: read and write).
  **Token expires Feb 2, 2027:** remind Alex in late January to regenerate it
  and paste it into the cron-job.org job. Steps:
  `/mnt/project-files/setup/plan-b-timer.md`.
- **Grading verified on real data (Nov 3 settlements):** Kalshi `result` and
  `expiration_value` fill in; alerts.csv, forecasts.csv and REPORT.md grade
  correctly. No code change needed.
- **Finding to watch:** on Nov 3 the official highs came in 1-2°F above the
  bot's observed max in 5 of 7 graded East Coast cities (e.g. Philadelphia
  69.1 → 71). That's the safe direction for LOCKs (they only ever say "at
  least X") but it means missed locks. Model log lines now show the source of
  the observed extreme and the count of 6-hour groups. Suspected bug:
  6-hour groups are stamped ~:53 past the hour, so the filter
  `o.time - 6h >= window_start - 5 min` drops the first group of the day for
  Central-time stations (0 groups seen at 10 AM CT). Loosening the tolerance
  touches LOCK logic: ask Alex before changing it.
- Lock census so far: 65 decided brackets, 0 cheap enough (all ≥98¢ when
  first seen; Nov 3 was only scanned after 10 PM).
- Silent EDGE: 1-2, market's Brier 0.16 vs model 0.38. ~70 pending.
- No real trades yet. Alex's one earlier manual trade (NY governor, Hochul
  YES, +$1.13) is unrelated to the bot.

## Automatic trading (built Nov 5, 2026)
- `wxbot/trader.py` runs after every scan via `.github/workflows/trade.yml`
  (workflow_run; scan.yml untouched). Strategies are plug-ins in
  `wxbot/strats/` (NAME + propose) with a `[trading.<NAME>]` config table.
- Global `[trading] mode` (or TRADING_MODE variable): off (kill switch) /
  paper / live. A strategy trades real money only if mode is live AND its
  table says `live = true`; otherwise paper. New edges start on paper.
- Limits are % of bankroll: open 60%, daily stop 10%, total stop 25% (halt +
  cancel all; clear with reset_halt). Limit orders only (post-only, except taker = true strategies: LOCKs); one order
  per market+side; per-strategy and per-event caps; ntfy on fills and stops.
- Live needs secrets KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY. Ledger:
  `data/trade_orders.csv`. Reports: `wxbot/digest.py` writes
  `reports/daily|weekly|monthly/` at 9 AM ET and pushes a link.
- **REAL MONEY since Nov 5 7 PM ET (Alex's OK):** `[trading] mode = "live"` with
  live = true for favorites, macro_fade (3 contracts since Nov 12) and weather_late (late-day lows, Nov 11;
  Mentions moved to paper Nov 12 after the audit;
  weather day-before was a small test until Nov 11, now paper). LOCKs stay
  paper (weather had 0 settled paper orders). Rails: 100% live cap (75% until Nov 12, Alex OK), 20% per event,
  -10%/day pause, -25% total halt + cancel, cash check, ntfy on fills/stops/rejected
  orders, EV exits (sell only when bid after fee beats mid + hold_edge). Kill switch:
  GitHub variable TRADING_MODE=off. Since Nov 10 "off" also cancels the main bot's resting real-money orders (held positions stay).
- **Weather day-before: back to PAPER since Nov 11** (`[trading.weather] live = false`). The
  real-money test (Nov 5-11) lost $3.95 on 43 contracts and the paper record was -2.0c per
  contract on 162 fills. Cause (research_wx/RESULTS.md): since September the market's own
  implied forecast misses by 1.31F at 36h, our model mix by 1.60F, so the model has almost
  nothing the price doesn't. Re-check in Jan-Feb; it keeps logging on paper.
- **Audit of Mentions / favorites / Fed-CPI (Nov 12, research_main/RESULTS.md):** an exact
  replay of each live rule on hourly bid/ask and trade tapes (one fixed-size order per
  market, fill only when a later trade prints at our price). Mentions NO: +0.9c ± 0.9 per
  filled order Jun-Nov 2026, -0.4c ± 1.3 in the last 90 days: no measurable edge at our
  size (the earlier +4 to +6c was weighted by contracts). Favorites: +3.2c ± 0.9, about
  +2.0c ± 1.5 on the markets the 7-day rule can reach (election markets close early and
  are mostly skipped). Fed/CPI fade: +9.4c ± 2.1 (+8.7c after maker fees), 45 of 52 release
  days up, about 8 fills a month. Done Nov 12 2:34 PM ET (Alex: "whatever you recommend"):
  Mentions to paper, Fed/CPI 1 -> 3 contracts. Late-day lows 1 -> 2 after its ~Nov 26 review if it holds. Re-run: push research_main/fetch.py on the research branch (workflow
  main-audit-research, output on branch main-audit-data), then replay.py and analyze.py.
- **Fed/CPI fade on weekly gas + jobless claims (PAPER, Nov 12):** `wxbot/strats/macro_wide.py`,
  `[trading.macro_wide]` (REAL 1-contract test since Nov 12, Alex OK). Same rule as macro_fade on KXAAAGASW and KXJOBLESSCLAIMS. Replay of 11
  extra scheduled-number series (Nov 2025-Nov 2026): these two +6.6c +- 2.7 per fill over 90 weekly
  events; payrolls -33c (left out), TSA/diesel ~0. Picked 2 of 11, so paper first.
- **Favorites decision-date shadow (PAPER, Nov 12):** `wxbot/strats/favorites_wide.py`,
  `[trading.favorites_wide]` (REAL 1-contract test since Nov 12, Alex OK). Places 1-contract orders on big-event favorites whose listed
  close is far out but whose `expected_expiration_time` is within 7 days (mostly elections, which
  the live 7-day close rule skips; replay Elections +6.5c). Reuses the live favorites scan, no
  extra Kalshi calls, never places a market the live rule places. Real money needs Alex's OK.
- **Late-day weather paper test (Nov 11):** `wxbot/latepaper.py` writes paper orders to
  `data/paper_late.csv` once per bracket between 12 and 5 hours before close: NO on brackets
  beyond the low/high the station has already recorded (spread 4c+, 1c inside the offer), YES
  on the bracket holding it. Backtest for lows: +5.7c per contract Oct 2025-Aug 2026, +3.7c
  Sep-Nov 2026, about 7 fills a day. Review at 300 filled low orders; real money needs Alex's
  OK. Switch off with `[markets] paper_late = false`.
- **Late-day weather on 16 more low cities, REAL since Nov 13** (Alex: "start em up with real money
  whenever" if confident; replay +3.9c need / +4.2c holds, `research_wx/RESULTS_cities.md`). Kalshi
  lists weather.com as every weather series' settlement source now, so discovery can't read the
  station; new series are in `wxbot/stations.py` DEFAULT_SERIES (station from the rules' "(CLIxxx)").
  New cities trade 1 contract (`series_contracts`). Highs "need" back to paper (replay ~0) and the
  new Newark/Trenton/San Diego HIGH series stay paper (`skip` in `[trading.weather_late]`).
  weather_late cap raised 15% -> 40% of bankroll (Alex, Nov 13 12:31 AM ET).
- **Late-day lows at 2 contracts since Nov 12 10:41 PM ET** (Alex "sure do it"): `contracts_by = {"low/need" = 2}`
  in `[trading.weather_late]`; highs/holds stay 1. Live cap is 100% of bankroll (PR #224).
- **Late-day highs and "holds": REAL MONEY since Nov 12** (Alex 2:45 PM ET "Switch them on", PR #221):
  `[trading.weather_late]` metrics low+high, kinds need+holds, still 1 contract, same caps.
- **Late-day lows: REAL MONEY since Nov 11** (Alex, 2:18 PM ET: "implement the strategy you
  mentioned with real money, it can replace the strategy you got rid of"). Strategy
  `weather_late` (`wxbot/strats/weather_late.py`, `[trading.weather_late]`) mirrors the
  paper orders in `data/paper_late.csv` that are lows + kind "need": 1 contract, NO 1c inside
  the offer, hold to settlement, 15% of bankroll cap, 8% per city-day, own loss stop at -10% of
  bankroll, never crosses the spread. Highs and "holds" orders stay paper. Reviews at
  100/300/500 contracts; the freeze applies until 500. Back to paper: `live = false`.
- **LOCK auto-buying, PAPER (real money paused Nov 5 PM after a false Philly LOCK from a bad 6-hour METAR group; 6-hour groups no longer make LOCKs):** to go live set `[trading] mode = "live"`
  and `[trading.locks] live = true`; every other strategy stays paper. The scan
  writes every LOCK it sees to `data/lock_signals.json` (quiet hours/alert
  limits don't apply); `wxbot/strats/weather_lock.py` re-reads the market and
  buys at the live ASK (immediate-or-cancel, never rests) while 99.5% - ask -
  taker fee >= min_edge_lock and ask is 20-97c. Size = the alert's Kelly sizing,
  shrunk to fit the 20% event / 75% live caps and the account's cash; loss stops
  apply. ntfy "BOUGHT" on each fill. Until the key secrets exist, live
  strategies are skipped (logged). Kill switch: TRADING_MODE variable = off.
- Strategies: mentions (paper, Nov 5), weather day-before (off until verdict). Mentions settings re-tuned Nov 5 by walk-forward (research_mn/RESULTS.md): min_volume 1000, size 5% of bankroll per word.

- **Paper trading, Nov 5 → weekend Nov 7-8:** `wxbot/daybefore.py` rests paper
  limit orders 1c inside the spread ~36h before close when a market+model
  blend (logistic, fit on the 1-year backtest) says the side is worth 6c+ more.
  Fills are checked on Kalshi's trade tape after close; graded in
  `data/paper_orders.csv` and REPORT.md. Parameters:
  `data/daybefore_params.json` (rebuild with `tools/daybefore_params.py` from
  the `backtest-data` branch). Backtest: +10-25%/month Jan-Aug 2026, only
  +2-4% Sep-Oct (not significant). Alex: if positive by the weekend, deploy
  the $37 live (proposed caps: 1 contract/order, <=$15 open, stop at -$5).
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

## Scaling (Nov 7, 2026)

How much the strategies can take before they move the market: `research_scale/RESULTS.md`
(the capacity rail in the trader enforces it). Tracking plumbing for thousands of orders a day:

- Markets are read in batches (`kalshi.get_markets`, 100 per request, one-by-one fallback).
  Live orders sync from one `/portfolio/orders?status=resting` listing; only orders that
  left the list are read individually. The paper tape is read once per market, starting
  from each order's `checked_at`.
- Finished orders (settled / unfilled / cancelled, market closed 2+ days ago) move once a day
  to append-only `data/orders/<YYYY-MM>.csv`. `data/orders/summary.json` keeps all-time
  realized P&L (used by the loss stops) and recent placed contracts (weekly capacity).
  `trader.load_all()` = archive + hot, for reports, digests and the dashboard.
- Fill/settle/sold/bought pushes are bundled into one when a run has more than 5.
- Up to `max_orders_per_run` (40) per 5-minute run, and placing stops after `run_budget_s`
  (240 s) so the 8-minute job never times out. That is ~11k orders a day at most, within
  Kalshi's Basic rate limits (the Http client spaces requests 0.12 s apart).
- Next step, only when needed (bankroll ~$50k+, or a strategy that needs seconds, like
  hourly-temp market making or BTC 15-min): an always-on ~$5/month server (e.g. a small
  VPS) running the trader in a loop with the websocket fill feed and SQLite instead of CSV
  in git. Nothing is bought until Alex says so.

## Strategy allocator (Nov 7, 2026)

Alex asked for the bot to "grade itself" and move money to what works without
killing any strategy. `wxbot/allocator.py`, settings in `[allocator]`:
- Grade = profit per $1 risked on settled results (not hit rate), bets on one
  event counted as one result, REAL money only (paper ignored, Alex Nov 7), shrunk toward a +3c hurdle
  by how noisy the record is (longshots need many more trades to be trusted).
- Size multiplier = grade / hurdle, kept in 0.25x-2x, re-graded weekly, moving at
  most 0.25 per week. When applied it scales each strategy's own cap and order
  size and gives the best-graded first claim on the overall cap. The 75% overall
  cap, 20% per-event cap and loss stops never change.
- SHADOW (`apply = false`) since Nov 7: grades are in REPORT.md and
  data/trader_state.json ("allocator"). Plan: go live at the Nov 17 checkpoint
  (Alex, Nov 7) by setting `apply = true`.
- Backtest: research_alloc/RESULTS.md (beat fixed sizing in 20/20 samples).
- The 15-minute bot is graded too (Nov 8), per coin (m15_btc, m15_silver, m15_copper), from
  its real fills on the m15-data branch (read on re-grade day and in the report). Report-only:
  the server's own 100/300-fill cut/raise rules decide its size.

## Dashboard (built Nov 5, 2026)

Read-only, mobile-first monitor in `dashboard/` (one static `index.html`, no build). It reads the
repo's public data files from raw.githubusercontent.com every 2 minutes (pinned to the latest main
commit, so no stale cache) and the GitHub Actions API every 6 minutes (60 requests/hour limit per
IP without a token). It never places, changes or cancels anything and needs no secrets.

- Hosted on GitHub Pages by `.github/workflows/pages.yml`, which deploys only when `dashboard/`
  changes (not on bot data commits). One-time setup: Settings -> Pages -> Source: GitHub Actions.
  URL: https://alexanderhabel22-commits.github.io/kalshi-weather-bot/
- LIVE / PAPER switch: same layout for both. LIVE = `mode=live` rows of `data/trade_orders.csv`
  plus `data/my_trades.csv`; PAPER = paper rows plus `data/paper_orders.csv` (weather day-before).
  Silent EDGE / near-lock calls (`data/alerts.csv`) are shown separately as signals, not trades.
- Research tab reads `dashboard/verdicts.json` (verdicts, checkpoints, backtest edge per
  strategy). Update it when a research verdict lands or a strategy is added.
- The repo is public, so everything the page shows is already public. Never put NTFY_TOPIC or
  API keys in `dashboard/`.

## 15-minute bot on its own server (built Nov 8, 2026)

- Why: 15-min up/down markets (BTC +7.3c, silver/copper +5.2c, alt crypto +3.1c per contract
  backtest, maker longshot fade, back-of-queue) close too fast for the 5-minute Actions bot.
- Code: `wxbot/m15.py` (engine: join the 71-95c favourite's best bid, 1 contract/market,
  paper fills only on a trade-through, grading to `markets-YYYY-MM-DD.csv`), `wxbot/m15_feeds.py`
  (Kalshi websocket + Coinbase spot jump guard; REST stays the source of truth).
  Run: `python bot.py m15`. Settings: `[m15]` in config.toml.
- Server: DigitalOcean ~$4/mo, Ubuntu, systemd `kalshi-m15` (+ hourly `kalshi-m15-update.timer`
  that pulls main and restarts). Setup for Alex: `deploy/m15/SETUP.md`.
- Fast switch (Claude): `m15_control.json` on main, mode live|paper|off, read every minute (~5 min with the CDN). `reset_halt` = a new string clears a total-loss halt.
- Results: uploaded every 15 min to branch `m15-data` (`m15/status.json`, `m15/markets-*.csv`).
- Rails: real orders only with `[m15] mode = "live"`, series in `live_series`, and the repo
  kill switch allowing it (the Actions trader writes `mode` into data/trader_state.json;
  TRADING_MODE=off stops the server bot too). Pool 25% of bankroll (Alex: the 25% the 75% cap
  leaves free), daily stop -10%, halt at -12.5%; no live orders if the switch can't be read for 30 min.
- Plan: LIVE from the start (Alex, Nov 8: no paper run) on BTC/silver/copper at 1 contract. Cut a market below
  -5c/contract after 100 fills. Expect +2-4c live (stale-quote risk vs fast pros), not +7c.

## AI ops watcher, "the Foreman" (built Nov 10, 2026)

- `wxbot/watch.py`, `python bot.py watch`, settings in config.toml `[watch]`. Runs every 15 min on the
  15-min bot's server (systemd `kalshi-watch.timer`, installed by the hourly update via
  `deploy/m15/install-watch.sh`). Needs `ANTHROPIC_API_KEY` in /etc/kalshi-m15/env
  (`sudo bash /opt/kalshi-m15/app/deploy/m15/set-anthropic-key.sh`); without it, it does nothing.
- Each run: fixed rules over both bots (15-min status/service/log, overfills, mode vs control,
  exposure vs pool, scan gaps, failed scan/trade runs, stale balance) -> Claude Haiku verdict
  (ok/watch/escalate) -> on escalate/critical, Claude Opus writes the ntfy push and a proposed fix
  (max 6/day, same problem re-alerts after 6h). Cost ~$1-3/month on Alex's API credit.
- Rails: may set `m15_control.json` mode to paper ONLY on a malfunction flag (overfill, mode
  mismatch, exposure above pool) when Opus agrees or is unreachable. Never for losses, never
  un-pauses, never changes caps/stops/sizes/code. Results: `m15/watch.json` on branch m15-data;
  the scheduled Claude check-ins read it and turn proposed fixes into PRs.

## Copycat (built Nov 10, 2026; real money per account once its paper test passes)

- Alex's idea and name: copy proven Kalshi winners' trades. `wxbot/copycat.py`, `python bot.py copycat`,
  separate systemd service `kalshi-copycat` on the 15-min server (installed by the hourly update via
  `deploy/m15/install-copycat.sh`; CPU 10%, 200 MB cap). Off: set `"mode": "off"` in
  `copycat_control.json` on main (read every minute; also off if the file can't be read for 10 min,
  if TRADING_MODE is off/paper or the main bot is halted). Whole service: `sudo systemctl disable --now
  kalshi-copycat` and `sudo touch /var/lib/kalshi-copycat.disabled`.
- Uses Kalshi's public social API (`api.elections.kalshi.com/v1/social`): every 12 h it rebuilds the
  watch list (profit boards, public trades, >= $500 lifetime profit, max 1,000; saved in watch.json); every 10 min it scouts
  every name seen in the global public trade feed. It reads that feed about once a second (one read
  covers every account) and also polls accounts round-robin as a backstop (passing ones get
  every other poll, so a few seconds apart). Feed delay measured Nov 10: median ~10 s.
- PAPER record (every account): each of their fills is scored as if copied at the price ~10 s later,
  after Kalshi's taker fee. Score 0-100 = 80 + 2.5 x the pessimistic (95% lower bound) cents per
  contract, +/-3 for a hot/cold last 50; letters A+ 97 ... B- 80 ... F < 60. Judged at 100 paper copies
  if it scores 87+ (B+), otherwise at 150 (Alex, Nov 10; the 7-day shortcut was dropped). 80+ = copied, while its last 50 average better than -leeway (5c, +1c per $1
  it has made us, up to +5c for a high score); "slump" below that, back when it recovers. Size 1,
  90+ = 2, 95+ with 300 settled = 3.
  DROPS (Alex, Nov 10 10:31 PM ET): an account that fails at 150 copies (score under 80, e.g. a
  market maker), or loses money over the last 30 days on two weekly re-checks in a row, is dropped:
  no polls, snapshots or copies for 30 days, then it may be re-scouted and starts again from zero.
  Kept in watch.json "dropped" and listed on the dashboard.
  CHANCES (Alex, Nov 11 6:10 PM ET, "don't disqualify someone for a bad day"): the score uses the
  LAST 150 copies; a fail at 150 is re-tested at 200 and 250 (TEST_CHANCES=3, RETEST_EVERY=50), and
  only 3 fails in a row drop it; a pass in between resets the count (watch.json fails_in_row/retest_at).
- REAL record (`wxbot/copycat_live.py`, state `/var/lib/kalshi-copycat/live_state.json`): a passing
  account's new fill is bought immediately-or-cancel at their price + 2c max, 5-95c, within 60 s of
  their trade, one copy per market, never a market we already hold (any bot) and never the 15-min
  bot's live series. Rails in `copycat_control.json`: open copies <= 15% of bankroll, an account is
  benched after losing 10% of bankroll on real copies, Copycat halts at -25% total (reset: edit
  live_state.json "halted" to null). Alex approved real money at the 100-copy threshold on Nov 10.
- The boss (`wxbot/copycat_boss.py`): Claude Haiku triage every 15 min; Claude Opus full review every
  2 h when something changed or when Haiku asks (max 16/day). Writes a plain-English note per
  account and a summary (dashboard), can bench an account 24 h for a reason the numbers can't see
  (style change, market maker, broken bot) and lift its own benches. Can't promote or raise sizes.
  ~$0.20-0.70/day. Needs ANTHROPIC_API_KEY in /etc/kalshi-m15/env (already there for the watcher).
- Dashboard COPYCAT tab: real P&L, at-risk vs pool, boss summary, per-account PAPER and REAL lines,
  progress bar to the 100-copy test, list of real copies. Data: `copycat/status.json` on branch
  `copycat-data` (every 5 min) plus hourly logs (fills, snaps, copy decisions, orders, settlements).
- Don't copy market makers (their fills are flow, not views); the score fails them on its own. The top
  ~50 accounts hide their trades. Background: Library analysis/leaderboard/RESULTS.md.

## Liquidity-reward test (built Nov 11, 2026; real money, small, 3 days)
- Kalshi pays daily rewards for resting orders in ~5,000 markets until Jan 1, 2027 (GET
  /incentive_programs). Research: /mnt/project-files/analysis/overnight-ideas/RESULTS.md, idea #1.
  Alex OK'd a ~$20, 3-day real test on Nov 11 8:23 AM ET.
- `wxbot/lip.py`, `python bot.py lip`, systemd service `kalshi-lip` on the 15-min server (installed by
  the hourly update via `deploy/m15/install-lip.sh`). Every 5 min: rests post-only bids 1c above the
  best bid on the cheap side (1-8c) of 8 quiet reward markets (market closes 21+ days out, exchange
  shard 0, no sports), front of the line, moves up at most every 30 min when outbid.
- Switch `lip_control.json` on main: mode live/off, budget $20 (resting + filled), reserve $6 of free
  cash always left on shard 0 for the main bot, fill_stop $10 (halt), until 2026-11-14 16:00 UTC (goes
  off by itself). Also off with TRADING_MODE off/paper, a live halt, or if the switches can't be read
  for 30 min. Off cancels every bid. Fills are held to settlement.
- Orders use client ids `lip-` and their own ceilings (kalshi_api.LIP_MAX_*: <=500 ct, <=10c, <=$6
  per order). The tax ledger tags them "rewards bot".
- Payouts: logged from GET /portfolio/credit_history every hour. Status: `m15/lip-status.json` on branch
  `m15-data` (every 15 min: orders, fills, our estimated share, credits, events). State
  /var/lib/kalshi-lip/state.json, daily logs there too.

## Paper tests: wide-spread maker + parlay quote requests (built Nov 11, 2026; no orders)
- `wxbot/paperlab.py`, `python bot.py paperlab`, systemd service `kalshi-paper` on the 15-min server
  (`deploy/m15/install-paperlab.sh`). GET requests only; the Kalshi key is wrapped read-only.
- Maker: ~20 wide-spread (3c+) non-sports, non-crypto markets (Mentions first, up to 8). Each minute a
  1-contract paper bid on each side 1c above the best bid; a later taker trade through our price fills
  it; markouts vs the mid at 5 and 30 min. Status `m15/paper-mm.json` on `m15-data` (hourly).
- Parlays: polls GET /communications/rfqs every 20 s. For combos whose legs are all in different events,
  fair = product of leg mids; logs the YES-selling quote at margins 0/5/10/20%, whether the combo then
  traded above it (we'd likely have won), and paper P&L at settlement. Status `m15/paper-rfq.json`.
- **Casual-flow tags (Nov 11):** each paper maker fill records the market's casual share (share of
  taker contracts from orders of $25 or less) and is scored again at settlement; `by_flow` in
  `m15/paper-mm.json` splits results by casual share and side. Research: `research_casual/RESULTS.md`
  (small orders lose ~3c/ct everywhere; rest NO bids only, never YES).
- **Sell the field (paper, Nov 11):** `FieldPaper` logs a paper NO on every 10-50c candidate of each
  open one-winner event with 5+ candidates (Entertainment, Social, Companies, Sci/Tech, World, Health),
  bought at the NO ask, scored at settlement after the taker fee. Status `m15/paper-field.json`.
  Target events and dates: `research_casual/CALENDAR.md` (Spotify Wrapped ~Dec 2, TIME and Google
  ~Dec 8-11, Super Bowl ~Feb 14). Real money needs Alex's OK.

## Source shadow logger (built Nov 10, 2026, logging only)

- `wxbot/srclog.py`, `python bot.py srclog`. Separate systemd service `kalshi-srclog` on the 15-min
  server (installed by the hourly update via `deploy/m15/install-srclog.sh`; CPU 20%, 300 MB cap).
  Never loads the Kalshi key, never orders. Off: `sudo systemctl disable --now kalshi-srclog` and
  `sudo touch /var/lib/kalshi-srclog.disabled`.
- Polls the settlement pages of data-settled markets (RCP Trump approval, Arena no-style-control
  leaderboard, Netflix Top 10 TSVs, TSA volumes, HF Arena dataset, each listed Rotten Tomatoes title),
  logs every change with the raw page, and snapshots the linked Kalshi series at +0/10/30/60/300 s.
  Hourly files on branch `srclog-data` (srclog/srclog-YYYY-MM-DD-HH.jsonl.gz).
- Why: research_src/race.py (Mar-Nov 2026 tapes) shows the first ~3 s of each repricing is swept by a
  faster bot, but stale prices keep trading for 3 s-5 min (Trump approval ~+20-39c/ct). Judge after
  1-2 weeks: our detect time vs the first Kalshi move. Write-up: Library analysis/source-lag/RESULTS.md.

## Scale-up kit (built Nov 10, 2026, switched OFF)
`wxbot/scaleup.py` + `[scaleup]` in `config.toml` (`enabled = false`). Turn it on (with
Alex's OK) after the 500-contract reviews and the Nov 17 checkpoint pass, before adding bankroll.
While off, nothing changes; the 15-min server only keeps each coin's step record.
- Bankroll tiers tighten limits, never loosen them (min of config and tier): $300+ main live cap
  50%, daily 8%, halt 20%, 15-min 24h stop 15%, halt 25%, window guard on; $1k+ 6%/15% and
  10%/15%; $5k+ 4%/10% and 6%/10%.
- 15-min contracts step 1 -> 2 -> 4 per coin, one step at a time: needs the 500 review count,
  300 real contracts at the current step at +1c or better, 3 days there, and bankroll $150 / $400.
  Last 300 at a step below -2c -> back one step. The 80-87c extra contract scales with the step.
- Push budget: after 40 routine trade pushes in an ET day the main bot stops pushing fills/sales
  (one note says so); stops and warnings still push.

## Tax ledger (built Nov 10, 2026)

- `python bot.py tax` (wxbot/taxledger.py), run by `.github/workflows/tax-ledger.yml` on Mondays,
  Jan 2 and on demand. READ-ONLY: GET /portfolio/fills + /portfolio/settlements (+ titles).
- Builds LEDGER.md (by year, bot, month), fills-YYYY.csv, settlements-YYYY.csv,
  markets-YYYY.csv (cost, proceeds, fees, net per market and bot), open-positions.csv.
- PRIVATE (repo is public): Actions commits only the ENCRYPTED copy (reports/tax-ledger.tgz.enc +
  .key.enc, encrypted to deploy/tax-ledger-public.pem). The private key is only in the project
  Library (tax/keys/). Unpack with `tools/tax_decrypt.sh <private.pem> /mnt/project-files/tax/`.
  Never commit the readable files (reports/tax/ is gitignored).
- Kalshi's records are the truth. Fills are tagged main bot (order/client id in trade_orders.csv,
  "wx-"), 15-min bot ("m15-" or a KX...15M series) or manual. data/my_trades.csv is shown on its
  own and never added. Each settled market is checked against Kalshi's paid revenue.
- Not tax advice: Kalshi's own year-end form is what gets filed; this backs it up.

- Nov 11: Alex named it "the Foreman": small persona in its pushes ("👷 Foreman: ..."), and a
  memory: `journal` (last 30 notable events: flags appearing/clearing, escalations, pushes,
  pauses) and `notes` (up to 8 standing facts Haiku/Opus chose to remember), both fed back into
  every check so it spots repeats; flag streaks ("N checks in a row"). Kept in watch_state.json
  on the server and shown in m15/watch.json. Also watches RAM: server >= 85% used, or kalshi-m15
  >= 85% of its systemd MemoryMax. update.sh no longer restarts the 15-min bot for watcher-only
  changes.

## Expectations to keep honest
Likely 0–3 LOCK alerts a day, ~10–15¢ expected each at this bankroll; a wrong
lock costs ~$3. Order books are thin (some ~5 contracts at the alert price),
so the ceiling is tens of dollars per trade. Not financial advice.

## Background from the original chat (Nov 3, 2026)
This project continues a long chat that sits outside the project (project
chats can't search it), so this section is the bridge.

**How we got here**
- Alex asked how Kalshi makes money (trading fees on every contract, interest
  on idle cash; it isn't the house) and how to profit. We covered: real edge =
  specialized knowledge, market making, the favorite-longshot bias, speed on
  news. A 95¢ favorite risks 95¢ to make 5¢, so one loss erases ~19 wins.
- Alex's first trade: NY governor, Hochul YES at ~97¢, $35.56 → $36.69
  (+$1.13, +3.18%), closed the same night. "3% a day" compounding was shown
  to be a mirage (survival odds, few markets, capital lock-up, fees).
- Sizing advice given: 2–5% of bankroll per trade, 10% max; Kelly is very
  sensitive to a 1-point error in the probability estimate.
- Kalshi vs Polymarket arbitrage: real but thin; fees, different resolution
  rules, execution risk; Polymarket's international site isn't available to
  US users.

**What Alex wants**
- As much money as possible, with alerts making it "that easy." Wants it as
  good as it can be. Willing to be more aggressive once it's proven.
- Using real money (~$37) as the test instead of paper trading; will add
  money if confident. OK paying a bit per month if confident it beats the
  market.
- Wants Claude to analyze results, grade itself, and track the alerts he
  skips (the ledger grades every alert, taken or not).

**Rules Alex was given**
- LOCK alert → limit order at or below the alert price, suggested size, never
  chase a moved price, log what he took (tell Claude, screenshot, or
  `data/my_trades.csv`).
- Ignore EDGE. Don't size above the bot. If a LOCK ever loses, stop and
  diagnose before the next one.
- Bankroll is automatic (Nov 5): each trade run reads Kalshi cash + open
  positions (wxbot/balance.py -> data/bankroll.json) and every size, cap and
  stop scales to it. Deposits change limits, never P&L. `BANKROLL` /
  `[account] bankroll` are only the fallback if the read fails for 6h.
- When to expect locks: daily-low locks ~7–10 AM ET (quiet hours end at 7);
  daily-high locks ~1–5 PM ET for East Coast cities, up to ~7–8 PM ET for
  the West.
- Expectations given: 0–3 locks/day, ~10–15¢ expected each, roughly $5–8 a
  month at this bankroll; caps ≈ $3.50 per lock and ≈ $14.50 open at once.
  Day-one view: software solid; whether enough cheap locks exist is a
  toss-up at best.

**Offers Alex hasn't answered yet (raise when relevant)**
- Ledger-based automatic bankroll (start value + settled P&L). Only accurate
  if he takes every alert.
- A weekly scheduled review (e.g. Sunday evening report to his phone).
- Later, if earned: a Kalshi API key for the real balance/fills and
  auto-buying locks. Kalshi keys can also trade, so storing one on GitHub is
  a trust decision.
- A speed upgrade if locks vanish before he can buy: DigitalOcean $4/mo
  (512 MB) or $6/mo (1 GB, preferred), running
  `python bot.py loop --every 60`. GitHub Actions' minimum is 5 minutes, and
  runs can start 5–20 minutes late or be skipped under load.

**Research notes**
- Kalshi creates every market itself. Users can only suggest ideas
  (kalshi.com/ideas/feed); Kalshi reviews them for manipulation risk, harm,
  and a reliable settlement source; most don't make it.
- Liquidity Incentive Program: pays for resting orders near the price, even
  unfilled; listed end date Jan 1, 2027 (Kalshi can change it). The separate
  volume-based rewards program ended in October 2026 amid wash-trading
  scrutiny.
- Fees: taker 0.07·C·P·(1−P) rounded up to the cent; resting (maker) orders
  pay less or nothing on many markets; no settlement fee; ACH free. Selling
  early pays a second fee.
- Weather settlement: NWS Daily Climate Report, whole °F, usually the next
  morning. Hourly temperature markets settle on The Weather Company instead,
  and the bot doesn't trade them.
- Why this won't scale far: thin books (~5 contracts at the alert price on
  some brackets), few chances a day, bot competition, capital tied up
  overnight. Ceiling ≈ tens of dollars per trade.
- Scaling ideas, in order: rain markets (same lock logic, about a dozen
  cities), then market making (resting orders + liquidity rewards; risk of
  getting picked off; needs a server and an API key), then markets that
  settle on a public number (research each), and cross-venue arbitrage
  (thin).
- EDGE policy: not now. Turn it on only after 50+ settled silent calls with a
  profit and a Brier score better than the market's, and even then cap it
  at 5%.

**Live calibration notes (first real scan, Nov 4 03:06 UTC)**
- All 143 ensemble members loaded, NWS forecasts present, medians mostly
  within 1–2°F of NWS.
- Day-ahead 80% ranges were 6–9°F wide for highs and much wider for lows
  (Denver low 25–40, Austin low 48–60, LA low 52–65), while markets put
  50–60% on a single 2°F bracket. Likely fixes: anchor harder to NWS,
  shrink sigma/spread inflation, especially for lows (the low window
  includes late evening, when front timing is uncertain).
- Discovery found 29 series, including a couple of unexpected tickers
  (e.g. KXHIGHOU next to KXHIGHTHOU). Worth a look.

**Alex's setup**
- GitHub account `alexanderhabel22-commits`. A local copy in GitHub Desktop
  on his Windows PC is behind; Fetch/Pull before editing there.
- ntfy on iPhone (new private topic), Kalshi app on phone.
