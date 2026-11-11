# Polymarket 15-min crypto: verdict (Nov 10, 2026, research only)

**NO-GO for our 15-min favourite bid.** On Polymarket's own history it loses or breaks even on
every coin, while the same rule earns about +7c per contract on Kalshi BTC.

## Can a US account trade it?
- Yes, but only on **Polymarket US** (QCX LLC d/b/a Polymarket US, a CFTC-regulated exchange,
  iPhone app + retail API). The international polymarket.com is not for US traders.
- 15-min Up/Down listed on Polymarket US: **BTC** (live since ~Sep 28, 2026), **gold and silver**
  (added Oct 22, 2026). No ETH/SOL/XRP 15-min seen in the docs.
- BTC settles on the CF Benchmarks BRTI 60-second average (the same index family as Kalshi BTC 15m).
  Gold/silver settle on Chainlink spot.
- State availability: not confirmed in official docs (a secondary source says ~36 states).

## Fees (docs.polymarket.us/fees.md, from Oct 28, 2026)
- Taker: Θ × contracts × p × (1−p). Θ = 0.095 under $10k 30-day volume (0.0695 in the first 7 days,
  and at $250k+). At 80c that is ~1.5c per contract.
- **Maker: 0.** Maker rebates only from $10M volume. (Kalshi charges makers on 15-min markets.)
- International site (for reference): taker 0.07 × p × (1−p), makers free.

## Funding
Debit card, bank transfer (ACH via Aeropay), Apple Pay, PayPal, Venmo, USDC on iPhone, wire ($5k min).
No deposit/withdrawal fee stated. ID verification (KYC) before trading or API keys.

## API
Trading: api.polymarket.us/v1, Ed25519 keys from polymarket.us/developer, ~20 requests/s, batch orders.
Public data: gateway.polymarket.us/v1 (markets, book, best bid/offer, book-derived price history).
**No public trade history on Polymarket US**, so the replay below uses the international book.

## Replay (international book, Jun 14 - Nov 10, 2026)
23,507 random 15-min windows (3,017 each for BTC, ETH, SOL, XRP, DOGE, BNB, HYPE; fewer for ZEC,
gold, silver). Data API v2 taker trades. Rule = our Kalshi rule: rest a maker bid 1c under the
favourite's last price while it is 71-95c, re-joining after every trade; filled only when a taker
sells that side below our bid ("back of queue"). "At price" also counts trades at our bid.

| Last 10 min, per contract | back of queue | at price | taker entry (fee paid) |
|---|---|---|---|
| BTC | -1.1c ±1.5 | +0.3c ±1.5 | -1.3c ±1.5 |
| ETH | -3.1c ±1.5 | -2.1c ±1.5 | -2.2c ±1.5 |
| SOL | -3.6c ±1.6 | -2.8c ±1.5 | -1.5c ±1.5 |
| XRP | -4.5c ±1.7 | -3.5c ±1.6 | -2.0c ±1.5 |
| DOGE / BNB / HYPE | -7c / -7c / -12c | | |
| gold / silver (only ~200 windows) | -9c / -9c (noisy) | | |
| All coins | -4.8c ±0.7 (16,323 fills) | -3.8c | -2.1c |

- Every price band 70-95c is negative (-4 to -6c). BTC by month: +1.7, -1.0, -1.8, +1.1, -2.4,
  -6.0c (Jun-Nov), so no stretch where it worked.
- Last 5 min only (the alt-coin rule): -5.5c overall, BTC -2.4c.
- Mirror test, bidding the UNDERDOG at 5-29c instead: also negative (-4.0c overall, BTC -2.2c).
  Both sides losing as a maker means fast traders pick off resting bids; the prices are fair.
- Depth: median taker dollars traded in the last 10 min per window: BTC $9.7k, ETH $1.7k,
  SOL $660, XRP $490, others < $200.

## Cost and break-even
- Cost to try: $0 extra. No account fee, makers pay no fee, and the bot could run on the existing
  $8/mo droplet. A separate server would be ~$4-8/mo.
- Break-even: none. At -1c per contract on BTC (best case), more volume only loses more.
  For reference, if a real +2c edge existed, $8/mo would need ~400 contracts a month.

## Why Kalshi differs
Polymarket's 15-min books are much busier (BTC ~$10k of takers in the last 10 min) and full of
fast market makers, so favourites are priced fairly. Kalshi's thinner books still overprice
longshots. The Polymarket US book is separate and may be thinner than the international one,
but it has no public trade history to test.

Code: research_poly/fetch.py (runs in GitHub Actions, poly-research.yml), research_poly/analyze.py.
Raw rows: branch poly-research-data, runs/20261111T0045Z/rows.csv.
