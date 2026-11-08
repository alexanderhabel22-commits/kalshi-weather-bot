# Coverage map + gap tests (Nov 8, 2026)

Doc: https://claude.ai/artifact/A2W4SwVGdD1YZhV5pzZhjF (full table: every category x 6 strategy types)

Catalog: catalog_series.csv = all 15,884 Kalshi series (category, tags, lifetime volume,
`recent` = contracts Aug-Oct 2026 for series with 50k+ lifetime volume). Built from hist-research-data series.csv/volume.csv.

Gap tests (code research_gaps/ on branch claude/project-thread-v2e4xk; data branch gaps-research-data runs/20261108-{m15,gas,cry}):
1. 15-min commodities/FX/rates, maker longshot fade back-of-queue (Aug-Nov 2026, 3,404 markets):
   BTC control +7.3±2.3; silver+copper +5.2±2.5; all commodities ex gold +2.9±1.7; alt crypto +3.1±1.2;
   gold -0.9±4.2; FX -1.7±3.6; rates -6.5±7.8. Taker favourites ~0. NO-GO now (needs always-on bot); strengthens the tabled 15-min bot.
2. KXBTCD/KXETHD hourly vs spot lognormal model (Coinbase 1-min, 6h vol): Brier tie (market slightly better); trading gaps -2.5..+1.8c, noise. NO-GO.
3. Daily AAA gas (national + states) vs yesterday's price + momentum: market Brier 0.023-0.031 vs model 0.085; OOS trading -0.6..-5c. NO-GO.

Raw outputs: RESULTS_raw_m15.txt, RESULTS_raw_gas.txt, RESULTS_raw_cry.txt.
