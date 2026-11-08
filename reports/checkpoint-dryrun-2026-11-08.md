# Checkpoint scorecard (Nov 08 02:38 UTC)

Real money only, since 2026-11-05 23:03 UTC (2.1 days). Bankroll $50.96. Decision day: 2026-11-17. Rules: CHECKPOINT.md (written Nov 8, before the data).

Settled real money so far: $+1.72 on $5.17 risked.

## Verdicts

| Strategy | Verdict | Why |
|---|---|---|
| mentions | **KEEP** | too early: 0 of 20 settled events for any verdict |
| favorites | **KEEP** | too early: 1 of 20 settled events for any verdict |
| macro_fade | **KEEP** | too early: 0 of 20 settled events for any verdict |
| weather | **KEEP** | too early: 4 of 20 settled events for any verdict |
| allocator | **SWITCH ON (apply = true)** | grades match the scorecard's recount and no strategy is broken |

## Results (settled real-money events)

| Strategy | Events | Contracts | Risked | Profit | Per $1 | 90% range | Chance > 0 | Hit rate | Fees | Backtest per $1 |
|---|---|---|---|---|---|---|---|---|---|---|
| mentions | 0 | 0 | $0.00 | $+0.00 | - | needs 10+ | - | - | $0.00 | +4.0c |
| favorites | 1 | 2 | $1.80 | $+0.14 | +7.8c | needs 10+ | - | 100% | $0.00 | +3.8c |
| macro_fade | 0 | 0 | $0.00 | $+0.00 | - | needs 10+ | - | - | $0.00 | +6.3c |
| weather | 4 | 8 | $3.37 | $+1.58 | +46.9c | needs 10+ | - | 100% | $0.00 | +2.0c |

## Fills vs the backtest

| Strategy | Orders | Still resting | Contracts offered | Filled | Fill rate so far | Avg price paid | Filled events/week | Backtest pace | Awaiting settlement |
|---|---|---|---|---|---|---|---|---|---|
| mentions | 5 | 0 | 10 | 10 | 100% | 86c | 3.3 | 10 | 1 |
| favorites | 7 | 2 | 18 | 12 | 67% | 91c | 6.5 | 5 | 1 |
| macro_fade | 4 | 1 | 4 | 3 | 75% | 88c | 9.8 | 1.5 | 3 |
| weather | 27 | 5 | 27 | 22 | 81% | 30c | 42.3 | 20 | 9 |

## When does each result mean something?

Events needed = settled events at which a strategy really earning its backtest edge would show a 90% range above zero 4 times out of 5.

| Strategy | Spread per event | Events needed | Have | Expected by checkpoint | Reaches it (live pace) |
|---|---|---|---|---|---|
| mentions | 29% (backtest) | 326 | 0 | ~5 | Oct 08 2028 |
| favorites | 25% (from price) | 274 | 1 | ~10 | Aug 28 2027 |
| macro_fade | 39% (from price) | 233 | 0 | ~15 | Apr 24 2027 |
| weather | 111% (from price) | 18,900 | 4 | ~67 | 5+ years |

## Allocator (shadow) would-be sizes

| Strategy | Now | If re-graded today | Target | Trust |
|---|---|---|---|---|
| mentions | 1.00x | 1.00x | 1.00x | 0.0% |
| favorites | 1.02x | 1.02x | 1.02x | 1.1% |
| macro_fade | 1.00x | 1.00x | 1.00x | 0.0% |
| weather | 1.01x | 1.05x | 1.05x | 0.4% |

Backtest sources:
- mentions: research_mn: +4-6c/contract, last 6 months +4.3c; per event sd 29%
- favorites: research_alloc streams: 659 fills, 97% hit, +3.8c per $1
- macro_fade: research_alloc streams: 256 fills, 93% hit, +6.3c per $1
- weather: day-before maker ~+1c/contract this fall (unproven)
