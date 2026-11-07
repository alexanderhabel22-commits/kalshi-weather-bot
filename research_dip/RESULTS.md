# Buy the panic dip: verdict (Nov 7, 2026)

**NO-GO.** Research only; nothing in the bot changed.

Data: exact Kalshi trade tapes + 1-minute bid/ask candles (dip-research-data branch) for
52k markets / 64k "crash" events (a side traded 56c+, then 15c or less), Jan 2025 - Nov 2026.
Categories: Mentions, Entertainment, Elections, Politics, Economics (incl. Fed/CPI).

## 1. Buying at the real ask after a crash (taker): loses everywhere
| setting | contracts | avg ask | won | P&L / contract after fee |
|---|---|---|---|---|
| ask <=5c, buy 5 min later | 38,388 | 1.7c | 0.9% | -1.9c (95% -2.0..-1.8) |
| ask <=10c, buy 5 min later | 40,315 | 3.5c | 2.2% | -2.3c (95% -2.4..-2.1) |
| ask <=15c, buy 5 min later | 41,934 | 5.8c | 3.8% | -3.0c (95% -3.2..-2.8) |
Every category and delay (1/5/30 min) is negative or within noise (Politics +0.5c, CI spans 0).

Why the coarse stop-loss test showed 22-36% wins: it counted any later bid under 10c.
Exact data: 19% of crash events still won, but almost all of those never had a real ask
under 10c (they won 47%). When the ask really is that cheap, the market is right.

## 2. Resting cheap bids after a crash (maker, catches panic sellers): edge died in July 2026
Bid 5c placed 5 min after the dip, filled only if a seller printed BELOW 5c (queue-safe),
held to settlement, non-Mentions (Mentions is ~0 at best), weighted to the full market universe:
| fills in | ROI on cost (95%) |
|---|---|
| Jan-Jun 2025 | +55% (+11..+102) |
| Jul-Dec 2025 | +101% (+67..+136) |
| Jan-Jun 2026 | +94% (+67..+125) |
| Jul-Oct 2026 | +9% (-25..+42) |
1-contract version Jul-Oct 2026: -15% (-43..+10). 8c bids and bigger sizes are negative recently
(big prints are informed sellers). Same decay pattern as the weather day-before edge.
Profit came from flash crashes (ask still 50c+ at the fill: 31% won) not real collapses
(ask <=15c at the fill: 5% won, break-even).

## Recommendation
- Do not buy crashes. Keep "diamond hands" (no stop-loss) as already decided.
- Optional, free: a silent paper census of 5c post-crash bids outside Mentions; re-test ~Feb 2027.
- With more money: no change; the recent maker edge is not significant at any size.

Scripts: research_dip/fetch.py (pull), analyze.py (taker), maker_by_period.py (maker by period).
