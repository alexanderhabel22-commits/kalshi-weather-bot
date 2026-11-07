# Whole-platform edge mine: results (Nov 7, 2026)

**Bottom line: no new edge.** Every new idea came back NO-GO. The only patterns that survive the out-of-sample check are versions of strategies already running: big-event Elections favorites, and the Fed/CPI longshot fade (macro_fade). Nothing here calls for a change to the bot or to real money.

## Data

- An unbiased random sample from every Kalshi category: up to 3 settled markets per series per month, for every series with 50k+ lifetime volume. Parlay/combo (MVE) markets are excluded.
- **45,138 markets, 30,250 events, 24.0M exact trades** (each with its taker side), closing Jan 1 2025 – Nov 6 2026. Only 1.2% of trade tapes were cut off at the 10k-trade cap.
- In-sample (IS) is markets closing before Jul 1 2026. Out-of-sample (OOS) is Jul 1 – Nov 6 2026, which is 41% of the trades.
- Returns are in cents per contract, after fees. Taker fees use the 0.07·p·(1−p) formula with 10-lot rounding; maker fees apply only where the series charges them. ± is a 95% CI clustered by event.
- Code is in `research_hist/` on branch `claude/project-thread-y3tcch`. Data is on branch `hist-research-data`.

## Two methods, and why the second one decides

1. **Trade-level.** Treat every real print as a fill: buy what the taker bought at the price they paid, or, as a resting maker, own the other side, counted only if the level was traded through within 1 hour.
2. **Bot snapshot (this one decides).** Check every market every 5 minutes, using only past data. The first time a side enters a price band, either (a) buy it at the next real print on that side within 2 minutes, or (b) rest a bid 1c below the last print. The bid fills only if a later print goes through it ("back of queue"), or, in the optimistic version, if a print merely touches it. One entry per market.

The trade-level method shows a strong favourite-longshot bias. The bot snapshot shows that a mechanical bot cannot collect it. The profitable favourite buys in the tape are well timed by fast or informed traders. A bot that enters on a schedule pays average prices, and its resting bids mostly fill while the favourite is collapsing.

## Lead-by-lead verdicts

