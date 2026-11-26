# Weather bot report

Updated Wed Nov 25, 09:00 PM · bankroll $55.38 · risk level normal · EDGE alerts off

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
| 2026-11-19 | 120 | 0 | 120 |
| 2026-11-20 | 122 | 0 | 122 |
| 2026-11-21 | 109 | 0 | 109 |
| 2026-11-22 | 103 | 0 | 103 |
| 2026-11-23 | 109 | 0 | 109 |
| 2026-11-24 | 106 | 0 | 106 |
| 2026-11-25 | 96 | 0 | 96 |

Over these days, 0% of decided brackets were ever cheap enough to be worth buying. The rest were already priced in by other traders.

## Your actual trades (from `data/my_trades.csv`)

None logged. Add a line per trade you place: `ticker,side,contracts,price,note` (price in dollars, e.g. `0.94`). Or tell Claude and it will add them.

## Silent near-lock test (late-day calls the model rates 95%+, not yet certain)

**Record: 104-19**, -$17.85 if you had taken them (-6.9%). Pending: 3.
Accuracy (Brier, lower is better): model 0.145 vs market 0.128 → **market ahead**.

| Model said | Calls | Market said (avg) | Actually won |
|---|---|---|---|
| 90-99% | 123 | 88% | 85% |

Verdict: keep near-lock alerts silent. Needs 50+ settled calls, a profit, and the model beating the market.

## Forecast accuracy (every city-day the bot priced)

| Lead time | City-days | Model bias | Model avg miss | 80% range held | NWS avg miss |
|---|---|---|---|---|---|
| More than 24h out | 2789 | +0.5°F | 2.0°F | 94% (target 80%) | 2.0°F |
| 12–24h out | 1577 | +0.6°F | 1.5°F | 91% (target 80%) | 1.9°F |
| 6–12h out | 793 | +0.4°F | 0.7°F | 94% (target 80%) | 1.7°F |
| Last 6h | 563 | +0.1°F | 0.5°F | 93% (target 80%) | 1.5°F |

Bias is actual minus model median (positive = model too cold). If the 80% range holds far more than 80% of the time, the model is too unsure; far less, too sure.

Biggest city biases (12h+ out): Denver low -2.9°F (n=123); Newark high +2.8°F (n=58); Trenton high +2.3°F (n=58); Dallas high +2.2°F (n=123); San Antonio low -2.1°F (n=70).

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

Placed 716 · open 59 · graded 657 · filled 546 (83%)

- All fills (1 contract each): 546 contracts, 232 won, P&L $-17.35 on $249.35 (-7.0%)
- Certain fills only (traded through our price): 537 contracts, 223 won, P&L $-19.36 on $242.36 (-8.0%)

| Day | Fills | Won | P&L |
|---|---|---|---|
| 2026-11-15 | 30 | 10 | $-3.48 |
| 2026-11-16 | 37 | 18 | $-2.50 |
| 2026-11-17 | 26 | 12 | $-0.49 |
| 2026-11-18 | 30 | 10 | $-3.75 |
| 2026-11-19 | 18 | 7 | $-1.88 |
| 2026-11-20 | 24 | 12 | $-0.34 |
| 2026-11-21 | 23 | 9 | $-2.29 |
| 2026-11-22 | 28 | 15 | $+1.77 |
| 2026-11-23 | 14 | 3 | $-2.49 |
| 2026-11-24 | 16 | 7 | $-0.56 |

## Paper test: late-day weather orders

No money. From midday on, the rule sells the brackets that still need the temperature to pass today's low/high so far (kind need, buys NO) and buys the bracket that holds it (kind holds). Filled = a real trade printed at our price or better. Backtest for lows, need: +3.7c per contract in Sep-Nov 2026. The lows, need orders also trade real money (strategy weather_late).

Written 1140 · open 60 · graded 1080 · filled 931

| Group | Fills | Won | P&L | Per contract (± 1 se) |
|---|---|---|---|---|
| lows, need | 223 | 162 | $+4.35 on $157.65 | +2.0c ±1.4 |
| lows, holds | 100 | 60 | $+3.00 on $57.00 | +3.0c ±3.8 |
| highs, need | 432 | 213 | $-11.56 on $224.56 | -2.7c ±0.9 |
| highs, holds | 176 | 98 | $+2.90 on $95.10 | +1.6c ±3.2 |

## Automatic trading

Mode: **live** · bankroll $55.38 · caps: 10 contracts/order, $55.38 open (100%), daily stop $-5.54 (10%), total stop $-13.85 (25%)

Scale-up kit: **OFF** (switches on after the 500-contract reviews + Nov 17 checkpoint). At this bankroll it would use the 'test money' tier.

