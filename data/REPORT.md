# Weather bot report

Updated Mon Nov 09, 09:00 PM · bankroll $62.32 · risk level normal · EDGE alerts off

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
| 2026-11-09 | 63 | 0 | 63 |

Over these days, 0% of decided brackets were ever cheap enough to be worth buying. The rest were already priced in by other traders.

## Your actual trades (from `data/my_trades.csv`)

None logged. Add a line per trade you place: `ticker,side,contracts,price,note` (price in dollars, e.g. `0.94`). Or tell Claude and it will add them.

## Silent near-lock test (late-day calls the model rates 95%+, not yet certain)

**Record: 29-4**, -$3.55 if you had taken them (-5.9%). Pending: 3.
Accuracy (Brier, lower is better): model 0.116 vs market 0.101 → **market ahead**.

| Model said | Calls | Market said (avg) | Actually won |
|---|---|---|---|
| 90-99% | 33 | 88% | 88% |

Verdict: keep near-lock alerts silent. Needs 50+ settled calls, a profit, and the model beating the market.

## Forecast accuracy (every city-day the bot priced)

| Lead time | City-days | Model bias | Model avg miss | 80% range held | NWS avg miss |
|---|---|---|---|---|---|
| More than 24h out | 423 | +1.0°F | 1.8°F | 96% (target 80%) | 1.8°F |
| 12–24h out | 261 | +1.0°F | 1.5°F | 91% (target 80%) | 1.8°F |
| 6–12h out | 135 | +0.5°F | 0.7°F | 92% (target 80%) | 1.6°F |
| Last 6h | 112 | +0.2°F | 0.5°F | 92% (target 80%) | 1.2°F |

Bias is actual minus model median (positive = model too cold). If the 80% range holds far more than 80% of the time, the model is too unsure; far less, too sure.

Biggest city biases (12h+ out): Atlanta high +3.7°F (n=22); Dallas high +3.1°F (n=27); Denver high +2.4°F (n=27); Minneapolis high +2.1°F (n=27); Houston high +1.9°F (n=27).

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

Placed 208 · open 85 · graded 123 · filled 97 (78%)

- All fills (1 contract each): 97 contracts, 41 won, P&L $+0.71 on $40.29 (+1.8%)
- Certain fills only (traded through our price): 95 contracts, 39 won, P&L $-0.26 on $39.26 (-0.7%)

| Day | Fills | Won | P&L |
|---|---|---|---|
| 2026-11-06 | 24 | 11 | $-0.10 |
| 2026-11-07 | 42 | 20 | $+2.59 |
| 2026-11-08 | 31 | 10 | $-1.78 |

## Automatic trading

Mode: **live** · bankroll $62.32 · caps: 10 contracts/order, $46.74 open (75%), daily stop $-6.23 (10%), total stop $-15.58 (25%)

| Strategy | Mode | Orders | Open | Filled | Settled | P&L |
|---|---|---|---|---|---|---|
| favorites | live | 8 | 1 | 7 | 6 | $+1.14 |
| favorites | paper | 4 | 0 | 4 | 4 | $+0.28 |
| macro_fade | live | 4 | 3 | 3 | 0 | $+0.00 |
| macro_fade | paper | 4 | 4 | 3 | 0 | $+0.00 |
| mentions | live | 8 | 2 | 7 | 6 | $+0.12 |
| mentions | paper | 7 | 1 | 7 | 6 | $+2.56 |
| weather | live | 56 | 23 | 37 | 23 | $-0.11 |

## Strategy grades (allocator)

Mode: **shadow (reported only, sizing unchanged)** · last re-graded 2026-11-07 · hurdle 3c profit per $1 risked

| Strategy | Settled events (real/paper) | Risked | Profit | Per $1 | Trust | Grade | Size x |
|---|---|---|---|---|---|---|---|
| favorites | 2/0 | $14.74 | $+1.14 | +7.7c | 2% | +3.1c | 1.02 (target 1.04) |
| locks | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| m15_btc | 177/0 | $212.33 | $+5.67 | +2.7c | 44% | +2.9c | 1.00 (target 0.95) |
| m15_copper | 74/0 | $101.56 | $-1.56 | -1.5c | 30% | +1.6c | 1.00 (target 0.54) |
| m15_palladium | 39/0 | $59.67 | $-4.67 | -7.8c | 16% | +1.2c | 1.00 (target 0.41) |
| m15_platinum | 11/0 | $14.25 | $-3.25 | -22.8c | 6% | +1.5c | 1.00 (target 0.50) |
| m15_silver | 74/0 | $106.76 | $+6.24 | +5.8c | 29% | +3.8c | 1.00 (target 1.28) |
| m15_wti | 18/0 | $22.54 | $-1.54 | -6.8c | 9% | +2.1c | 1.00 (target 0.71) |
| macro_fade | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| mentions | 2/0 | $13.88 | $+0.12 | +0.9c | 2% | +3.0c | 1.00 (target 0.99) |
| weather | 13/0 | $7.06 | $-0.11 | -1.6c | 1% | +3.0c | 1.01 (target 0.99) |

Trust = how much the grade follows the strategy's own record instead of the hurdle. It grows with settled events and grows slower for longshots (noisier per dollar).

## New Kalshi markets (launch watcher)

Watching 16024 series since 2026-11-05; 177 new series have opened markets, 128 waiting for their first market.


## Bot health

Scans per day: 11-03: 3, 11-04: 171, 11-05: 288, 11-06: 291, 11-07: 293, 11-08: 293, 11-09: 252

