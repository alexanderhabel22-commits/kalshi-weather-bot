# Checkpoint rules (written Nov 8, 2026, before the data)

These are the rules for the Nov 17 checkpoint and every later one. They were
written down before the real-money results came in, so the call can't be bent
to fit the numbers. `python bot.py checkpoint` applies them and prints the
scorecard. It only reads data: it never changes sizes, config or orders.

## What is counted

- REAL money only, from the first live order (Nov 5, 2026, 7:03 PM ET). Paper is ignored.
- One **event** = all bets on one speech, one city-day, one election, one Fed
  meeting, added together. Ten correlated bets on one speech count once.
- **Profit per $1** = settled profit (after fees, including early sells) divided
  by the money paid for the contracts plus fees.
- **90% range** = bootstrap over whole events (4,000 resamples, fixed seed).
  Not shown below 10 settled events, because 4 events can't make a range.

## Verdicts per strategy (first rule that matches wins)

| # | Rule | Verdict | What happens |
|---|---|---|---|
| 1 | Real loss on the strategy is more than 15% of the bankroll | **CUT** | Halve its base size. Tell Alex. |
| 2 | 20+ settled events and the whole 90% range is below zero | **KILL** | Back to paper (`live = false`). Tell Alex. |
| 3 | 20+ settled events, losing, and the 90% range is below the backtest figure | **CUT** | Halve its base size. |
| 4 | 20+ settled events and the whole 90% range is above zero | **RAISE** | Base size x1.5, inside Kelly and the existing caps. Tell Alex. |
| 5 | Anything else | **KEEP** | No change. The allocator does the slow adjusting. |

KEEP also carries two warnings that don't change sizes by themselves:
- under 25% of contracts filled after 30+ orders: the prices are wrong for this market, review them;
- fees eating more than half the expected edge.

The overall 75% cap, the 20% per-event cap and the loss stops (-10% a day,
-25% total) are not part of the checkpoint and never change here.

## Allocator: shadow to live

Switch `[allocator] apply = true` at the checkpoint if all of these hold:
1. its profit-per-$1 for each strategy matches the scorecard's own recount within 1c;
2. no strategy is CUT or KILL;
3. the bot is not halted.

Otherwise it stays shadow and the failing reason gets fixed first. With little
data its sizes barely move (trust ~1% today), so switching on mainly starts
the clock; it cannot move a strategy more than 0.25x per week.

## How many events before a result means anything

"Events needed" = the number of settled events at which a strategy that really
earns its backtest edge would show a 90% range above zero 4 times out of 5:
`(2.49 x spread per event / backtest edge)^2`. The spread is measured once a
strategy has 20+ settled events; before that it comes from the backtest
(Mentions 29%) or from the prices paid.

As of Nov 8 (live pace from the first 2 days, so rough):

| Strategy | Backtest per $1 | Events needed | Expected by Nov 17 |
|---|---|---|---|
| Mentions NO | +4c | ~330 | ~5 |
| Favorites | +3.8c | ~270 | ~10 |
| Fed/CPI fade | +6.3c | ~230 | ~15 |
| Weather day-before | +2c (unproven) | ~19,000 | ~65 |

**The plain consequence:** no strategy can be *proven* on Nov 17, or for many
months at $50 sizes. Nov 17 can only catch a strategy that is clearly broken
(rules 1-3). Proving an edge (rule 4) needs far more events, which is why
growing the bet count and letting the allocator learn slowly is the plan,
rather than waiting for proof. Weather's edge is too small to ever prove at
these sizes; it stays only while it isn't clearly losing, at the 15% cap.

## Backtest figures used

- Mentions: research_mn/RESULTS.md, +4-6c per contract, last 6 months +4.3c; per-event spread 29%.
- Favorites: research_alloc/RESULTS.md streams, 659 fills, 97% hit, +3.8c per $1.
- Fed/CPI fade: research_alloc/RESULTS.md streams, 256 fills, 93% hit, +6.3c per $1.
- Weather day-before: about +1c per contract this fall at ~45c (unproven; the taker proxy was -1.4c).

They live in `wxbot/checkpoint.py` (`EXPECTED`). Changing them after Nov 8
needs a note here saying why, so the goalposts don't move quietly.
