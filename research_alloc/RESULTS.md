# Strategy allocator backtest (Nov 7, 2026)

Question (Alex): can the bot grade its own strategies and move money toward what
works, without switching any off, re-graded about weekly?

## Method
- `streams.py` rebuilds each live strategy's historical fills from the research
  data branches (mentions-, cat-, fin-research-data, backtest-data), Feb 2025 - Nov 2026:
  | strategy | fills | hit rate | avg price | per $1 |
  |---|---|---|---|---|
  | favorites | 659 | 97% | 94c | +3.8c |
  | macro_fade | 256 | 93% | 87c | +6.3c |
  | mentions | 11,308 | 86% | 85c | +2.2c |
  | weather* | 15,386 | 40% | 41c | -1.4c |
  *Weather uses the walk-forward model's TAKER rule (Oct 2025 on), not the live
  maker orders, so it stands in for "a strategy that stopped working" (it fades
  in the second half, as the live maker edge did this fall).
- `replay.py` samples fills each week at about the live pace (Mentions 25,
  favorites 10, macro 4, weather 50 per week), grades every Monday with the
  live `[allocator]` settings, and compares with fixed 1x sizing. 20 random samples.

## Result (RESULTS_raw.txt)
| | profit | risked | per $1 | beat fixed |
|---|---|---|---|---|
| fixed 1x | $66.7 | $3,232 | +2.06c | - |
| allocator | $90.2 | $3,785 | +2.36c | 20/20 |
| allocator, same $ risked | $70.1 | $3,232 | +2.17c | 13/20 |

- It moved money the right way: weather fell from 1.4x (spring) to the 0.25x
  floor by Oct 2026; macro and favorites rose to ~1.3-1.4x.
- Most of the gain (+35%) comes from betting more on the winners overall; pure
  re-shuffling of the same dollars adds ~5%. So the bigger lever is "size up
  what's proven", which only matters once caps stop binding.
- Settings are not fragile: prior_sd 0.02-0.08, max_step 0.25-0.5, hurdle
  2-3c, floor 0.25-0.5, ceiling 1.5-2 all beat fixed sizing in every sample.
- Caveats: fills are approximate (maker fills judged from the trade tape in time
  buckets), history only (no Nov 2026 regime), sizes ignore the 75% overall cap.
