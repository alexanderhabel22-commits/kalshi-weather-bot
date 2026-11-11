# Kalshi vs Polymarket 15-min BTC, overnight log (Nov 10 8 PM - Nov 11 1:45 AM ET)

Research only, public read-only endpoints from GitHub Actions. 23 BTC windows, quotes every ~3 s.
Silver: Polymarket US silver 15-min markets were not found under the documented slug, so silver
was not compared.

## Findings
1. **Same contract on both venues.** Polymarket US's "price to beat" equalled Kalshi's strike in all
   22 settled windows, the settlement values were identical (for example 73,326.19 both), and the
   outcome agreed 22/22. Any price gap between the two is a true arbitrage: buy Up on one venue and
   Down on the other for under $1, and one leg must pay $1.
2. **Gaps look large, but the data can't prove they're real.** Using the quotes as logged, buying both
   legs as a taker cleared fees in 1,198 of 4,772 snapshots across all 22 windows (559 by more than
   3c). The free public endpoints are cached, though: Kalshi's REST quote changed only every ~12 s
   and Polymarket US's every ~30 s (its last-trade stamp was ~16 s old at the median). Most "gaps"
   are one venue's stale snapshot. In snapshots where Polymarket US's quote had just changed, with
   more than a minute left and prices 10-90c, 64 snapshots in 18 windows still showed more than 2c
   after fees, but Kalshi's side may be up to 12 s old there.
3. **Lead/lag is confounded the same way.** Kalshi appears to follow the international Polymarket
   book (closes ~39% of the gap each step), but the Kalshi quotes here were cached, so this needs
   Kalshi's live websocket to confirm.
4. Typical mid-price gap: 2c median, 9c at the 90th percentile (Polymarket US vs Kalshi).

## What a real test needs
- Live books on both sides at the same moment. Kalshi: our server already has the live websocket.
  Polymarket US: its live order-book websocket needs an account with ID verification and an API key
  (free; no deposit is needed to read data). Only Alex can open that account.
- Fees to clear per pair (taker both legs, mid-priced): Kalshi ~1.75c + Polymarket US ~2.4c at 50c,
  ~1.2c + 1.5c at 80c. Making one leg as a maker removes Polymarket's fee (makers pay 0).
- Capital: each pair ties up ~$1 per contract for up to 15 minutes, split across two venues.

Code: research_xv/log.py (xv-research.yml), research_xv/analyze.py. Raw: branch xv-research-data,
runs/20261111T0642Z.
