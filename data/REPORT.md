# Weather bot report

Updated Fri Nov 06, 09:00 PM · bankroll $50.84 · risk level normal · EDGE alerts off

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

Over these days, 0% of decided brackets were ever cheap enough to be worth buying. The rest were already priced in by other traders.

## Your actual trades (from `data/my_trades.csv`)

None logged. Add a line per trade you place: `ticker,side,contracts,price,note` (price in dollars, e.g. `0.94`). Or tell Claude and it will add them.

## Silent near-lock test (late-day calls the model rates 95%+, not yet certain)

**Record: 12-1**, +$0.48 if you had taken them (+2.3%). Pending: 9.
Accuracy (Brier, lower is better): model 0.077 vs market 0.073 → **market ahead**.

| Model said | Calls | Market said (avg) | Actually won |
|---|---|---|---|
| 90-99% | 13 | 87% | 92% |

Verdict: keep near-lock alerts silent. Needs 50+ settled calls, a profit, and the model beating the market.

## Forecast accuracy (every city-day the bot priced)

| Lead time | City-days | Model bias | Model avg miss | 80% range held | NWS avg miss |
|---|---|---|---|---|---|
| More than 24h out | 126 | +1.5°F | 2.3°F | 91% (target 80%) | 2.2°F |
| 12–24h out | 99 | +1.1°F | 1.7°F | 86% (target 80%) | 2.1°F |
| 6–12h out | 54 | +0.5°F | 0.8°F | 89% (target 80%) | 1.9°F |
| Last 6h | 61 | +0.3°F | 0.7°F | 87% (target 80%) | 1.5°F |

Bias is actual minus model median (positive = model too cold). If the 80% range holds far more than 80% of the time, the model is too unsure; far less, too sure.

Biggest city biases (12h+ out): Oklahoma City high +4.9°F (n=9); Atlanta high +4.6°F (n=7); Dallas high +4.4°F (n=9); Austin high +3.8°F (n=9); Chicago low -3.4°F (n=9).

## Rain census (silent test): monthly rain thresholds already passed

Provisional = NWS report plus hourly station rain since; official = NWS climate report total. No alerts are sent for these.

| Market | Above | First seen (provisional) | Ask then | Official | Ask then | Cheapest ask |
|---|---|---|---|---|---|---|
| KXRAINLEXM-26NOV-1 | 1 in | 2026-11-04T20:45 | 100¢ | 2026-11-04T22:00 | 100¢ | 100¢ |
| KXRAINMIAM-26NOV-5 | 5 in | 2026-11-04T20:45 | 100¢ | 2026-11-04T21:45 | 100¢ | 100¢ |
| KXRAINCMHM-26NOV-1 | 1 in | 2026-11-04T22:00 | 100¢ | 2026-11-04T22:00 | 100¢ | 100¢ |
| KXRAINLEXM-26NOV-2 | 2 in | 2026-11-04T22:30 | 100¢ | 2026-11-05T00:45 | 100¢ | 100¢ |
| KXRAINBOSM-26NOV-1 | 1 in | 2026-11-06T17:30 | 100¢ | 2026-11-06T21:30 | 100¢ | 100¢ |

0 of 5 passed thresholds were ever offered at 95¢ or less after we saw them pass.

## Paper trading: day-before limit orders

Orders the bot would rest 1¢ inside the spread about 36 hours before close (no money). Filled = a real trade printed at our price or better.

Placed 83 · open 83 · graded 0 · filled 0 (0%)

- All fills (1 contract each): none yet
- Certain fills only (traded through our price): none yet

## Automatic trading

Mode: **live** · bankroll $50.84 · caps: 10 contracts/order, $38.13 open (75%), daily stop $-5.08 (10%), total stop $-12.71 (25%)

| Strategy | Mode | Orders | Open | Filled | Settled | P&L |
|---|---|---|---|---|---|---|
| favorites | live | 4 | 4 | 3 | 0 | $+0.00 |
| favorites | paper | 4 | 1 | 4 | 3 | $+0.08 |
| macro_fade | live | 4 | 4 | 3 | 0 | $+0.00 |
| macro_fade | paper | 4 | 4 | 3 | 0 | $+0.00 |
| mentions | live | 5 | 5 | 5 | 0 | $+0.00 |
| mentions | paper | 7 | 7 | 7 | 0 | $+0.00 |
| weather | live | 19 | 17 | 7 | 2 | $+0.18 |

## New Kalshi markets (launch watcher)

Watching 15896 series since 2026-11-05; 97 new series have opened markets, 80 waiting for their first market.


## Bot health

Scans per day: 11-03: 3, 11-04: 171, 11-05: 288, 11-06: 256

