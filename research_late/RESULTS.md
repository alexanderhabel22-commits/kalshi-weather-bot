# Late-game sports, player props and lines: results (Nov 8, 2026)

**Bottom line: NO-GO on all three.** Nothing here beats fees in a way that holds up out of sample. No change to the bot or real money.

## Data
- **Late-game:** 898 NBA and 900 NHL games (Jan 2025 to Nov 7, 2026), matched to ESPN play-by-play. The game clock decides "last 10 minutes" (Q4 10:00 / P3 10:00, plus a 30s feed lag), so there is no look-ahead. Kalshi 1-minute bid/ask quotes and every trade print from the start of Q4/P3 to the end, for the winner, spread and total markets.
- **Props and lines:** 107k markets with full trade tapes from about 220 sampled games per series. NBA points/rebounds/assists/threes/double-double, NFL TD/anytime TD/first TD/yards/receptions, MLB HR/K/hits/total bases/H+R+RBI, NHL goalscorer, plus NBA/NFL/MLB/NHL spreads and totals. Game start times come from ESPN.
- Returns are in cents per contract after fees, assuming 5-contract orders: taker 0.07·p·(1−p), maker 0.0175·p·(1−p) only where the series charges it. ± is a 95% CI clustered by game. IS = before Jul 1 2026, OOS = after.
- Bot-snapshot method (as in the edge mine): the decision uses only data known at the time. Taker buys at the next minute's ask or the next real print. Maker fills only if a later print trades through the bid.
- Code: `research_late/` on branch `claude/project-thread-fsfh1t`. Data: branch `late-research-data`.

## 1. Fade 10-40c contracts in the last 10 minutes (buy the favourite, hold)

| | IS | OOS |
|---|---|---|
| NBA winner, taker, 10 min left | −3.5 ±3.7 (469 games) | +3.2 ±8.8 (65) |
| NBA winner, maker, 10 min left | −3.0 ±4.0 | +4.5 ±9.9 |
| NBA winner, taker, 5 min left | +0.6 ±4.1 | +9.0 ±10.4 (41) |
| NHL winner, taker, 10 min left | −3.2 ±3.6 (434) | +3.5 ±7.1 (92) |
| NBA spread/total, taker | −4 to −10 | −1 to −4 |
| NHL spread/total, taker | −4 to −8 | −2 to +3 |

Calibration (underdog's price vs how often it won): NBA 10-20c dogs won 14.2% at an average price of 13.7c, and 20-30c dogs won 27.0% at 24.9c. NHL 10-20c dogs won 12.7% at 15.3c. **The market prices late underdogs about right.** The paper's "they almost never win" claim does not hold on Kalshi in 2025-26. The OOS numbers look positive, but that's only 40-90 games of Oct-Nov 2026, inside the noise, and IS is negative. Lines lose in every cell.

## 2. Player props: longshot bias

Fading cheap YES props before the game (2h out, 10-40c), pooled across all sports: taker −5 to +4, resting bid cancelled at tip-off −1.5 to +0.8, in both IS and OOS, all within noise. Buying the cheap side instead is also ~0 to −2. Leaving the bid resting into the game loses 6-29c, because in-play news picks off resting orders. In-game taker fades are ~0.

The raw calibration looks like a *reverse* longshot bias: props last traded at 20-30c hit 27.3% of the time. But that comes from where prints happen in the bid/ask spread, and it disappears once you have to actually buy at the ask or wait for a fill.

The one faint lead is NFL anytime TD: fade IS taker +7.0 ±7.6 (72), maker +3.8 ±4.1 (154 games). The series was replaced by KXNFLTD, where OOS gives taker −3.5 ±5.7 and maker +4.2 ±5.0. It isn't significant and doesn't hold for takers, so it's not worth money. It's the only thing worth re-checking after the NFL season (Feb 2027).

## 3. Lines vs sportsbooks
At the DraftKings closing number, Kalshi's NBA/NFL spread and total markets trade at 49.5-50.7c (220 matched games). That is exactly where a fair 50/50 line should sit, so there is no gap to trade. NHL puck lines (1.5 goals) aren't 50/50 lines, so that row doesn't apply. This matches the earlier finding that Kalshi winner prices sit within ~1c of DraftKings over 8,259 games.

## Capacity at $50
This doesn't matter, because nothing is positive. For reference, NBA/NFL props trade 2-55M contracts a season pre-game, so a $50 account would never move prices.

## With more money or tools
None of these improve with size, since the per-contract edge is about zero. The only real in-play edge is speed: buying the favourite seconds after a score, before market makers re-quote. That needs a sub-second paid data feed ($100-500/mo), an always-on server and co-located execution, and it means competing with professional market makers. It's not recommended at any bankroll under ~$5-10k.
