# Project handoff: Kalshi Weather Alerts

Use this as the Claude project's instructions. It is kept current in the repo:
when the project changes, update this file and the project instructions together.

## Who and what
Alex (Concord, NC; works full-time in sales, builds side projects with Claude)
runs a bot that watches Kalshi daily high/low temperature markets and sends
phone alerts with an exact trade. Alex places every trade himself in the
Kalshi app. The bot doesn't trade yet, but Alex said (Nov 4) he's fine with a
bot trading: going live (Kalshi API key as a GitHub secret, small caps) is
his call once paper results earn it. It is a small real-money test (~$37 bankroll,
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
  Daily rain series (Weather Company source) are not covered.
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
  cancel all; clear with reset_halt). Limit, post-only orders only; one order
  per market+side; per-strategy and per-event caps; ntfy on fills and stops.
- Live needs secrets KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY. Ledger:
  `data/trade_orders.csv`. Reports: `wxbot/digest.py` writes
  `reports/daily|weekly|monthly/` at 9 AM ET and pushes a link.
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
- Update `BANKROLL` only on a deposit/withdrawal or a ~20% balance change.
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
