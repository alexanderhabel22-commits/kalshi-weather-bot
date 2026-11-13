# Weather bot report

Updated Thu Nov 12, 09:00 PM · bankroll $50.54 · risk level normal · EDGE alerts off

## Alerts sent to your phone

**Record: 0-2**, -$4.47 at suggested sizes (-100.0% on $4.47 staked). Open: 0 ($0.00).
By type: LOCK 0-2 (-$4.47).

| When | City | Bracket | Side | Price | Lasted | Result | P&L |
|---|---|---|---|---|---|---|---|
| 11/05 17:50 | Philadelphia high 11-05 | 74° to 75° | NO lock | 28¢ | 145m+ | lost | -$1.18 |
| 11/05 16:50 | Philadelphia high 11-05 | 74° to 75° | NO lock | 64¢ | 175m+ | lost | -$3.29 |

**Every loss, explained:**
- Philadelphia high 2026-11-05: bought NO on 74° to 75° at 64¢ (lock). Bot saw observed extreme 77.0°F, forecast median 77°F; official settlement 74.00°F.
- Philadelphia high 2026-11-05: bought NO on 74° to 75° at 28¢ (lock). Bot saw observed extreme 77.0°F, forecast median 77°F; official settlement 74.00°F.

## Did the prices last long enough to buy?

- Still buyable at the alert price **15 min later: 50%** (1/2); 60 min later: 100% (2/2).
- Typical time the price lasted: 160+ minutes.

## Lock census: every bracket the readings had already decided

| Day | Locks seen | Cheap enough to alert | Priced ≥98¢ when first seen |
|---|---|---|---|
| 2026-11-06 | 84 | 0 | 84 |
| 2026-11-07 | 84 | 0 | 84 |
| 2026-11-08 | 74 | 0 | 74 |
| 2026-11-09 | 63 | 0 | 63 |
| 2026-11-10 | 71 | 0 | 71 |
| 2026-11-11 | 71 | 0 | 71 |
| 2026-11-12 | 78 | 0 | 78 |

Over these days, 0% of decided brackets were ever cheap enough to be worth buying. The rest were already priced in by other traders.

## Your actual trades (from `data/my_trades.csv`)

None logged. Add a line per trade you place: `ticker,side,contracts,price,note` (price in dollars, e.g. `0.94`). Or tell Claude and it will add them.

## Silent near-lock test (late-day calls the model rates 95%+, not yet certain)

**Record: 37-5**, -$3.99 if you had taken them (-4.7%). Pending: 4.
Accuracy (Brier, lower is better): model 0.113 vs market 0.097 → **market ahead**.

| Model said | Calls | Market said (avg) | Actually won |
|---|---|---|---|
| 90-99% | 42 | 88% | 88% |

Verdict: keep near-lock alerts silent. Needs 50+ settled calls, a profit, and the model beating the market.

## Forecast accuracy (every city-day the bot priced)

| Lead time | City-days | Model bias | Model avg miss | 80% range held | NWS avg miss |
|---|---|---|---|---|---|
| More than 24h out | 720 | +0.9°F | 1.9°F | 95% (target 80%) | 2.0°F |
| 12–24h out | 423 | +0.9°F | 1.6°F | 89% (target 80%) | 1.8°F |
| 6–12h out | 216 | +0.5°F | 0.8°F | 92% (target 80%) | 1.6°F |
| Last 6h | 163 | +0.2°F | 0.6°F | 91% (target 80%) | 1.4°F |

Bias is actual minus model median (positive = model too cold). If the 80% range holds far more than 80% of the time, the model is too unsure; far less, too sure.

Biggest city biases (12h+ out): Atlanta high +3.1°F (n=37); Dallas high +2.6°F (n=45); Minneapolis high +2.4°F (n=45); Denver low -2.4°F (n=45); Denver high +1.9°F (n=45).

## Rain census (silent test): monthly rain thresholds already passed

Provisional = NWS report plus hourly station rain since; official = NWS climate report total. No alerts are sent for these.

