# Unorthodox ideas, round 2 (Nov 7, 2026): verdicts

Research only; nothing in the bot changed.

| idea | data | result | verdict |
|---|---|---|---|
| Ladder arbitrage (strike ladder priced inconsistently, e.g. ">0.8%" cheaper than ">0.9%") | 1.2M same-hour strike pairs, econ series, hourly quotes | 0 arbs after fees | DEAD |
| Late sniping (taker buys 85-98c in last hour) | cat/fin trade aggregates 2025-26 | -1 to -4c/contract in Entertainment, Economics, Politics; Tech/Elections small + on few markets | NO-GO |
| Time of day | 2.9M non-sports trades Oct-Nov 2026 | makers earn +0.6..+1.3c in the day, +1.5..+3.3c overnight/weekend; takers lose everywhere | tweak only |
| TSA weekly average nowcast (TSA posts daily counts each morning) | 3,073 KXTSAW markets, hourly quotes, TSA daily 2024-26 | model sd fit on 2025, tested on 2026: about 0c/contract overall; Sunday (6 of 7 days known) +7.5c on n=52 (SE ~6c); 2025 Sunday +27c (in-sample) | weak lead, NO-GO for money |
| Netflix / Spotify / Billboard nowcasts | 13k markets pulled | FlixPatrol (daily Netflix leak data) blocks bots (403); Netflix's own TSV is the settlement source, not a leak | not testable for free |

TSA detail (2026 out of sample, buy at ask when model edge >= 8c after fee):
days known 0-2: +1.0c (n=306); 3-5: -2.4c (n=218); 6 (Sunday): +7.5c (n=52).
Market Brier vs model: model better at 0 and 5-6 days known, worse at 1-4.
Same decay pattern as weather: big in 2025, small in 2026.

Next: optional silent paper test of Sunday TSA trades; needs no money.
Data: nowcast-research-data branch; scripts research_nowcast/.
