# Weather bot report

Updated Fri Nov 13, 09:00 PM · bankroll $59.89 · risk level normal · EDGE alerts off

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
| 2026-11-07 | 84 | 0 | 84 |
| 2026-11-08 | 74 | 0 | 74 |
| 2026-11-09 | 63 | 0 | 63 |
| 2026-11-10 | 71 | 0 | 71 |
| 2026-11-11 | 71 | 0 | 71 |
| 2026-11-12 | 107 | 0 | 107 |
| 2026-11-13 | 126 | 0 | 126 |

Over these days, 0% of decided brackets were ever cheap enough to be worth buying. The rest were already priced in by other traders.

## Your actual trades (from `data/my_trades.csv`)

None logged. Add a line per trade you place: `ticker,side,contracts,price,note` (price in dollars, e.g. `0.94`). Or tell Claude and it will add them.

## Silent near-lock test (late-day calls the model rates 95%+, not yet certain)

**Record: 45-8**, -$7.54 if you had taken them (-7.1%). Pending: 3.
Accuracy (Brier, lower is better): model 0.145 vs market 0.128 → **market ahead**.

| Model said | Calls | Market said (avg) | Actually won |
|---|---|---|---|
| 90-99% | 53 | 88% | 85% |

Verdict: keep near-lock alerts silent. Needs 50+ settled calls, a profit, and the model beating the market.

## Forecast accuracy (every city-day the bot priced)

| Lead time | City-days | Model bias | Model avg miss | 80% range held | NWS avg miss |
|---|---|---|---|---|---|
| More than 24h out | 819 | +1.0°F | 2.0°F | 96% (target 80%) | 2.0°F |
| 12–24h out | 477 | +0.9°F | 1.6°F | 90% (target 80%) | 1.8°F |
| 6–12h out | 243 | +0.5°F | 0.8°F | 91% (target 80%) | 1.6°F |
| Last 6h | 205 | +0.1°F | 0.5°F | 91% (target 80%) | 1.4°F |

Bias is actual minus model median (positive = model too cold). If the 80% range holds far more than 80% of the time, the model is too unsure; far less, too sure.

Biggest city biases (12h+ out): Atlanta high +3.2°F (n=42); Dallas high +2.6°F (n=51); Minneapolis high +2.2°F (n=51); Denver low -2.1°F (n=51); Chicago high +1.8°F (n=51).

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

Placed 377 · open 81 · graded 296 · filled 235 (79%)

- All fills (1 contract each): 235 contracts, 102 won, P&L $+0.32 on $101.68 (+0.3%)
- Certain fills only (traded through our price): 232 contracts, 99 won, P&L $-0.81 on $99.81 (-0.8%)

| Day | Fills | Won | P&L |
|---|---|---|---|
| 2026-11-06 | 24 | 11 | $-0.10 |
| 2026-11-07 | 42 | 20 | $+2.59 |
| 2026-11-08 | 31 | 10 | $-1.78 |
| 2026-11-09 | 26 | 10 | $-2.02 |
| 2026-11-10 | 39 | 14 | $-1.93 |
| 2026-11-11 | 34 | 14 | $+0.23 |
| 2026-11-12 | 39 | 23 | $+3.33 |

## Paper test: late-day weather orders

No money. From midday on, the rule sells the brackets that still need the temperature to pass today's low/high so far (kind need, buys NO) and buys the bracket that holds it (kind holds). Filled = a real trade printed at our price or better. Backtest for lows, need: +3.7c per contract in Sep-Nov 2026. The lows, need orders also trade real money (strategy weather_late).

Written 227 · open 98 · graded 129 · filled 111

| Group | Fills | Won | P&L | Per contract (± 1 se) |
|---|---|---|---|---|
| lows, need | 29 | 23 | $+1.70 on $21.30 | +5.9c ±1.7 |
| lows, holds | 8 | 4 | $-0.60 on $4.60 | -7.5c ±17.5 |
| highs, need | 53 | 29 | $+0.53 on $28.47 | +1.0c ±2.9 |
| highs, holds | 21 | 14 | $+0.89 on $13.11 | +4.2c ±9.8 |

## Automatic trading

Mode: **live** · bankroll $59.89 · caps: 10 contracts/order, $59.89 open (100%), daily stop $-5.99 (10%), total stop $-14.97 (25%)

Scale-up kit: **OFF** (switches on after the 500-contract reviews + Nov 17 checkpoint). At this bankroll it would use the 'test money' tier.