| Market | Above | First seen (provisional) | Ask then | Official | Ask then | Cheapest ask |
|---|---|---|---|---|---|---|
| KXRAINLEXM-26NOV-1 | 1 in | 2026-11-04T20:45 | 100¢ | 2026-11-04T22:00 | 100¢ | 100¢ |
| KXRAINMIAM-26NOV-5 | 5 in | 2026-11-04T20:45 | 100¢ | 2026-11-04T21:45 | 100¢ | 100¢ |
| KXRAINCMHM-26NOV-1 | 1 in | 2026-11-04T22:00 | 100¢ | 2026-11-04T22:00 | 100¢ | 100¢ |
| KXRAINLEXM-26NOV-2 | 2 in | 2026-11-04T22:30 | 100¢ | 2026-11-05T00:45 | 100¢ | 100¢ |
| KXRAINBOSM-26NOV-1 | 1 in | 2026-11-06T17:30 | 100¢ | 2026-11-06T21:30 | 100¢ | 100¢ |
| KXRAINPVDM-26NOV-1 | 1 in | 2026-11-07T02:00 | 100¢ | 2026-11-07T05:45 | 100¢ | 100¢ |
| KXRAINBOSM-26NOV-2 | 2 in | 2026-11-07T02:30 | 100¢ | 2026-11-07T05:45 | 100¢ | 100¢ |
| KXRAINPVDM-26NOV-2 | 2 in | 2026-11-07T05:30 | 100¢ | 2026-11-07T05:45 | 100¢ | 100¢ |
| KXRAINBOSM-26NOV-3 | 3 in | 2026-11-07T13:30 | 100¢ | 2026-11-07T21:45 | 100¢ | 100¢ |
| KXRAINPVDM-26NOV-3 | 3 in | 2026-11-07T14:30 | 100¢ | 2026-11-07T21:45 | 100¢ | 100¢ |
| KXRAINAUSM-26NOV-1 | 1 in | 2026-11-08T13:45 | 100¢ | 2026-11-08T13:45 | 100¢ | 100¢ |
| KXRAINSEAM-26NOV-1 | 1 in | 2026-11-10T02:45 | 100¢ | 2026-11-10T09:30 | 100¢ | 100¢ |
| KXRAINHOUM-26NOV-1 | 1 in | 2026-11-10T22:45 | 100¢ | 2026-11-10T22:45 | 100¢ | 100¢ |
| KXRAINSEAM-26NOV-2 | 2 in | 2026-11-11T00:45 | 100¢ | 2026-11-11T02:45 | 100¢ | 100¢ |

0 of 14 passed thresholds were ever offered at 95¢ or less after we saw them pass.

## Paper trading: day-before limit orders

Orders the bot would rest 1¢ inside the spread about 36 hours before close (no money). Filled = a real trade printed at our price or better.

Placed 338 · open 85 · graded 253 · filled 196 (77%)

- All fills (1 contract each): 196 contracts, 79 won, P&L $-3.01 on $82.01 (-3.7%)
- Certain fills only (traded through our price): 193 contracts, 76 won, P&L $-4.14 on $80.14 (-5.2%)

| Day | Fills | Won | P&L |
|---|---|---|---|
| 2026-11-06 | 24 | 11 | $-0.10 |
| 2026-11-07 | 42 | 20 | $+2.59 |
| 2026-11-08 | 31 | 10 | $-1.78 |
| 2026-11-09 | 26 | 10 | $-2.02 |
| 2026-11-10 | 39 | 14 | $-1.93 |
| 2026-11-11 | 34 | 14 | $+0.23 |

## Paper test: late-day weather orders

No money. From midday on, the rule sells the brackets that still need the temperature to pass today's low/high so far (kind need, buys NO) and buys the bracket that holds it (kind holds). Filled = a real trade printed at our price or better. Backtest for lows, need: +3.7c per contract in Sep-Nov 2026. The lows, need orders also trade real money (strategy weather_late).

Written 127 · open 64 · graded 63 · filled 51

| Group | Fills | Won | P&L | Per contract (± 1 se) |
|---|---|---|---|---|
| lows, need | 9 | 7 | $+0.87 on $6.13 | +9.7c ±0.0 |
| lows, holds | 2 | 2 | $+0.84 on $1.16 | +42.0c ±0.0 |
| highs, need | 27 | 15 | $+1.36 on $13.64 | +5.0c ±0.0 |
| highs, holds | 13 | 9 | $+2.01 on $6.99 | +15.5c ±0.0 |

