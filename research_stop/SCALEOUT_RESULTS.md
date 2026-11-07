# Scaling out: is selling SOME contracts early a good bet? (Nov 7, 2026)

Question (Alex): with several contracts in one position, should the bot sell part
of it early (take some profit, or cut part of a loser) instead of holding all of
them or selling all of them?

Method: `scaleout_bt.py` on the same trade tapes as the stop-loss test (Mentions
2025 to Nov 2026, 38,848 positions; Entertainment, Economics and Elections samples).
Entry is a maker fill in each live strategy's price band. Triggers: our side's bid
rises 3c, 5c or 10c, reaches 97c, or falls 10c or 20c. At a trigger we sell at that
price minus the taker fee. We compare hold all, sell all and sell half, in cents per
contract and as bankroll growth (log) with the position at 15% of the bankroll, so
the lower risk of selling half also gets credit. Raw output: SCALEOUT_raw.txt.

## Result: no. Every early-sell rule loses money, and selling half loses half as much.

| Live band | Hold | Best sell-all rule | Same rule, sell half |
|---|---|---|---|
| Mentions NO 75-90c | +0.93c | at 97c: -2.16c | -1.08c |
| Entertainment favorites | +0.02c | up +10c: -0.38c | -0.19c |
| Fed/CPI NO 71-95c | +1.39c | at 97c: -2.06c | -1.03c |
| Economics favorites | -1.72c | up +10c: -0.10c | -0.05c |
| Elections favorites | -1.73c | up +10c: -0.42c | -0.21c |

(change vs hold, cents per contract; every rule in every band is negative, from
-0.1c to -7.8c)

- **Taking profit is wrong.** After a 3-10c rise, 93-99% of positions go on to win,
  so selling hands back the last few cents plus a fee.
- **Cutting losers is also wrong.** After a 10-20c drop, 57-74% still win: same
  story as the stop-loss test.
- **Selling half sits exactly halfway** between hold and sell all in expected value,
  because every contract in a position pays the same. So it can never beat the
  better of the two. The growth numbers (which credit the lower risk) also rank
  hold first in every band.
- **Depth is rarely a problem at our sizes.** When a trigger fired, at least 6
  contracts traded at or above that price in the same time bucket about 95% of the
  time (and 20+ about 90%).

## What this means for the bot

Partial selling is only right when it comes from the book, not from a rule: if the
EV exit says a contract is worth selling at the bid, it's worth selling all of
them at that bid, and any contracts the bid can't take should stay held and be
checked again. That's what the bot now does (see the PR). No take-profit or
partial-stop rules were added. Diamond hands stays.

Side note: at 15% of the bankroll per position, even holding has slightly negative
log growth in the Mentions band (+0.93c edge on ~83c). By Kelly, the best size for
an edge that thin is about 5% per position, and growth turns negative above about
11%. Mentions words are at about 10% now, so that's fine, but don't let a single
Mentions word grow past ~10% without a better record. The allocator will see this
at Nov 17.
