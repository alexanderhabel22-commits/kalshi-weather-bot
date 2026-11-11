# Polymarket US vs Kalshi, all-market arbitrage scan (Nov 11, 2026 ~9 AM ET)

Snapshot of 30,000 open Polymarket US markets (mostly sports 76%, politics 16%) and 173,274 open
Kalshi markets, public data. Polymarket US prices come from each side's listed quote (best ask);
the two snapshots were taken up to ~20 minutes apart, so this is a screen, not a fill test.

## Game winners (NFL, NBA, NHL, men's/women's college basketball, college football)
- 1,249 Polymarket US game-winner markets; 325 matched to a Kalshi game market (541 team sides).
- **Real arbitrage after taker fees on both legs: 0.** The screen flagged 3, and all 3 were false
  matches where two different teams share the code "SDST" (San Diego State / South Dakota State).
- Prices agree closely: Kalshi mid vs Polymarket US ask differ by 1.5c at the median. Both venues
  run ~2c spreads on these games, and fees on both legs add ~3-4c, so a gap has to be 5c+ to pay.

## Fed December decision
Polymarket US vs Kalshi bid/ask: +25 bp hike 85/86 vs 84/86, +50 bp 10/11 vs 10/12, no change
5/6 vs 4/5. Within a cent; no arbitrage.

## Not comparable
Polymarket US's politics list is mostly past Nov 3 election margin markets; CPI October markets had
already settled; long-dated futures (champions, awards) were not matched (capital locked for months).

## Verdict
Slow markets are priced the same on both venues to within 1-2c, below the fees. Plain
"buy both sides" arbitrage isn't there in a snapshot. The open lead is still the BTC 15-min contract,
where prices move every second, tested with live books and a maker-leg-then-hedge approach.

Code: research_xv/dump.py (xvdump-research.yml), research_xv/match.py. Raw: branch
xv-research-data, runs/dump-20261111T1400Z.