| Strategy | Mode | Orders | Open | Filled | Settled | P&L |
|---|---|---|---|---|---|---|
| ai_fade | live | 2 | 0 | 2 | 2 | $+0.73 |
| ai_fade | paper | 1 | 0 | 1 | 1 | $+0.32 |
| fade_leads | live | 1 | 1 | 1 | 0 | $+0.00 |
| fade_leads_paper | paper | 1 | 1 | 1 | 0 | $+0.00 |
| favorites | live | 20 | 1 | 17 | 17 | $+3.37 |
| favorites | paper | 4 | 0 | 4 | 4 | $+0.28 |
| fx_paper | paper | 81 | 71 | 81 | 10 | $+0.36 |
| macro_fade | live | 10 | 6 | 6 | 3 | $-0.63 |
| macro_fade | paper | 4 | 0 | 4 | 4 | $-0.60 |
| macro_wide | live | 14 | 3 | 5 | 4 | $-0.09 |
| macro_wide | paper | 3 | 0 | 2 | 2 | $+0.23 |
| mentions | live | 11 | 0 | 11 | 11 | $+3.96 |
| mentions | paper | 11 | 0 | 11 | 11 | $+0.90 |
| mentions_wide | paper | 18 | 1 | 16 | 16 | $+1.11 |
| ndx_range | live | 54 | 0 | 0 | 0 | $+0.00 |
| quake_ladder | live | 2 | 0 | 2 | 2 | $+0.30 |
| quake_no | live | 4 | 0 | 4 | 4 | $-0.46 |
| quake_no_paper | paper | 4 | 0 | 4 | 4 | $-1.14 |
| rain_evening | live | 8 | 0 | 8 | 8 | $-0.89 |
| ship_paper | paper | 54 | 28 | 54 | 26 | $+0.53 |
| spx_range | live | 49 | 0 | 0 | 0 | $+0.00 |
| weather | live | 80 | 0 | 61 | 61 | $-3.43 |
| weather | paper | 110 | 5 | 101 | 96 | $-1.89 |
| weather_late | live | 304 | 13 | 259 | 248 | $-3.00 |
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
| gas_fav_paper | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| locks | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| m15_natgas | 184/0 | $154.28 | $+5.72 | +3.7c | 54% | +3.4c | 1.00 (target 1.13) |
| macro_fade | 3/0 | $2.63 | $-0.63 | -23.9c | 3% | +2.3c | 0.75 (target 0.77) |
| macro_wide | 3/0 | $5.01 | $-0.09 | -1.8c | 2% | +2.9c | 0.97 (target 0.97) |
| mentions | 5/0 | $32.04 | $+3.96 | +12.4c | 3% | +3.3c | 1.09 (target 1.09) |
| mentions_wide | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| ndx_range | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| quake_ladder | 1/0 | $1.70 | $+0.30 | +17.6c | 1% | +3.1c | 1.00 (target 1.03) |
| quake_ladder_paper | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| quake_no | 3/0 | $4.46 | $-0.46 | -10.3c | 2% | +2.7c | 0.76 (target 0.89) |
| quake_no_guard_paper | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| quake_no_paper | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| rain_evening | 5/0 | $6.89 | $-0.89 | -12.9c | 3% | +2.5c | 0.73 (target 0.83) |
| ship_paper | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| spx_range | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| weather | 37/0 | $20.84 | $-3.43 | -16.5c | 2% | +2.5c | 0.85 (target 0.85) |
| weather_late | 172/0 | $232.59 | $-3.00 | -1.3c | 21% | +2.1c | 0.98 (target 0.69) |

Trust = how much the grade follows the strategy's own record instead of the hurdle. It grows with settled events and grows slower for longshots (noisier per dollar).

## New Kalshi markets (launch watcher)

Watching 17132 series since 2026-11-05; 400 new series have opened markets, 799 waiting for their first market.


| Opened | Series | Category | Freq | Markets | Spread | Test |
|---|---|---|---|---|---|---|
| 2026-11-26T01:00 | Snow on Christmas (KXSNOWCHRISTMAS) | Climate and Weather | annual | 23 | 99c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (99c), so makers haven't arrived yet. |
| 2026-11-26T00:30 | Rain on Christmas (KXRAINCHRISTMAS) | Climate and Weather | custom | 51 |  | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. |
| 2026-11-24T17:30 | Macy’s parade weather postponement (KXMACYSPARADEWX) | Climate and Weather | annual | 1 | 9c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (9c), so makers haven't arrived yet. |
| 2026-11-23T23:00 | Rainstorm in San Francisco (KXRAINSSFO) | Climate and Weather | custom | 8 | 99c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (99c), so makers haven't arrived yet. |
| 2026-11-23T23:00 | Texas Category 3+ hurricane landfall (KXHURRLANDTX3) | Climate and Weather | custom | 1 | 10c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (10c), so makers haven't arrived yet. |
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

## Bot health

Scans per day: 11-19: 291, 11-20: 292, 11-21: 292, 11-22: 292, 11-23: 290, 11-24: 291, 11-25: 248

- 2026-11-17: could not reach NWS observations
