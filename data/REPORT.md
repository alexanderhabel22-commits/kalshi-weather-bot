# Weather bot report

Updated Thu Nov 05, 09:00 PM · bankroll $48.25 · risk level normal · EDGE alerts off

## Alerts sent to your phone

**Record: 0-0**, +$0.00 at suggested sizes (+0.0% on $0.00 staked). Open: 2 ($4.32).

| When | City | Bracket | Side | Price | Lasted | Result | P&L |
|---|---|---|---|---|---|---|---|
| 11/05 17:50 | Philadelphia high 11-05 | 74° to 75° | NO lock | 28¢ | 145m+ | pending |  |
| 11/05 16:50 | Philadelphia high 11-05 | 74° to 75° | NO lock | 64¢ | 175m+ | pending |  |

## Did the prices last long enough to buy?

- Still buyable at the alert price **15 min later: 50%** (1/2); 60 min later: 100% (2/2).
- Typical time the price lasted: 160+ minutes.

## Lock census: every bracket the readings had already decided

| Day | Locks seen | Cheap enough to alert | Priced ≥98¢ when first seen |
|---|---|---|---|
| 2026-11-03 | 55 | 0 | 55 |
| 2026-11-04 | 62 | 0 | 62 |
| 2026-11-05 | 103 | 1 | 103 |

Over these days, 0% of decided brackets were ever cheap enough to be worth buying. The rest were already priced in by other traders.

## Your actual trades (from `data/my_trades.csv`)

None logged. Add a line per trade you place: `ticker,side,contracts,price,note` (price in dollars, e.g. `0.94`). Or tell Claude and it will add them.

## Silent near-lock test (late-day calls the model rates 95%+, not yet certain)

**Record: 5-0**, +$0.50 if you had taken them (+7.7%). Pending: 7.
Accuracy (Brier, lower is better): model 0.001 vs market 0.010 → **model ahead**.

| Model said | Calls | Market said (avg) | Actually won |
|---|---|---|---|
| 90-99% | 5 | 90% | 100% |

Verdict: keep near-lock alerts silent. Needs 50+ settled calls, a profit, and the model beating the market.

## Forecast accuracy (every city-day the bot priced)

| Lead time | City-days | Model bias | Model avg miss | 80% range held | NWS avg miss |
|---|---|---|---|---|---|
| More than 24h out | 27 | +1.0°F | 2.2°F | 89% (target 80%) | 2.3°F |
| 12–24h out | 45 | +1.0°F | 1.8°F | 82% (target 80%) | 2.4°F |
| 6–12h out | 27 | +0.6°F | 0.9°F | 85% (target 80%) | 2.0°F |
| Last 6h | 44 | +0.2°F | 0.8°F | 86% (target 80%) | 1.6°F |

Bias is actual minus model median (positive = model too cold). If the 80% range holds far more than 80% of the time, the model is too unsure; far less, too sure.

## Rain census (silent test): monthly rain thresholds already passed

Provisional = NWS report plus hourly station rain since; official = NWS climate report total. No alerts are sent for these.

| Market | Above | First seen (provisional) | Ask then | Official | Ask then | Cheapest ask |
|---|---|---|---|---|---|---|
| KXRAINLEXM-26NOV-1 | 1 in | 2026-11-04T20:45 | 100¢ | 2026-11-04T22:00 | 100¢ | 100¢ |
| KXRAINMIAM-26NOV-5 | 5 in | 2026-11-04T20:45 | 100¢ | 2026-11-04T21:45 | 100¢ | 100¢ |
| KXRAINCMHM-26NOV-1 | 1 in | 2026-11-04T22:00 | 100¢ | 2026-11-04T22:00 | 100¢ | 100¢ |
| KXRAINLEXM-26NOV-2 | 2 in | 2026-11-04T22:30 | 100¢ | 2026-11-05T00:45 | 100¢ | 100¢ |

0 of 4 passed thresholds were ever offered at 95¢ or less after we saw them pass.

## Paper trading: day-before limit orders

Orders the bot would rest 1¢ inside the spread about 36 hours before close (no money). Filled = a real trade printed at our price or better.

Placed 34 · open 34 · graded 0 · filled 0 (0%)

- All fills (1 contract each): none yet
- Certain fills only (traded through our price): none yet

## Automatic trading

Mode: **live** · bankroll $48.25 · caps: 10 contracts/order, $36.19 open (75%), daily stop $-4.83 (10%), total stop $-12.06 (25%)

| Strategy | Mode | Orders | Open | Filled | Settled | P&L |
|---|---|---|---|---|---|---|
| favorites | live | 4 | 4 | 2 | 0 | $+0.00 |
| favorites | paper | 4 | 4 | 3 | 0 | $+0.00 |
| macro_fade | live | 4 | 4 | 0 | 0 | $+0.00 |
| macro_fade | paper | 4 | 4 | 3 | 0 | $+0.00 |
| mentions | live | 5 | 5 | 4 | 0 | $+0.00 |
| mentions | paper | 7 | 7 | 7 | 0 | $+0.00 |

## New Kalshi markets (launch watcher)

Watching 15835 series since 2026-11-05; 46 new series have opened markets, 70 waiting for their first market.


## Bot health

Scans per day: 11-03: 3, 11-04: 171, 11-05: 253