## Automatic trading

Mode: **live** · bankroll $50.54 · caps: 10 contracts/order, $37.91 open (75%), daily stop $-5.05 (10%), total stop $-12.63 (25%)

Scale-up kit: **OFF** (switches on after the 500-contract reviews + Nov 17 checkpoint). At this bankroll it would use the 'test money' tier.

| Strategy | Mode | Orders | Open | Filled | Settled | P&L |
|---|---|---|---|---|---|---|
| favorites | live | 13 | 2 | 12 | 10 | $+1.82 |
| favorites | paper | 4 | 0 | 4 | 4 | $+0.28 |
| macro_fade | live | 4 | 1 | 3 | 2 | $-0.74 |
| macro_fade | paper | 4 | 2 | 4 | 2 | $-0.71 |
| macro_wide | live | 2 | 2 | 2 | 0 | $+0.00 |
| mentions | live | 11 | 4 | 11 | 7 | $+1.20 |
| mentions | paper | 8 | 1 | 8 | 7 | $+1.02 |
| mentions_wide | paper | 17 | 17 | 13 | 0 | $+0.00 |
| weather | live | 80 | 12 | 61 | 52 | $-3.39 |
| weather | paper | 21 | 20 | 12 | 1 | $+0.60 |
| weather_late | live | 23 | 14 | 22 | 8 | $+0.31 |

## Strategy grades (allocator)

Mode: **shadow (reported only, sizing unchanged)** · last re-graded 2026-11-07 · hurdle 3c profit per $1 risked

| Strategy | Settled events (real/paper) | Risked | Profit | Per $1 | Trust | Grade | Size x |
|---|---|---|---|---|---|---|---|
| favorites | 4/0 | $29.74 | $+1.82 | +6.1c | 5% | +3.2c | 1.02 (target 1.05) |
| favorites_wide | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| locks | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| m15_btc | 325/0 | $443.15 | $+5.85 | +1.3c | 61% | +2.0c | 1.00 (target 0.66) |
| m15_silver | 229/0 | $340.75 | $+3.83 | +1.1c | 57% | +1.9c | 1.00 (target 0.64) |
| macro_fade | 2/0 | $1.74 | $-0.74 | -42.5c | 2% | +2.3c | 1.00 (target 0.75) |
| macro_wide | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| mentions | 3/0 | $18.80 | $+1.20 | +6.4c | 2% | +3.1c | 1.00 (target 1.02) |
| mentions_wide | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| weather | 31/0 | $17.80 | $-3.39 | -19.0c | 2% | +2.6c | 1.01 (target 0.86) |
| weather_late | 4/0 | $5.28 | $+0.31 | +5.9c | 3% | +3.1c | 1.00 (target 1.02) |

Trust = how much the grade follows the strategy's own record instead of the hurdle. It grows with settled events and grows slower for longshots (noisier per dollar).

## New Kalshi markets (launch watcher)

Watching 16341 series since 2026-11-05; 339 new series have opened markets, 283 waiting for their first market.


| Opened | Series | Category | Freq | Markets | Spread | Test |
|---|---|---|---|---|---|---|
| 2026-11-13T00:00 | Atlanta snow-day count (KXSNOWDAYSATL) | Climate and Weather | custom | 1 |  | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. |
| 2026-11-12T02:30 | Great Falls, MT Rainfall (KXRAINGTF) | Climate and Weather | custom | 10 |  | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. |
| 2026-11-11T03:30 | Lowest temperature over a period (KXLOWTPERIOD) | Climate and Weather | custom | 25 | 99c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (99c), so makers haven't arrived yet. |
| 2026-11-11T03:00 | Snowfall by city (KXSNOWCITIES) | Climate and Weather | custom | 5 | 97c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (97c), so makers haven't arrived yet. |
| 2026-11-10T05:00 | Biggest earthquake in a given month (KXBIGGESTQUAKEMONTH) | Climate and Weather | one_off | 9 | 100c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (100c), so makers haven't arrived yet. |

## Bot health

Scans per day: 11-06: 291, 11-07: 293, 11-08: 293, 11-09: 288, 11-10: 292, 11-11: 286, 11-12: 256

