# Weather bot report

Updated Sun Nov 08, 09:00 PM · bankroll $55.48 · risk level normal · EDGE alerts off

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
| 2026-11-03 | 55 | 0 | 55 |
| 2026-11-04 | 62 | 0 | 62 |
| 2026-11-05 | 106 | 1 | 106 |
| 2026-11-06 | 84 | 0 | 84 |
| 2026-11-07 | 84 | 0 | 84 |
| 2026-11-08 | 74 | 0 | 74 |

Over these days, 0% of decided brackets were ever cheap enough to be worth buying. The rest were already priced in by other traders.

## Your actual trades (from `data/my_trades.csv`)

None logged. Add a line per trade you place: `ticker,side,contracts,price,note` (price in dollars, e.g. `0.94`). Or tell Claude and it will add them.

## Silent near-lock test (late-day calls the model rates 95%+, not yet certain)

**Record: 26-4**, -$4.05 if you had taken them (-7.5%). Pending: 2.
Accuracy (Brier, lower is better): model 0.128 vs market 0.110 → **market ahead**.

| Model said | Calls | Market said (avg) | Actually won |
|---|---|---|---|
| 90-99% | 30 | 88% | 87% |

Verdict: keep near-lock alerts silent. Needs 50+ settled calls, a profit, and the model beating the market.

## Forecast accuracy (every city-day the bot priced)

| Lead time | City-days | Model bias | Model avg miss | 80% range held | NWS avg miss |
|---|---|---|---|---|---|
| More than 24h out | 324 | +1.0°F | 1.9°F | 94% (target 80%) | 1.9°F |
| 12–24h out | 207 | +1.0°F | 1.6°F | 90% (target 80%) | 1.9°F |
| 6–12h out | 108 | +0.5°F | 0.8°F | 92% (target 80%) | 1.7°F |
| Last 6h | 95 | +0.2°F | 0.5°F | 91% (target 80%) | 1.3°F |

Bias is actual minus model median (positive = model too cold). If the 80% range holds far more than 80% of the time, the model is too unsure; far less, too sure.

Biggest city biases (12h+ out): Atlanta high +3.9°F (n=17); Dallas high +3.7°F (n=21); Denver high +2.9°F (n=21); Houston high +2.2°F (n=21); Austin low -2.0°F (n=21).

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

0 of 11 passed thresholds were ever offered at 95¢ or less after we saw them pass.

## Paper trading: day-before limit orders

Orders the bot would rest 1¢ inside the spread about 36 hours before close (no money). Filled = a real trade printed at our price or better.

Placed 163 · open 80 · graded 83 · filled 66 (79%)

- All fills (1 contract each): 66 contracts, 31 won, P&L $+2.49 on $28.51 (+8.7%)
- Certain fills only (traded through our price): 64 contracts, 29 won, P&L $+1.52 on $27.48 (+5.5%)

| Day | Fills | Won | P&L |
|---|---|---|---|
| 2026-11-06 | 24 | 11 | $-0.10 |
| 2026-11-07 | 42 | 20 | $+2.59 |

## Automatic trading

Mode: **live** · bankroll $55.48 · caps: 10 contracts/order, $41.61 open (75%), daily stop $-5.55 (10%), total stop $-13.87 (25%)

| Strategy | Mode | Orders | Open | Filled | Settled | P&L |
|---|---|---|---|---|---|---|
| favorites | live | 7 | 6 | 6 | 1 | $+0.14 |
| favorites | paper | 4 | 1 | 4 | 3 | $+0.08 |
| macro_fade | live | 4 | 4 | 3 | 0 | $+0.00 |
| macro_fade | paper | 4 | 4 | 3 | 0 | $+0.00 |
| mentions | live | 5 | 5 | 5 | 0 | $+0.00 |
| mentions | paper | 7 | 7 | 7 | 0 | $+0.00 |
| weather | live | 41 | 22 | 28 | 16 | $+1.28 |

## Strategy grades (allocator)

Mode: **shadow (reported only, sizing unchanged)** · last re-graded 2026-11-07 · hurdle 3c profit per $1 risked

| Strategy | Settled events (real/paper) | Risked | Profit | Per $1 | Trust | Grade | Size x |
|---|---|---|---|---|---|---|---|
| favorites | 1/0 | $1.80 | $+0.14 | +7.8c | 1% | +3.0c | 1.02 (target 1.02) |
| locks | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| m15_btc | 85/0 | $71.28 | $+1.72 | +2.4c | 24% | +2.9c | 1.00 (target 0.95) |
| m15_copper | 8/0 | $12.71 | $+2.29 | +18.0c | 5% | +3.7c | 1.00 (target 1.24) |
| m15_silver | 6/0 | $8.24 | $+0.76 | +9.2c | 3% | +3.2c | 1.00 (target 1.06) |
| macro_fade | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| mentions | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| weather | 7/0 | $5.67 | $+1.28 | +22.6c | 0% | +3.1c | 1.01 (target 1.03) |

Trust = how much the grade follows the strategy's own record instead of the hurdle. It grows with settled events and grows slower for longshots (noisier per dollar).

## New Kalshi markets (launch watcher)

Watching 15939 series since 2026-11-05; 147 new series have opened markets, 73 waiting for their first market.


## Bot health

Scans per day: 11-03: 3, 11-04: 171, 11-05: 288, 11-06: 291, 11-07: 293, 11-08: 258

