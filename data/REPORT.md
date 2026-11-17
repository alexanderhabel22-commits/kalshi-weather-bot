# Weather bot report

Updated Mon Nov 16, 09:00 PM · bankroll $34.30 · risk level normal · EDGE alerts off

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
| 2026-11-10 | 71 | 0 | 71 |
| 2026-11-11 | 71 | 0 | 71 |
| 2026-11-12 | 107 | 0 | 107 |
| 2026-11-13 | 126 | 0 | 126 |
| 2026-11-14 | 105 | 0 | 105 |
| 2026-11-15 | 104 | 0 | 104 |
| 2026-11-16 | 118 | 0 | 118 |

Over these days, 0% of decided brackets were ever cheap enough to be worth buying. The rest were already priced in by other traders.

## Your actual trades (from `data/my_trades.csv`)

None logged. Add a line per trade you place: `ticker,side,contracts,price,note` (price in dollars, e.g. `0.94`). Or tell Claude and it will add them.

## Silent near-lock test (late-day calls the model rates 95%+, not yet certain)

**Record: 58-11**, -$12.51 if you had taken them (-8.4%). Pending: 7.
Accuracy (Brier, lower is better): model 0.151 vs market 0.134 → **market ahead**.

| Model said | Calls | Market said (avg) | Actually won |
|---|---|---|---|
| 90-99% | 69 | 88% | 84% |

Verdict: keep near-lock alerts silent. Needs 50+ settled calls, a profit, and the model beating the market.

## Forecast accuracy (every city-day the bot priced)

| Lead time | City-days | Model bias | Model avg miss | 80% range held | NWS avg miss |
|---|---|---|---|---|---|
| More than 24h out | 1282 | +0.7°F | 1.9°F | 95% (target 80%) | 2.0°F |
| 12–24h out | 751 | +0.8°F | 1.5°F | 91% (target 80%) | 1.8°F |
| 6–12h out | 380 | +0.5°F | 0.8°F | 93% (target 80%) | 1.6°F |
| Last 6h | 294 | +0.2°F | 0.5°F | 93% (target 80%) | 1.4°F |

Bias is actual minus model median (positive = model too cold). If the 80% range holds far more than 80% of the time, the model is too unsure; far less, too sure.

Biggest city biases (12h+ out): San Antonio low -2.8°F (n=16); Atlanta high +2.7°F (n=57); Seattle low -2.6°F (n=16); Washington DC low +2.5°F (n=13); Dallas high +2.3°F (n=69).

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

Placed 483 · open 69 · graded 414 · filled 330 (79%)

- All fills (1 contract each): 330 contracts, 139 won, P&L $-4.82 on $143.82 (-3.4%)
- Certain fills only (traded through our price): 325 contracts, 134 won, P&L $-6.25 on $140.25 (-4.5%)

| Day | Fills | Won | P&L |
|---|---|---|---|
| 2026-11-06 | 24 | 11 | $-0.10 |
| 2026-11-07 | 42 | 20 | $+2.59 |
| 2026-11-08 | 31 | 10 | $-1.78 |
| 2026-11-09 | 26 | 10 | $-2.02 |
| 2026-11-10 | 39 | 14 | $-1.93 |
| 2026-11-11 | 34 | 14 | $+0.23 |
| 2026-11-12 | 39 | 23 | $+3.33 |
| 2026-11-13 | 36 | 18 | $+2.29 |
| 2026-11-14 | 29 | 9 | $-3.95 |
| 2026-11-15 | 30 | 10 | $-3.48 |

## Paper test: late-day weather orders

No money. From midday on, the rule sells the brackets that still need the temperature to pass today's low/high so far (kind need, buys NO) and buys the bracket that holds it (kind holds). Filled = a real trade printed at our price or better. Backtest for lows, need: +3.7c per contract in Sep-Nov 2026. The lows, need orders also trade real money (strategy weather_late).

Written 481 · open 90 · graded 391 · filled 331

