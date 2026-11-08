# Weather bot report

Updated Sat Nov 07, 09:00 PM · bankroll $51.60 · risk level normal · EDGE alerts off

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
| 2026-11-07 | 81 | 0 | 81 |

Over these days, 0% of decided brackets were ever cheap enough to be worth buying. The rest were already priced in by other traders.

## Your actual trades (from `data/my_trades.csv`)

None logged. Add a line per trade you place: `ticker,side,contracts,price,note` (price in dollars, e.g. `0.94`). Or tell Claude and it will add them.

## Silent near-lock test (late-day calls the model rates 95%+, not yet certain)

**Record: 22-1**, +$2.24 if you had taken them (+5.6%). Pending: 6.
Accuracy (Brier, lower is better): model 0.044 vs market 0.047 → **model ahead**.

| Model said | Calls | Market said (avg) | Actually won |
|---|---|---|---|
| 90-99% | 23 | 88% | 96% |

Verdict: keep near-lock alerts silent. Needs 50+ settled calls, a profit, and the model beating the market.

## Forecast accuracy (every city-day the bot priced)

| Lead time | City-days | Model bias | Model avg miss | 80% range held | NWS avg miss |
|---|---|---|---|---|---|
| More than 24h out | 225 | +1.0°F | 1.9°F | 93% (target 80%) | 2.0°F |
| 12–24h out | 153 | +0.9°F | 1.5°F | 90% (target 80%) | 2.0°F |
| 6–12h out | 81 | +0.5°F | 0.7°F | 90% (target 80%) | 1.8°F |
| Last 6h | 78 | +0.3°F | 0.6°F | 90% (target 80%) | 1.5°F |

Bias is actual minus model median (positive = model too cold). If the 80% range holds far more than 80% of the time, the model is too unsure; far less, too sure.

Biggest city biases (12h+ out): Dallas high +4.1°F (n=15); Atlanta high +4.1°F (n=12); Denver high +2.8°F (n=15); Chicago low -2.6°F (n=15); Oklahoma City high +2.1°F (n=15).

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

0 of 10 passed thresholds were ever offered at 95¢ or less after we saw them pass.

## Paper trading: day-before limit orders

Orders the bot would rest 1¢ inside the spread about 36 hours before close (no money). Filled = a real trade printed at our price or better.

Placed 123 · open 89 · graded 34 · filled 24 (70%)

- All fills (1 contract each): 24 contracts, 11 won, P&L $-0.10 on $11.10 (-0.9%)
- Certain fills only (traded through our price): 23 contracts, 10 won, P&L $-0.77 on $10.77 (-7.1%)

| Day | Fills | Won | P&L |
|---|---|---|---|
| 2026-11-06 | 24 | 11 | $-0.10 |

## Automatic trading

Mode: **live** · bankroll $51.60 · caps: 10 contracts/order, $38.70 open (75%), daily stop $-5.16 (10%), total stop $-12.90 (25%)

| Strategy | Mode | Orders | Open | Filled | Settled | P&L |
|---|---|---|---|---|---|---|
| favorites | live | 7 | 6 | 5 | 1 | $+0.14 |
| favorites | paper | 4 | 1 | 4 | 3 | $+0.08 |
| macro_fade | live | 4 | 4 | 3 | 0 | $+0.00 |
| macro_fade | paper | 4 | 4 | 3 | 0 | $+0.00 |
| mentions | live | 5 | 5 | 5 | 0 | $+0.00 |
| mentions | paper | 7 | 7 | 7 | 0 | $+0.00 |
| weather | live | 27 | 19 | 21 | 8 | $+1.58 |

## Strategy grades (allocator)

Mode: **shadow (reported only, sizing unchanged)** · last re-graded 2026-11-07 · hurdle 3c profit per $1 risked

| Strategy | Settled events (real/paper) | Risked | Profit | Per $1 | Trust | Grade | Size x |
|---|---|---|---|---|---|---|---|
| favorites | 1/0 | $1.80 | $+0.14 | +7.8c | 1% | +3.0c | 1.02 (target 1.02) |
| locks | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| macro_fade | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| mentions | 0/0 | $0.00 | $+0.00 |  | 0% | +3.0c | 1.00 (target 1.00) |
| weather | 4/0 | $3.37 | $+1.58 | +46.9c | 0% | +3.2c | 1.01 (target 1.05) |

Trust = how much the grade follows the strategy's own record instead of the hurdle. It grows with settled events and grows slower for longshots (noisier per dollar).

## New Kalshi markets (launch watcher)

Watching 15919 series since 2026-11-05; 123 new series have opened markets, 77 waiting for their first market.


## Bot health

Scans per day: 11-03: 3, 11-04: 171, 11-05: 288, 11-06: 291, 11-07: 258

