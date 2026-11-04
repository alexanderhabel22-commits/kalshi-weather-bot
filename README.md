# Kalshi Weather Alerts

A bot that watches every Kalshi daily high/low temperature market (20 cities),
prices each bracket from live station readings and weather-model forecasts, and
pings your phone with an exact trade when the price is wrong. It never trades
for you, and it keeps a paper record of every alert so you can see whether it
actually works before you trust it with money.

## What an alert means

- **LOCK**: today's official station readings have already decided the
  bracket. Example: Central Park already hit 61.2°F, so "59° to 60°" can't win,
  but NO is still selling at 95¢. These are the core idea.
- **EDGE**: a forecast-based call where the model disagrees with the price.
  These are **silent by default** (`edge_alerts = "shadow"` in `config.toml`):
  the bot logs and grades them without sending them. The nightly summary shows
  their record ("Silent EDGE test"). Only switch them `"on"` once that record
  shows the model beating the market over a few weeks.

Each alert tells you the bracket, YES or NO, the highest price to pay, how many
contracts (sized from your bankroll), the expected profit, and why. Tap
**Open Kalshi**, find that day and bracket, and place a **limit order** at or
below the listed price.

## Setup (about 10 minutes)

1. **Phone:** install the free **ntfy** app (App Store / Play Store). Tap **+**,
   subscribe to topic `wx-alerts-wzszi9l3sxcx` (or any long random name you
   like; whoever knows the name can read it, so keep it unguessable).
2. **GitHub:** create a new repository and put these files in it. A **public**
   repo gets free unlimited scans. Your secrets stay hidden either way, but the
   code and `data/alerts.csv` are visible.
3. In the repo: **Settings → Secrets and variables → Actions → New repository
   secret**. Name `NTFY_TOPIC`, value = your topic name.
   Optional: under the **Variables** tab add `BANKROLL` (e.g. `35`) so you can
   change it without editing files.
4. **Actions** tab → enable workflows → **weather-scan** → **Run workflow** →
   choose `test-notify`. Your phone should buzz. After that it scans every
   5 minutes on its own (GitHub's fastest schedule; runs can start late).

Private repo instead? Change the cron line in `.github/workflows/scan.yml` to
`*/30 * * * *` to stay inside GitHub's 2,000 free minutes a month.

Faster than 5 minutes needs an always-on server: run
`python bot.py loop --every 60` there instead of using GitHub's schedule.

## Your track record

- A **summary** arrives after 9 PM each day: alerts sent, wins/losses,
  paper profit, and whether the model or the market has been more accurate.
  If the summary stops arriving, the bot is down.
- Every alert is logged in `data/alerts.csv` and graded automatically once
  Kalshi settles it (the next morning).
- Run the workflow with `report` to see the record plus the model's view of
  every city in the latest scan.

## Risk level

Add a GitHub variable `RISK_LEVEL` = `aggressive` (Settings → Secrets and
variables → Actions → Variables) to roughly double LOCK sizes: up to 20% of
bankroll per lock, 25% per city-day, 60% open, half Kelly, locks from 2¢ edge.
Forecast (EDGE) sizing doesn't change. Delete the variable to go back.
Suggested: switch only after about 20 LOCK alerts have settled correctly.

## How the bot grades itself

Everything lands in the `data/` folder of the repo, and **`data/REPORT.md`** is
rewritten every night (tap the 9 PM summary to open it):

- **Alerts sent to your phone:** every alert, won/lost, profit at the suggested
  size, and an explanation of every loss.
- **Did the prices last?** After each alert the bot keeps checking the price,
  so you can see if locks are still buyable 15 and 60 minutes later. This is
  what decides whether a faster paid server is worth it.
- **Lock census:** every bracket the readings had already decided, and how many
  were ever cheap enough to buy versus already priced in by other traders.
- **Your actual trades:** add a line to `data/my_trades.csv` for each trade you
  place (`ticker,side,contracts,price,note`, price like `0.94`), or tell Claude
  and it will add them. The report shows your real record separately.
- **Silent EDGE test:** the forecast calls it didn't send, graded, with model
  vs. market accuracy.
- **Forecast accuracy:** the model's forecast for every city-day versus the
  official result (bias, average miss, whether its ranges are honest), next to
  the NWS forecast's miss.

Run the workflow with `report` to refresh it any time.

## Settings (`config.toml`)

| Setting | Default | What it does |
|---|---|---|
| `bankroll` | 35 | Sizes every trade. Update it as your balance changes. |
| `min_edge_lock` / `min_edge_model` | 3¢ / 7¢ | Minimum edge after fees to alert. |
| `max_bet_frac_lock` / `_model` | 10% / 5% | Max share of bankroll on one alert. |
| `max_open_frac` | 40% | Max total money tied up at once. |
| `quiet_start` / `quiet_end` | 0 / 7 | No alerts overnight. |
| `include_lows` | true | Also watch daily-low markets. |
| `max_days_ahead` | 1 | 0 = today's markets only. |
| `series` / `skip_cities` | all | Limit which cities it watches. |

## Honest limits

- The model's settings (forecast noise, lock safety margins) are reasoned
  starting points, not fitted to past results. Watch the paper record for
  2–3 weeks first. If the summary says the market beats the model, ignore
  EDGE alerts; LOCK alerts rest on observed data and should hold up better.
- Other bots trade these markets. Some locks will already be priced in, and
  GitHub's scheduler can run a few minutes late.
- Settlement uses the NWS Daily Climate Report, which is occasionally delayed
  or revised. The bot only calls a lock when readings clear the bracket by a
  safety margin, and it treats noisy 5-minute airport readings cautiously.
- Not financial advice. Only use money you can afford to lose.

## Run it on your own computer instead

```
pip install -r requirements.txt
set NTFY_TOPIC=your-topic          (Mac/Linux: export NTFY_TOPIC=your-topic)
python bot.py loop --every 300
```

Other commands: `python bot.py scan --dry-run` (print alerts, change nothing),
`python bot.py report`, `python bot.py test-notify`. Tests: `pytest`.

## How it works

1. Pulls open temperature markets from Kalshi's public API (no key needed)
   and checks each bracket's wording against its strike values.
2. For each city-day it gets the official station's readings so far (NWS,
   using the precise 0.1°C METAR groups and 6-hour max/min groups), an
   ensemble forecast of up to 143 members (GFS, ECMWF, ICON, GEM via
   Open-Meteo), and
   the NWS forecast high/low.
3. Simulates the final settlement value 20,000 times: the observed extreme so
   far versus the rest-of-day forecast, corrected for how far off the models
   have been today. The climate day follows the NWS rule (midnight to midnight
   local *standard* time).
4. Compares each bracket's probability to the ask after Kalshi's fee, shrinks
   forecast calls 25% toward the market price, sizes with quarter-Kelly under
   per-trade, per-city and total caps, and checks order book depth.
5. Alerts once per contract (again only if the edge grows a lot), logs it,
   and grades it after settlement.
