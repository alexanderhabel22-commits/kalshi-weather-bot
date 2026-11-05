# How far can the strategies scale? (Nov 5, 2026)

Research only. Data: research_scale/probe.py, run on GitHub Nov 5 (branch
scale-research-data, runs/20261105T2019Z). It looked at every market that settled
Oct 8 - Nov 5, 2026 for each strategy (450 sampled per strategy, all trades), and a
live order-book snapshot of open markets.

"Pool" = contracts that resting orders on OUR side, in OUR price band, actually got
filled for. "Through" = the part where the price traded past the level within an hour,
so even an order at the back of the queue was filled.

## What the market can absorb

| Strategy | Markets / week | Through-pool / week | Concentration | Our fair share* |
|---|---|---|---|---|
| Mentions NO 75-90c | 907 (56 events) | ~930k contracts | top 10% of markets = 76% of pool | ~20-40k / week |
| Macro fade (Fed/CPI/PCE) | 16 (1.5 events) | ~450k | 10 of 65 markets hold all of it | ~1-4k / week |
| Big-event favorites 85-96c | 124 (19.5 events)** | ~12.8M** | top 10% = 90% | ~8k / week normal, far more in election weeks |
| Weather day-before | ~170 orders/week the model picks (of 2,014 brackets) | 2.2M all brackets | spread out | ~4-9k / week |
| Hourly temp market making | 3,911 (657 events) | 4.3M | spread out | big, but no edge |

\* Taking at most 10% of each market's through-pool, with 50-1,000 contracts per
market. Past ~10% we become the price and informed traders pick us off; that number is
a judgment call, not measured.
\** The window includes the Nov 3 US election. A normal month has ~15 qualifying events
(317 over 21 months), so divide by about 5.

Order books: in Mentions and favorites, the median open market has **no** resting
bids in our band, so we'd usually be first in line (good for fills, but it also means
we set the price). Weather brackets have ~2,400 contracts of resting bids each, so we
compete for queue position there.

## Realistic $/month by bankroll

Edges used (haircut from backtests because they're shrinking and unproven live):
Mentions 3c, macro 3c, favorites 2.5c, weather 1.3c (Sep-Oct level; Jan-Aug was 7.9c),
hourly MM 0. Bankroll limit uses each strategy's open cap, typical holding time
(Mentions ~1 week, macro ~2 weeks, favorites ~4 days, weather ~1.5 days) and ~50% of
resting orders filling.

| Strategy | $37 | $500 | $5k | $50k | Market ceiling |
|---|---|---|---|---|---|
| Mentions NO | ~$1-2 | ~$15-20 | ~$150-200 | ~$600-1,800 | ~$2-5k/mo, edge degrades first |
| Macro fade | <$1 | ~$5 | ~$50 | ~$150-500 | ~$500/mo; few events, big tail risk |
| Big-event favorites | ~$1 | ~$15 | ~$150-170 | ~$600-900 | ~$900/mo normal months, more in election seasons |
| Weather day-before | ~$5-10 | ~$100-200 | ~$200 (capped) | ~$200 (capped) | ~$200/mo at today's edge; $1-2.5k/mo if the Jan-Aug edge returns |
| Hourly MM | $0 | $0 | $0 | $0 | break-even |
| **All together** | **~$5-15** | **~$130-240** | **~$550-650** | **~$1.5-3.5k** | |

Reading it: at $37 and $500 the bankroll is the limit, so returns grow with money.
Somewhere around $5-10k the markets start being the limit (weather first, then macro).
By $50k we'd be 5-10% of all the flow we trade in, returns per dollar fall to ~3-7% a
month, and our own orders would likely eat a good part of the edge.

## Candid caveats

- None of these edges has a single real-money fill yet. All numbers are backtests
  or paper. The live result could be zero or negative.
- Mentions and weather edges are measurably shrinking. Mentions Nov 1-5 was -3c.
- Macro and favorites lose 70-96c per contract when wrong; one surprise hits a whole
  event. Their monthly results will swing a lot.
- Fill assumptions (10% share, 50% fill rate) are judgment calls. The paper fills
  over the next 3-4 weeks are the first real check.
- Kalshi position limits per market are not binding below ~$50k.
