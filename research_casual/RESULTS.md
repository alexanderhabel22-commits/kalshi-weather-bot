# Where casual traders lose money (Nov 11, 2026)

Research only, plus one logging change to the paper market-making test. No real-money rule changed.

**Bottom line.** Casual traders (orders of $25 or less) lose about 3c per contract in every category, and
5-9c in Mentions, Companies, Health and Social markets. Orders over $1,000 roughly break even or win. Markets
where small orders make up most of the flow do pay the other side more, and the size of the effect rises
steadily with the casual share. But for one small resting order per market the edge is +2 to +4c if we are
first in line and about zero if we are at the back, with ranges that still cross zero in the recent period.
So this is a filter to add to what we already do (rest bids, NO side, wide spreads), not a new proven strategy.

## Data and method

- The saved whole-platform sample (branch `hist-research-data`, runs/20261107-s0..s7): 45,138 settled markets,
  24.0M trades, Jan 2025 - Nov 6 2026, up to 3 markets per series per month. IS = closed before Jul 1 2026,
  OOS = after.
- Trades are grouped into orders (same market, second and side). "Small" = order cost of $25 or less.
- A market's "casual share" = share of taker contracts in its FIRST 30 orders that came from small orders.
  Everything is then measured on LATER orders only, so there is no look-ahead.
- Returns are cents per contract. ± is a 95% range clustered by event. Taker returns are after the taker
  fee. Maker returns are before any maker fee (sports and some econ series charge one, up to 0.44c, and a
  1-contract order may round up to 1c).
- Copycat's own feed log only started Nov 11 (under a day), so the 22-month tape was used instead. It has
  every trade's size, which is all this test needs.

## 1. Who loses: taker return by order size (order-equal)

| Category | <=$5 | $5-25 | $25-100 | $100-1k | $1k+ |
|---|---|---|---|---|---|
| Sports IS / OOS | -3.3 / -3.1 | -3.1 / -3.1 | -1.9 / -2.4 | -0.1 / -0.6 | +2.6 / +0.5 |
| Mentions | -6.6 / -5.5 | -7.8 / -0.1 | -5.0 / +3.4 | -1.6 / +3.2 | -0.4 / n/a |
| Entertainment | -3.8 / -3.6 | -4.0 / -3.5 | -2.4 / -0.3 | -0.3 / +1.7 | 0.0 / +1.4 |
| Elections | -3.3 / -2.0 | -3.3 / -2.3 | -2.9 / -0.9 | -0.6 / +0.7 | +1.6 / +3.1 |
| Politics | -3.5 / -2.7 | -3.0 / -1.8 | -1.4 / +0.4 | +0.4 / +2.5 | +1.3 / +2.4 |
| Weather | -3.1 / -1.1 | -1.9 / -1.2 | -1.4 / -1.4 | 0.0 / -1.9 | +2.2 / n/a |
| Crypto | -4.7 / -2.1 | -3.1 / -1.9 | -2.0 / -1.5 | +0.1 / -1.2 | +0.7 / -1.0 |
| Economics | -3.8 / -0.8 | -3.4 / -1.6 | -2.6 / +1.3 | -0.6 / +4.6 | +1.4 / +7.0 |

Small orders, all history: Companies -8.8c, Social -8.8c, Transportation -8.2c, Mentions -6.4c, Health -6.3c,
World -5.9c, Entertainment -3.9c, Crypto -3.5c, Sports -3.2c, Weather -2.2c. Small orders buy YES 58-75% of the
time (75% in sports). The loss sits in 10-30c longshots: -5 to -10c on either side, in every group.

## 2. Do casual-heavy markets pay the other side more?

Maker return against every later order (order-equal within a market, market-equal across), by casual share:

| | <5% | 5-20% | 20-50% | 50%+ |
|---|---|---|---|---|
| Sports, first in line, IS | +1.0 | +1.6 | +2.7 | +3.4 ±1.1 |
| Sports, first in line, OOS | +0.1 | +0.6 | +2.0 | +3.1 ±1.1 |
| Sports, back of line, OOS | -3.1 | -3.2 | -0.8 | +1.0 ±1.3 |
| Mentions, first in line, OOS | n/a | -0.5 | +1.4 | +4.1 ±2.7 |
| Mentions, back of line, OOS | n/a | -6.1 | -2.2 | +1.1 ±3.6 |
| Entertainment, first in line, OOS | n/a | -0.2 | +0.6 | +3.1 ±2.5 |

"Back of line" counts a fill only when the price later trades through our level within an hour.
The pattern holds in both periods, so the answer is yes. Weather, Elections, Politics and Crypto show no such
gradient.

Where the casual-heavy markets are: weather highs (60-85% of sampled markets per city), SNL / Trump monthly /
MrBeast / Bernie Mentions, Netflix runner-up, UFC method of victory, NFL first touchdown, minor soccer leagues
(Liga MX, Saudi, K-League), Call of Duty, F1 and IndyCar races. Sports types: futures, period (quarter/half)
markets and props carry the most; totals, spreads and both-teams-to-score carry none.

## 3. The honest version: one first fill per market

A bot does not get the average of every order. It gets the first one. One first fill per market after the
30-order mark, more than an hour before close, maker buying the 50-90c side:

