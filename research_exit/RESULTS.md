# Model-based early exit for weather day-before positions (Nov 6, 2026)

Question (Alex): if a weather position's price rises above what the model thinks
it's worth, should the bot sell instead of holding to settlement?

Method: `exit_bt.py` on the backtest archive (Sep 2025 - Nov 3 2026, 27 series,
walk-forward by month from Jan 2026). Entries are exactly the live rule (rest 1c
inside the spread 36h before close when the blend edge is >= 6c; filled when a
later trade printed at our price). Exit checks at 24h and 12h before close:
re-blend market mid with the model (refit for that hour) and sell at the bid if
bid - taker fee >= model value + margin.

| Exit rule | Sells | P&L | Return on cost | Months better than hold |
|---|---|---|---|---|
| Hold to settlement | 0 | +$389.93 | +13.0% | - |
| margin 0c | 897 | +$487.76 | +16.3% | 9 of 11 |
| margin 3c | 611 | +$480.32 | +16.0% | 11 of 11 |
| margin 5c | 465 | +$467.99 | +15.6% | 11 of 11 |
| **margin 8c (chosen)** | 335 | +$455.12 | +15.2% | 11 of 11 |
| margin 15c | 144 | +$428.50 | +14.3% | 10 of 11 |

Where the gain comes from: with the day-before forecast at the exit check
(`LEAD12=d1`), the rule almost never fires (0 sells at 3c+). All of the gain is
the fresher same-day forecast at 12h before close (about 1 PM local). Highs gain
much more than lows (highs +$77 at 8c; lows +$1).

Caveat: the archived same-day forecast is stitched from the newest runs and can
include a run that wasn't out yet at 1 PM, so the real gain is probably smaller
than shown. That's why the live margin is 8c, not 0-3c: a modest edge survives
even if the model is somewhat worse live, and Alex's rule (don't sell a 50%
bracket at 51c) holds.

Live: `[trading.weather] exit = "model"`, `exit_margin = 0.08`. The check runs
only within 45 minutes of 12h before close; otherwise the bot holds.
Params: `exit_params.py` adds the `exit` block (same-day model fit, 12h blend) to
data/daybefore_params.json. Raw output: RESULTS_raw.txt.