| Group | Fills | Won | P&L | Per contract (± 1 se) |
|---|---|---|---|---|
| lows, need | 89 | 68 | $+2.07 on $65.93 | +2.3c ±1.9 |
| lows, holds | 43 | 28 | $+0.72 on $27.28 | +1.7c ±5.4 |
| highs, need | 136 | 69 | $-1.90 on $70.90 | -1.4c ±2.0 |
| highs, holds | 63 | 39 | $+1.43 on $37.57 | +2.3c ±6.4 |

## Automatic trading

Mode: **live** · bankroll $34.30 · caps: 10 contracts/order, $34.30 open (100%), daily stop $-3.43 (10%), total stop $-8.57 (25%)

Scale-up kit: **OFF** (switches on after the 500-contract reviews + Nov 17 checkpoint). At this bankroll it would use the 'test money' tier.

| Strategy | Mode | Orders | Open | Filled | Settled | P&L |
|---|---|---|---|---|---|---|
| ai_fade | live | 1 | 1 | 1 | 0 | $+0.00 |
| ai_fade | paper | 1 | 1 | 1 | 0 | $+0.00 |
| favorites | live | 18 | 0 | 16 | 16 | $+3.02 |
| favorites | paper | 4 | 0 | 4 | 4 | $+0.28 |
| macro_fade | live | 4 | 1 | 3 | 2 | $-0.74 |
| macro_fade | paper | 4 | 2 | 4 | 2 | $-0.71 |
| macro_wide | live | 7 | 5 | 2 | 2 | $-0.70 |
| macro_wide | paper | 3 | 3 | 1 | 0 | $+0.00 |
| mentions | live | 11 | 4 | 11 | 7 | $+1.20 |
| mentions | paper | 11 | 4 | 11 | 7 | $+1.02 |
| mentions_wide | paper | 18 | 16 | 15 | 2 | $-1.54 |
| ndx_range | live | 9 | 0 | 0 | 0 | $+0.00 |
| rain_evening | live | 4 | 0 | 4 | 4 | $-0.60 |
| spx_range | live | 9 | 0 | 0 | 0 | $+0.00 |
| weather | live | 80 | 0 | 61 | 61 | $-3.43 |
| weather | paper | 66 | 22 | 57 | 40 | $+0.07 |
| weather_late | live | 142 | 23 | 123 | 104 | $+0.21 |
| weather_late | paper | 21 | 21 | 17 | 0 | $+0.00 |

## Strategy grades (allocator)

Mode: **shadow (reported only, sizing unchanged)** · last re-graded 2026-11-14 · hurdle 3c profit per $1 risked

| Strategy | Settled events (real/paper) | Risked | Profit | Per $1 | Trust | Grade | Size x |
|---|---|---|---|---|---|---|---|
| ai_fade | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| favorites | 6/0 | $52.26 | $+3.02 | +5.8c | 7% | +3.2c | 1.07 (target 1.07) |
| favorites_wide | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| locks | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| m15_btc | 329/0 | $446.37 | $+2.63 | +0.6c | 61% | +1.5c | 0.75 (target 0.51) |
| macro_fade | 2/0 | $1.74 | $-0.74 | -42.5c | 2% | +2.3c | 0.75 (target 0.75) |
| macro_wide | 1/0 | $1.67 | $-0.70 | -41.9c | 1% | +2.7c | 1.00 (target 0.89) |
| mentions | 3/0 | $18.80 | $+1.20 | +6.4c | 2% | +3.1c | 1.02 (target 1.02) |
| mentions_wide | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| ndx_range | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| rain_evening | 3/0 | $3.60 | $-0.60 | -16.7c | 3% | +2.4c | 0.75 (target 0.80) |
| spx_range | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| weather | 37/0 | $20.84 | $-3.43 | -16.5c | 2% | +2.5c | 0.85 (target 0.85) |
| weather_late | 66/0 | $72.38 | $+0.21 | +0.3c | 13% | +2.6c | 1.00 (target 0.88) |

Trust = how much the grade follows the strategy's own record instead of the hurdle. It grows with settled events and grows slower for longshots (noisier per dollar).

## New Kalshi markets (launch watcher)

Watching 16484 series since 2026-11-05; 400 new series have opened markets, 320 waiting for their first market.


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

Scans per day: 11-10: 292, 11-11: 286, 11-12: 291, 11-13: 292, 11-14: 293, 11-15: 292, 11-16: 255