| Lead | What the data says | Verdict |
|---|---|---|
| Favourite-longshot bias, sports (game, futures, props, totals, spreads) | In the tape, takers buying at 10-35c lose 5-10c and buyers of 65-95c favourites make +1 to +7c, in both IS and OOS. In the bot snapshot, taker entries are about 0: sports games 85-95c +0.1 ±2.1 (IS) and −0.9 ±2.1 (OOS) over 1,600 entries. Maker bids lose 2-15c (games 85-95c: −2.8 IS / −3.6 OOS). | **NO-GO** |
| FLB elsewhere (crypto, commodities, econ, financials, entertainment, politics, sci/tech, weather) | Bot snapshot: taker about 0 with wide CIs, maker −3 to −18c everywhere. | **NO-GO** |
| Elections favourites | Bot snapshot taker OOS: 85-95c +5.5 ±5.9, 95-99c +2.3 ±1.4. The grid search agrees (Elections 85c+, more than a day out: +5.0 to +5.5 OOS over 8-10M contracts). | This is already live as "big-event favorites", so no new action. |
| "Last hour" sports favourites (football/tennis +4 to +7c) | **Look-ahead.** Sports markets close when the game ends, so "1 hour to close" is not known in real time. Taken over the whole market life instead, the result is about 0. | **NO-GO (artefact)** |
| Maker spread capture (two-sided, all prices) | Back-of-queue maker, IS/OOS: sports −1.7/−1.0, crypto +0.9/−1.4, elections +0.5/−0.3, politics +0.4/−0.4, entertainment +0.1/+0.5, econ −0.1/−6.5. | **NO-GO** |
| Time of day (ET) and weekday/weekend | No 6-hour block or weekend cell beats its category average in both periods. Takers lose 1-4c in every block. | **NO-GO** |
| Momentum (buy with or against the last hour's move) | Taker returns are negative in every momentum bucket and category. Example: sports ≥10c move +2.9 IS / 0.0 OOS; elections ≤−10c move −8.3 / −5.6. | **NO-GO** |
| Follow or fade big trades (≥1k or ≥5k contracts), entering at the next real print | All categories, ≥1k contracts: follow −2.0 ±1.0 / −1.9 ±1.1, fade −2.3 ±1.0 / −1.5 ±1.1. Same picture at ≥5k. | **NO-GO** |
| Resolution-rule quirks (95c+ favourites that lose more than priced in the last 24h, by series) | None significant. The worst cases (K-League −31 ±25, 26 events; Chinese Super League −27 ±28) are a few upsets, not a rule. | **NO-GO** |
| Grid search (group × price × time-to-close × role, 1,200 cells) | 118 cells pass IS at t≥3, against about 1.6 expected by chance. But most flip in OOS, depend on look-ahead time-to-close, or come from a single event (e.g. NBA props +61c is one Finals game). The ones that hold are existing strategies: Elections favourites, the econ longshot fade (Economics "custom" maker 0-15c: +10.9/+11.6 OOS = macro_fade), and Politics one-off longshot fade (+2.9 OOS). | **No new GO** |
| Cross-market consistency | Not testable with this sample, which takes single markets rather than whole events. Bracket and ladder arbitrage were already ruled out. | Not tested |

## Side check: live Mentions NO rule

I replayed the live rule (NO 75-90c, 1-168h before the event, word volume ≥1k, bid rests until the event, no fee) on this independent sample. **IS +3.9c ±8.2 (100 fills) and OOS −2.8c ±6.8 (158 fills).** The samples are small and the CIs cross zero. The result matches the recent −3c reading and the "edge is shrinking" trend. No change now. The Dec 3 / 100-fill judgement remains the right gate.

## With more money or more tools

None of these leads gets better with a bigger bankroll, because they are about zero or negative per contract. The one structural pattern is that well-timed in-play sports favourite buying makes money for whoever does it. Doing that needs a live sports data feed (~$100-500/month), an always-on server, and sub-second execution against professional market makers. The earlier sportsbook-comparison test found no edge either, so I don't recommend it.

## Files

- `RESULTS_raw_trades.md`: all trade-level tables (category, price, sports type, time to close, hour, momentum, big trades, quirks, grid, volume).
- `RESULTS_raw_snapshot.md`: bot-snapshot favourite tests by category × band × window (taker, back-of-queue maker, touch maker).
- `RESULTS_raw_mentions.txt`: the Mentions rule replay.

## Follow-up: cross-market consistency (game winner vs spread), Nov 7 evening

The question: within one game, do the winner market and the "wins by more than X" spread markets ever disagree enough to lock in a profit after fees? I took 2,524 settled games across 12 leagues (NBA, NFL, MLB, NHL, NCAAF, NCAAM, WNBA, World Cup, La Liga, UCL, EPL, MLS; 2025-Nov 2026) and checked 1-minute bid/ask quotes over the last 6 hours before close (6.2M quote-minutes).

| Check | Games with any profitable gap | Gap-minutes | Games where it lasted 5+ min | Median gap |
|---|---|---|---|---|
| Spread bid > winner ask (same team) | 95 | 172 | 2 | 0.9c |
| Spread ladder out of order | 87 | 223 | 1 | 1.6c |
| Two exclusive outcomes, bids > $1 | 103 | 241 | 3 | 0.7c |

Across all 2,524 games, the gaps add up to about **$21 per contract of depth over 22 months**. Almost all of them last 1-2 minutes, mid-game, and a few cents of book depth is gone in seconds. Only 6 games had a gap that lasted 5 minutes. **NO-GO.** It would need an always-on bot watching every game second by second, and it would still make pennies.
