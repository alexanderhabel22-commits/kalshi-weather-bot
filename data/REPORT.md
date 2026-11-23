# Weather bot report

Updated Sun Nov 22, 09:00 PM · bankroll $53.55 · risk level normal · EDGE alerts off

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
| 2026-11-16 | 119 | 0 | 119 |
| 2026-11-17 | 120 | 0 | 120 |
| 2026-11-18 | 117 | 0 | 117 |
| 2026-11-19 | 120 | 0 | 120 |
| 2026-11-20 | 122 | 0 | 122 |
| 2026-11-21 | 109 | 0 | 109 |
| 2026-11-22 | 95 | 0 | 95 |

Over these days, 0% of decided brackets were ever cheap enough to be worth buying. The rest were already priced in by other traders.

## Your actual trades (from `data/my_trades.csv`)

None logged. Add a line per trade you place: `ticker,side,contracts,price,note` (price in dollars, e.g. `0.94`). Or tell Claude and it will add them.

## Silent near-lock test (late-day calls the model rates 95%+, not yet certain)

**Record: 80-15**, -$15.62 if you had taken them (-7.9%). Pending: 9.
Accuracy (Brier, lower is better): model 0.148 vs market 0.128 → **market ahead**.

| Model said | Calls | Market said (avg) | Actually won |
|---|---|---|---|
| 90-99% | 95 | 87% | 84% |

Verdict: keep near-lock alerts silent. Needs 50+ settled calls, a profit, and the model beating the market.

## Forecast accuracy (every city-day the bot priced)

| Lead time | City-days | Model bias | Model avg miss | 80% range held | NWS avg miss |
|---|---|---|---|---|---|
| More than 24h out | 2285 | +0.6°F | 2.0°F | 95% (target 80%) | 1.9°F |
| 12–24h out | 1301 | +0.6°F | 1.5°F | 91% (target 80%) | 1.7°F |
| 6–12h out | 655 | +0.3°F | 0.8°F | 94% (target 80%) | 1.6°F |
| Last 6h | 473 | +0.1°F | 0.5°F | 92% (target 80%) | 1.4°F |

Bias is actual minus model median (positive = model too cold). If the 80% range holds far more than 80% of the time, the model is too unsure; far less, too sure.

Biggest city biases (12h+ out): Newark high +3.3°F (n=43); Trenton high +2.7°F (n=43); Denver low -2.6°F (n=105); Dallas high +2.5°F (n=105); Atlanta high +2.3°F (n=87).

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

Placed 640 · open 49 · graded 591 · filled 488 (82%)

- All fills (1 contract each): 488 contracts, 207 won, P&L $-16.07 on $223.07 (-7.2%)
- Certain fills only (traded through our price): 479 contracts, 198 won, P&L $-18.08 on $216.08 (-8.4%)

| Day | Fills | Won | P&L |
|---|---|---|---|
| 2026-11-12 | 39 | 23 | $+3.33 |
| 2026-11-13 | 36 | 18 | $+2.29 |
| 2026-11-14 | 29 | 9 | $-3.95 |
| 2026-11-15 | 30 | 10 | $-3.48 |
| 2026-11-16 | 37 | 18 | $-2.50 |
| 2026-11-17 | 26 | 12 | $-0.49 |
| 2026-11-18 | 30 | 10 | $-3.75 |
| 2026-11-19 | 18 | 7 | $-1.88 |
| 2026-11-20 | 24 | 12 | $-0.34 |
| 2026-11-21 | 23 | 9 | $-2.29 |

## Paper test: late-day weather orders

No money. From midday on, the rule sells the brackets that still need the temperature to pass today's low/high so far (kind need, buys NO) and buys the bracket that holds it (kind holds). Filled = a real trade printed at our price or better. Backtest for lows, need: +3.7c per contract in Sep-Nov 2026. The lows, need orders also trade real money (strategy weather_late).

Written 909 · open 84 · graded 825 · filled 707