| Strategy | Mode | Orders | Open | Filled | Settled | P&L |
|---|---|---|---|---|---|---|
| favorites | live | 17 | 4 | 15 | 12 | $+2.36 |
| favorites | paper | 4 | 0 | 4 | 4 | $+0.28 |
| macro_fade | live | 4 | 1 | 3 | 2 | $-0.74 |
| macro_fade | paper | 4 | 2 | 4 | 2 | $-0.71 |
| macro_wide | live | 2 | 2 | 2 | 0 | $+0.00 |
| mentions | live | 11 | 4 | 11 | 7 | $+1.20 |
| mentions | paper | 10 | 3 | 10 | 7 | $+1.02 |
| mentions_wide | paper | 17 | 15 | 14 | 2 | $-1.54 |
| rain_evening | live | 1 | 1 | 1 | 0 | $+0.00 |
| weather | live | 80 | 0 | 61 | 61 | $-3.43 |
| weather | paper | 29 | 26 | 23 | 3 | $+0.73 |
| weather_late | live | 58 | 35 | 47 | 22 | $+0.56 |

## Strategy grades (allocator)

Mode: **shadow (reported only, sizing unchanged)** · last re-graded 2026-11-07 · hurdle 3c profit per $1 risked

| Strategy | Settled events (real/paper) | Risked | Profit | Per $1 | Trust | Grade | Size x |
|---|---|---|---|---|---|---|---|
| favorites | 5/0 | $37.06 | $+2.36 | +6.4c | 6% | +3.2c | 1.02 (target 1.07) |
| favorites_wide | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| locks | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| m15_btc | 329/0 | $446.37 | $+2.63 | +0.6c | 61% | +1.5c | 1.00 (target 0.51) |
| macro_fade | 2/0 | $1.74 | $-0.74 | -42.5c | 2% | +2.3c | 1.00 (target 0.75) |
| macro_wide | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| mentions | 3/0 | $18.80 | $+1.20 | +6.4c | 2% | +3.1c | 1.00 (target 1.02) |
| mentions_wide | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| rain_evening | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| spx_range | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| weather | 37/0 | $20.84 | $-3.43 | -16.5c | 2% | +2.5c | 1.01 (target 0.85) |
| weather_late | 13/0 | $13.03 | $+0.56 | +4.3c | 3% | +3.0c | 1.00 (target 1.01) |

Trust = how much the grade follows the strategy's own record instead of the hurdle. It grows with settled events and grows slower for longshots (noisier per dollar).

## New Kalshi markets (launch watcher)

Watching 16441 series since 2026-11-05; 344 new series have opened markets, 378 waiting for their first market.


| Opened | Series | Category | Freq | Markets | Spread | Test |
|---|---|---|---|---|---|---|
| 2026-11-13T20:00 | Snow on Thanksgiving (KXSNOWTHANKSGIVING) | Climate and Weather | annual | 21 |  | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. |
| 2026-11-13T18:30 | Rain on Thanksgiving (KXRAINTHANKSGIVING) | Climate and Weather | custom | 48 | 20c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (20c), so makers haven't arrived yet. |
| 2026-11-13T15:00 | Cumulative snow days in cities (KXSNOWDAYS) | Climate and Weather | custom | 22 | 99c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (99c), so makers haven't arrived yet. |
| 2026-11-13T00:00 | Atlanta snow-day count (KXSNOWDAYSATL) | Climate and Weather | custom | 1 |  | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. |
| 2026-11-12T02:30 | Great Falls, MT Rainfall (KXRAINGTF) | Climate and Weather | custom | 10 |  | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. |
| 2026-11-11T03:30 | Lowest temperature over a period (KXLOWTPERIOD) | Climate and Weather | custom | 25 | 99c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (99c), so makers haven't arrived yet. |
| 2026-11-11T03:00 | Snowfall by city (KXSNOWCITIES) | Climate and Weather | custom | 5 | 97c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (97c), so makers haven't arrived yet. |
| 2026-11-10T05:00 | Biggest earthquake in a given month (KXBIGGESTQUAKEMONTH) | Climate and Weather | one_off | 9 | 100c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (100c), so makers haven't arrived yet. |

## Bot health

Scans per day: 11-07: 293, 11-08: 293, 11-09: 288, 11-10: 292, 11-11: 286, 11-12: 291, 11-13: 257

