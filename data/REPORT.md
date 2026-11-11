# Weather bot report

Updated Tue Nov 10, 09:00 PM · bankroll $56.07 · risk level normal · EDGE alerts off

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
| 2026-11-04 | 62 | 0 | 62 |
| 2026-11-05 | 106 | 1 | 106 |
| 2026-11-06 | 84 | 0 | 84 |
| 2026-11-07 | 84 | 0 | 84 |
| 2026-11-08 | 74 | 0 | 74 |
| 2026-11-09 | 63 | 0 | 63 |
| 2026-11-10 | 69 | 0 | 69 |

Over these days, 0% of decided brackets were ever cheap enough to be worth buying. The rest were already priced in by other traders.

## Your actual trades (from `data/my_trades.csv`)

None logged. Add a line per trade you place: `ticker,side,contracts,price,note` (price in dollars, e.g. `0.94`). Or tell Claude and it will add them.

## Silent near-lock test (late-day calls the model rates 95%+, not yet certain)

**Record: 33-5**, -$4.96 if you had taken them (-6.7%). Pending: 0.
Accuracy (Brier, lower is better): model 0.125 vs market 0.106 → **market ahead**.

| Model said | Calls | Market said (avg) | Actually won |
|---|---|---|---|
| 90-99% | 38 | 88% | 87% |

Verdict: keep near-lock alerts silent. Needs 50+ settled calls, a profit, and the model beating the market.

## Forecast accuracy (every city-day the bot priced)

| Lead time | City-days | Model bias | Model avg miss | 80% range held | NWS avg miss |
|---|---|---|---|---|---|
| More than 24h out | 522 | +1.1°F | 1.8°F | 95% (target 80%) | 1.8°F |
| 12–24h out | 315 | +1.0°F | 1.5°F | 90% (target 80%) | 1.7°F |
| 6–12h out | 162 | +0.5°F | 0.8°F | 92% (target 80%) | 1.6°F |
| Last 6h | 129 | +0.2°F | 0.5°F | 91% (target 80%) | 1.2°F |

Bias is actual minus model median (positive = model too cold). If the 80% range holds far more than 80% of the time, the model is too unsure; far less, too sure.

Biggest city biases (12h+ out): Atlanta high +3.7°F (n=27); Dallas high +2.6°F (n=33); Denver high +2.4°F (n=33); Minneapolis high +2.0°F (n=33); Phoenix high +1.7°F (n=33).

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
| KXRAINSEAM-26NOV-2 | 2 in | 2026-11-11T00:45 | 100¢ | – | – | 100¢ |

0 of 14 passed thresholds were ever offered at 95¢ or less after we saw them pass.

## Paper trading: day-before limit orders

Orders the bot would rest 1¢ inside the spread about 36 hours before close (no money). Filled = a real trade printed at our price or better.

Placed 253 · open 90 · graded 163 · filled 123 (75%)

- All fills (1 contract each): 123 contracts, 51 won, P&L $-1.31 on $52.31 (-2.5%)
- Certain fills only (traded through our price): 121 contracts, 49 won, P&L $-2.28 on $51.28 (-4.4%)

| Day | Fills | Won | P&L |
|---|---|---|---|
| 2026-11-06 | 24 | 11 | $-0.10 |
| 2026-11-07 | 42 | 20 | $+2.59 |
| 2026-11-08 | 31 | 10 | $-1.78 |
| 2026-11-09 | 26 | 10 | $-2.02 |

## Automatic trading

Mode: **live** · bankroll $56.07 · caps: 10 contracts/order, $42.05 open (75%), daily stop $-5.61 (10%), total stop $-14.02 (25%)

Scale-up kit: **OFF** (switches on after the 500-contract reviews + Nov 17 checkpoint). At this bankroll it would use the 'test money' tier.

| Strategy | Mode | Orders | Open | Filled | Settled | P&L |
|---|---|---|---|---|---|---|
| favorites | live | 10 | 3 | 8 | 6 | $+1.14 |
| favorites | paper | 4 | 0 | 4 | 4 | $+0.28 |
| macro_fade | live | 4 | 1 | 3 | 2 | $-0.74 |
| macro_fade | paper | 4 | 2 | 3 | 2 | $-0.71 |
| mentions | live | 10 | 3 | 10 | 7 | $+1.20 |
| mentions | paper | 7 | 0 | 7 | 7 | $+1.02 |
| mentions_wide | paper | 8 | 8 | 0 | 0 | $+0.00 |
| weather | live | 63 | 19 | 46 | 33 | $-1.50 |

## Strategy grades (allocator)

Mode: **shadow (reported only, sizing unchanged)** · last re-graded 2026-11-07 · hurdle 3c profit per $1 risked

| Strategy | Settled events (real/paper) | Risked | Profit | Per $1 | Trust | Grade | Size x |
|---|---|---|---|---|---|---|---|
| favorites | 2/0 | $14.74 | $+1.14 | +7.7c | 2% | +3.1c | 1.02 (target 1.04) |
| locks | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| m15_btc | 267/0 | $352.43 | $+6.57 | +1.9c | 55% | +2.4c | 1.00 (target 0.79) |
| m15_silver | 139/0 | $204.29 | $+12.21 | +6.0c | 43% | +4.3c | 1.00 (target 1.43) |
| macro_fade | 2/0 | $1.74 | $-0.74 | -42.5c | 2% | +2.3c | 1.00 (target 0.75) |
| mentions | 3/0 | $18.80 | $+1.20 | +6.4c | 2% | +3.1c | 1.00 (target 1.02) |
| mentions_wide | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| weather | 21/0 | $10.43 | $-1.50 | -14.4c | 1% | +2.8c | 1.01 (target 0.94) |

Trust = how much the grade follows the strategy's own record instead of the hurdle. It grows with settled events and grows slower for longshots (noisier per dollar).

## New Kalshi markets (launch watcher)

Watching 16077 series since 2026-11-05; 262 new series have opened markets, 96 waiting for their first market.


| Opened | Series | Category | Freq | Markets | Spread | Test |
|---|---|---|---|---|---|---|
| 2026-11-10T05:00 | Biggest earthquake in a given month (KXBIGGESTQUAKEMONTH) | Climate and Weather | one_off | 9 | 100c | Weather: check what it settles on and whether our forecasts/obs cover it; if yes, paper-test it like the day-before maker orders. Spreads are wide (100c), so makers haven't arrived yet. |

## Bot health

Scans per day: 11-04: 171, 11-05: 288, 11-06: 291, 11-07: 293, 11-08: 293, 11-09: 288, 11-10: 257