| Casual 50%+ | First in line IS | First in line OOS | Back of line IS | Back of line OOS |
|---|---|---|---|---|
| Buy NO from a YES buyer, all ex crypto | +3.8 ±2.0 | +2.4 ±2.8 | +1.9 ±2.2 | +0.4 ±3.0 |
| Buy YES from a NO buyer, all ex crypto | -4.6 ±3.7 | -4.6 ±4.8 | -6.6 ±3.8 | -6.3 ±4.9 |
| Buy NO, 50-70c only | +8.0 ±2.9 | +4.2 ±4.1 | +4.1 ±3.2 | +0.1 ±4.4 |
| Buy NO, sports only | +4.0 ±3.2 | +2.8 ±3.7 | +2.4 ±3.4 | +1.5 ±3.9 |
| Buy NO, ex sports | +3.8 ±2.6 | +2.0 ±4.4 | +1.6 ±2.8 | -0.7 ±4.7 |

Two things stand out. The side matters more than the casual share: casual money buys YES, and the people who
hit NO are the informed ones, so a resting bid should only ever be on the NO side. And the edge is real only
when we are first in line, which is possible only where spreads are wide. That is the same condition as the
late-day weather lows and the Mentions NO strategy.

## 4. Seasonality

- Entertainment has a real winter peak in both years: Dec (Spotify Wrapped, TIME Person of the Year, Google
  Year in Search: ~20M contracts each in Dec 2025), Jan (Oscar nominations), Feb (Super Bowl halftime: 113M
  contracts on the first song alone, plus Grammys), Mar (Oscars: 25M each for Picture and Actor).
- Mentions: volume ran 120-180M contracts a month in Jan-Jul 2026, then fell to 48M, 29M and 51M in Aug-Oct.
  Small orders per sampled market fell from 240 (Feb) to 33 (Oct), and small takers' loss shrank from 6-10c to
  about 1c. The casual money did leave. Last winter's Mentions volume came from NFL broadcast Mentions (21-25M
  in Dec-Jan), Trump speeches (54M in Feb) and NBA Mentions. Those return with the NFL playoffs and the State
  of the Union. Only one winter of data exists and the platform was growing fast then, so "it comes back" is
  likely but not proven.
- Weather: total volume did NOT fall (115M in Oct 2026, the highest month). It is spread over ten times more
  markets (about 3,300 in Jan vs 40,000 in Oct, mostly new cities and hourly markets). Small orders per market
  peaked in Jan 2026 (250) and were 80-100 in Sep-Oct. Small takers in weather stopped losing in Oct-Nov
  (+1.0c, +1.7c). So the thinner weather edge is dilution plus a sharper market, not football season.

## 5. How small traders did in last winter's surge markets (small YES buyers, after fees)

| Group | 10-30c | 30-50c | 50-90c | Events |
|---|---|---|---|---|
| Super Bowl novelty (first song, guests, performers) | -22.4 | -39.4 | -44.4 | 8-10 (one Super Bowl) |
| Year-end lists (Spotify, TIME, Google, Xmas song) | -18.9 | -35.6 | -36 ±40 | ~20 (two Decembers) |
| Sports-broadcast Mentions (NFL, NBA, NCAA) | -14.0 ±9.8 | -19 ±23 | -36 ±25 | 53-73 |
| Late-night / TV Mentions | -10 ±12 | -19.4 ±12 | -19 ±16 | 82-96 |
| Trump / politics Mentions | -11.3 ±5.1 | -2 ±14 | -5 ±17 | 137-161 |
| Awards (Oscars, Grammys, Globes) | +2 ±15 | -7 ±14 | -6 ±16 | 180-240 |
| Reality TV winners | 0 ±12 | -2 ±17 | +23 ±15 | 18-21 |

Super Bowl and year-end numbers are huge but rest on one or two occurrences, so treat them as a lead. Awards
and reality TV are priced about right: do not fade those.

## 6. Why not sports (short)

Sports is 59% of Kalshi volume and where most casual money goes, but it is also where the professionals are:
61% of the top-100 profits come from sports. We tested it four ways: against DraftKings closing lines (8,259
games, Kalshi is as accurate, taker -1.1 to -1.5c), late-game underdog fades (-3c), props (bids resting into
the game lose 6-29c), and winner-vs-spread gaps ($21 total in 22 months). Sports also charges makers a fee, and
once a game starts a resting order is picked off by faster traders with live data. Today's test adds one
opening: casual-heavy sports markets before the game starts (+2.8 ±3.7c OOS on the NO side, first in line).
That is a paper-test lead only.

## 7. What changed and what is next

- `wxbot/paperlab.py`: every paper maker fill now records the market's casual share and is scored at
  settlement, split by casual share and by side (`by_flow` in m15/paper-mm.json). No orders, read-only.
- Paper plan for each surge event: see CALENDAR.md.
- Not done: a quote-level replay of casual-heavy markets. It needs a new download job and the paper test
  answers the same question on live books within 1-2 weeks.

Scripts: load.py, feat.py (orders), a1-a6 (sections 1-3), c1-c3 (sections 4-5). Run `python load.py` in a
folder that holds a checkout of `hist-research-data` as `hist/`.
