# Signals from other traders' flow (not copying anyone), Nov 13, 2026

Question (Alex, Nov 13): besides copying people, is there anything in the data we've been
collecting from other traders that we can use?

**Answer: no tradeable signal.** Following AND fading big bets both lose, and so do following
the best track records or fading the worst. Prices already reflect this flow, and what we'd
pay to act on it (spread + taker fee, 2-4c) is more than any edge it carries.

## Data
- Copycat logs (branch copycat-data, Nov 10 10:38 PM - Nov 13 ET): 176,835 settled fills from
  1,344 tracked accounts (109,097 logged live), plus 47,257 big bets (anonymous $2k+, named $500+).
- Settlements for 68,767 markets fetched by `research_flow/settle.py` (Actions job
  flow-research, branch flow-research-data).
- Train = trades before Nov 12 7 AM ET, holdout = after. Grouped by event.
- Already tested before on the same data and not repeated: copying single accounts (copycat),
  hot streaks (analysis/streaks), new-account big bets, casual small-order flow (analysis/casual-flow,
  the one lead, already on paper as "Selling the field").

## A. Big bets: follow vs fade (we buy right after at their price +1c, taker fee off)
Cents per contract, train | holdout:

| Market group | Follow anonymous | Fade anonymous | Follow named | Fade named |
|---|---|---|---|---|
| 15-min | -1.8 \| -3.7 | -3.3 \| -1.4 | -3.7 \| -4.9 | -1.5 \| -0.1 |
| Sports | -3.3 \| -1.4 | -2.1 \| -3.9 | -6.1 \| -3.1 | +0.9 \| -2.1 |
| Parlays | +0.1 \| +2.5 | -5.6 \| -8.0 | -5.6 \| +2.8 | +0.1 \| -8.2 |
| Other | +1.0 \| -2.6 | -6.4 \| -2.6 | -5.4 \| -3.7 | 0.0 \| -1.7 |
| Other crypto | -1.8 \| -12.1 | -3.1 \| +6.9 | -2.9 \| -14.7 | -2.0 \| +9.8 |

Both sides lose about the cost of crossing the spread: the big bettor's price is about fair.
The few positive holdout cells were negative on train (other crypto: 22 events) or are parlays,
which we can't buy at that price (RFQ only).

## B. Track records: follow the top fifth, fade the bottom fifth
Each account was scored only on its bets settled before the new trade (>= 20). Cut points were
set on train: top fifth >= +5.7c, bottom fifth <= -2.7c. We buy at the ask Copycat saw ~15 s later.

| Group | Follow top fifth (train \| holdout) | Fade bottom fifth (train \| holdout) |
|---|---|---|
| 15-min | -4.4 \| -5.7 | -1.4 \| +2.3 ± 1.7 |
| Sports | -3.6 \| -4.1 | -3.3 \| -0.7 |
| Other | -3.3 \| -8.5 | +1.7 \| -1.5 |
| Parlays | -7.0 \| -7.7 | -3.7 \| -0.7 |
| Other crypto | +0.1 \| -13.6 | -3.7 \| +7.3 |

A good past record does not predict the next trade once we pay to get in. Nothing is positive on
both train and holdout.

## Verdict
NO. Nothing clears +3c/ct after fees on both periods, so nothing goes to real money or paper.
The one place others' flow did pay is passive: resting orders that small casual traders hit
(the casual-flow study), which is already being tested on paper (market-making and "Selling the
field" books). Re-run `research_flow/analyze.py` if Copycat's full 7-day public feed is ever
exported from the server, but expect the same answer: crossing the spread costs more than the signal.