| Group | Fills | Won | P&L | Per contract (± 1 se) |
|---|---|---|---|---|
| lows, need | 148 | 111 | $+3.57 on $107.43 | +2.4c ±1.5 |
| lows, holds | 78 | 50 | $+1.40 on $48.60 | +1.8c ±3.8 |
| highs, need | 344 | 169 | $-7.87 on $176.87 | -2.3c ±1.1 |
| highs, holds | 137 | 78 | $+4.26 on $73.74 | +3.1c ±4.0 |

## Automatic trading

Mode: **live** · bankroll $53.55 · caps: 10 contracts/order, $53.55 open (100%), daily stop $-5.36 (10%), total stop $-13.39 (25%)

Scale-up kit: **OFF** (switches on after the 500-contract reviews + Nov 17 checkpoint). At this bankroll it would use the 'test money' tier.

| Strategy | Mode | Orders | Open | Filled | Settled | P&L |
|---|---|---|---|---|---|---|
| ai_fade | live | 2 | 0 | 2 | 2 | $+0.73 |
| ai_fade | paper | 1 | 0 | 1 | 1 | $+0.32 |
| fade_leads | live | 1 | 1 | 1 | 0 | $+0.00 |
| fade_leads_paper | paper | 1 | 1 | 1 | 0 | $+0.00 |
| favorites | live | 19 | 0 | 17 | 17 | $+3.37 |
| favorites | paper | 4 | 0 | 4 | 4 | $+0.28 |
| fx_paper | paper | 37 | 37 | 37 | 0 | $+0.00 |
| macro_fade | live | 9 | 6 | 5 | 2 | $-0.74 |
| macro_fade | paper | 4 | 2 | 4 | 2 | $-0.71 |
| macro_wide | live | 8 | 3 | 3 | 3 | $-0.22 |
| macro_wide | paper | 3 | 1 | 2 | 2 | $+0.23 |
| mentions | live | 11 | 0 | 11 | 11 | $+3.96 |
| mentions | paper | 11 | 0 | 11 | 11 | $+0.90 |
| mentions_wide | paper | 18 | 1 | 16 | 16 | $+1.11 |
| ndx_range | live | 41 | 5 | 0 | 0 | $+0.00 |
| quake_no | live | 2 | 0 | 2 | 2 | $-0.74 |
| quake_no_paper | paper | 3 | 0 | 3 | 3 | $-1.24 |
| rain_evening | live | 6 | 0 | 6 | 6 | $-1.27 |
| ship_paper | paper | 49 | 49 | 49 | 0 | $+0.00 |
| spx_range | live | 33 | 0 | 0 | 0 | $+0.00 |
| weather | live | 80 | 0 | 61 | 61 | $-3.43 |
| weather | paper | 103 | 3 | 94 | 92 | $-1.70 |
| weather_late | live | 263 | 19 | 222 | 205 | $+4.82 |
| weather_late | paper | 21 | 0 | 17 | 17 | $+0.33 |

## Strategy grades (allocator)

Mode: **APPLIED to live sizing** · last re-graded 2026-11-21 · hurdle 3c profit per $1 risked

