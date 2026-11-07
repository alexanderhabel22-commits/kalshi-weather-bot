# Weather early sell using today's actual temperature readings

Question (Alex, Nov 7): the weather exit only looks at forecasts. Shouldn't it also
look at the real readings so far today?

## Method (research_exit/obs_exit_bt.py, value in wxbot/obs_exit.py)

- Same entries as the live rule (day-before orders, 1 contract), Oct 2025 - Nov 2026.
- Checks at 18, 15, 12, 9, 6 and 3 hours before close.
- Value = chance the final high/low lands in our bracket, where
  final = max(highest reading so far + 0.35F, rest-of-day forecast). The forecast
  is the **day-before** hourly forecast (no look-ahead), nudged by how wrong it has
  been over the last 3 hours today. Lows: same with signs flipped.
- Blended with the market mid, fit per check hour on earlier months only (walk-forward).
- Sell at the bid when bid - taker fee >= value + margin.

## Result

| rule | sells | P&L | vs hold | months better/worse |
|---|---|---|---|---|
| hold to settlement | 0 | +$390 | | |
| readings, all check hours, margin 8c | 428 | +$446 | +$56 | 10/1 |
| readings, margin 5c | 614 | +$459 | +$69 | 10/1 |
| readings, only 18-12h before close | 224 | +$421 | +$31 | 10/0 |
| readings, only 9-3h before close | 248 | +$420 | +$30 | 10/1 |
| current live rule: same-day forecast at 12h | 335 | +$455 | +$65 | 11/0 |
| **both: readings checks + the 12h forecast check, margin 8c** | 679 | **+$487** | **+$97** | 10/1 |
| both, margin 5c | 934 | +$508 | +$118 | 10/1 |

The readings rule is about as good as the live forecast rule, and it has no
look-ahead (the same-day forecast archive might be a little fresher than what we
get live). Most of the gain comes from bets the readings have already killed (e.g.
the high already passed our bracket) that the market still bids for.

Running both rules together adds up: they catch mostly different bets (readings
catch brackets already blown past; the fresh forecast catches shifts in the rest
of the day). Part of the combo's gain comes from the same-day forecast check, which
might be a little flattered by the archive.

## Status

Shadow only (exit_obs.shadow = true in data/daybefore_params.json). Each trade run
logs, once per position and check hour, what it would do to data/exit_shadow.csv.
Nothing is sold by it. Turning it on for real needs Alex's OK.
