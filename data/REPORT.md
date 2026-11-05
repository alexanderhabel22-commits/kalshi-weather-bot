# Weather bot report

Updated Wed Nov 04, 09:00 PM · bankroll $36.69 · risk level normal · EDGE alerts shadow

## Alerts sent to your phone

None yet.

## Did the prices last long enough to buy?

Not enough follow-up data yet (needs alerts that are 15+ minutes old).

## Lock census: every bracket the readings had already decided

| Day | Locks seen | Cheap enough to alert | Priced ≥98¢ when first seen |
|---|---|---|---|
| 2026-11-03 | 55 | 0 | 55 |
| 2026-11-04 | 61 | 0 | 61 |

Over these days, 0% of decided brackets were ever cheap enough to be worth buying. The rest were already priced in by other traders.

## Your actual trades (from `data/my_trades.csv`)

None logged. Add a line per trade you place: `ticker,side,contracts,price,note` (price in dollars, e.g. `0.94`). Or tell Claude and it will add them.

## Silent EDGE test (forecast calls logged but not sent)

**Record: 1-4**, -$4.11 if you had taken them (-67.3%). Pending: 174.
Accuracy (Brier, lower is better): model 0.370 vs market 0.141 → **market ahead**.

| Model said | Calls | Market said (avg) | Actually won |
|---|---|---|---|
| 20-29% | 1 | 10% | 0% |
| 50-59% | 1 | 20% | 0% |
| 80-89% | 2 | 58% | 50% |
| 90-99% | 1 | 60% | 0% |

Verdict: keep EDGE alerts silent. Needs 50+ settled calls, a profit, and the model beating the market.

## Silent near-lock test (late-day calls the model rates 95%+, not yet certain)

Nothing settled yet (5 pending).

## Forecast accuracy (every city-day the bot priced)

| Lead time | City-days | Model bias | Model avg miss | 80% range held | NWS avg miss |
|---|---|---|---|---|---|
| Last 6h | 27 | +0.3°F | 1.0°F | 85% (target 80%) | 1.7°F |

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

## Bot health

Scans per day: 11-03: 3, 11-04: 136