| Strategy | Settled events (real/paper) | Risked | Profit | Per $1 | Trust | Grade | Size x |
|---|---|---|---|---|---|---|---|
| ai_fade | 2/0 | $8.07 | $+0.73 | +9.0c | 2% | +3.1c | 1.04 (target 1.04) |
| fade_leads | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| fade_leads_paper | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| favorites | 7/0 | $60.81 | $+3.37 | +5.5c | 9% | +3.2c | 1.07 (target 1.08) |
| favorites_mid | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| favorites_wide | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| fx_paper | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| locks | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| m15_btc | 329/0 | $446.37 | $+2.63 | +0.6c | 61% | +1.5c | 0.51 (target 0.51) |
| macro_fade | 2/0 | $1.74 | $-0.74 | -42.5c | 2% | +2.3c | 0.75 (target 0.75) |
| macro_wide | 2/0 | $3.19 | $-0.22 | -6.9c | 1% | +2.9c | 0.97 (target 0.97) |
| mentions | 5/0 | $32.04 | $+3.96 | +12.4c | 3% | +3.3c | 1.09 (target 1.09) |
| mentions_wide | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| ndx_range | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| quake_no | 1/0 | $1.74 | $-0.74 | -42.5c | 1% | +2.6c | 0.76 (target 0.88) |
| quake_no_guard_paper | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| quake_no_paper | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| rain_evening | 4/0 | $5.27 | $-1.27 | -24.1c | 3% | +2.2c | 0.73 (target 0.73) |
| ship_paper | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| spx_range | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| weather | 37/0 | $20.84 | $-3.43 | -16.5c | 2% | +2.5c | 0.85 (target 0.85) |
| weather_late | 142/0 | $180.77 | $+4.82 | +2.7c | 19% | +2.9c | 0.98 (target 0.98) |

Trust = how much the grade follows the strategy's own record instead of the hurdle. It grows with settled events and grows slower for longshots (noisier per dollar).

## New Kalshi markets (launch watcher)

Watching 16775 series since 2026-11-05; 400 new series have opened markets, 479 waiting for their first market.


| Opened | Series | Category | Freq | Markets | Spread | Test |
|---|---|---|---|---|---|---|
| 2026-11-21T01:30 | Windspeed in NYC (KXWINDSPEEDNYC) | Climate and Weather | custom | 4 | 99c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (99c), so makers haven't arrived yet. |
| 2026-11-18T22:30 | Rainstorm in Los Angeles (KXRAINSLAX) | Climate and Weather | custom | 6 | 99c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (99c), so makers haven't arrived yet. |
| 2026-11-18T21:30 | Will La Niña conditions be declared? (KXLANINA) | Climate and Weather | custom | 1 | 98c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (98c), so makers haven't arrived yet. |
| 2026-11-18T19:30 | Rain during Coachella? (KXRAINCOACHELLA) | Climate and Weather | custom | 2 | 98c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (98c), so makers haven't arrived yet. |
| 2026-11-18T00:03 | Saskatchewan precipitation (KXSKPRECPERIOD) | Climate and Weather | custom | 1 |  | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. |
| 2026-11-13T20:00 | Snow on Thanksgiving (KXSNOWTHANKSGIVING) | Climate and Weather | annual | 21 |  | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. |
| 2026-11-13T18:30 | Rain on Thanksgiving (KXRAINTHANKSGIVING) | Climate and Weather | custom | 48 | 20c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (20c), so makers haven't arrived yet. |
| 2026-11-13T15:00 | Cumulative snow days in cities (KXSNOWDAYS) | Climate and Weather | custom | 22 | 99c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (99c), so makers haven't arrived yet. |
| 2026-11-13T00:00 | Atlanta snow-day count (KXSNOWDAYSATL) | Climate and Weather | custom | 1 |  | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. |
| 2026-11-12T02:30 | Great Falls, MT Rainfall (KXRAINGTF) | Climate and Weather | custom | 10 |  | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. |
| 2026-11-11T03:30 | Lowest temperature over a period (KXLOWTPERIOD) | Climate and Weather | custom | 25 | 99c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (99c), so makers haven't arrived yet. |
| 2026-11-11T03:00 | Snowfall by city (KXSNOWCITIES) | Climate and Weather | custom | 5 | 97c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (97c), so makers haven't arrived yet. |
| 2026-11-10T05:00 | Biggest earthquake in a given month (KXBIGGESTQUAKEMONTH) | Climate and Weather | one_off | 9 | 100c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (100c), so makers haven't arrived yet. |

## Bot health

Scans per day: 11-16: 290, 11-17: 290, 11-18: 292, 11-19: 291, 11-20: 292, 11-21: 292, 11-22: 256

- 2026-11-17: could not reach NWS observations
