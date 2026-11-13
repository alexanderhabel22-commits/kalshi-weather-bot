# Late-day weather on more cities (Nov 13, 2026)

## What changed on Kalshi
Kalshi now runs daily LOW markets in 23 US cities (we traded 7) and highs in 23
(we had 20). Every weather series now lists weather.com as its settlement source, so
our auto-discovery could no longer read the station and silently skipped the new
ones. The station is still named in the rules ("(CLIATL)" etc.). New series were added
to `wxbot/stations.py` by hand.

New lows: Atlanta, Boston, Dallas, DC, Houston (Hobby), Las Vegas, Minneapolis,
New Orleans, Oklahoma City, Phoenix, San Diego, San Antonio, Seattle, San Francisco,
Newark, Trenton. New highs: Newark, Trenton, San Diego.

## Replay (live rule: first scan 12h..5h before close, spread 4c+, rest 1c inside, hold)
Kalshi hourly candles + IEM station readings, Aug 20 - Nov 12 2026 (fetch:
`research_wx/fetch_cities.py`, analysis: `a7_lows.py` then `a12_cities_rule.py`).
Cents per filled contract, ± 1 s.e. clustered by day.

| Group | Sep 1 - Nov 12 | Oct 15 - Nov 12 | trade-through only |
|---|---|---|---|
| 7 live low cities, need NO (control) | +6.6c ±1.4 (262) | +6.4 ±2.1 | |
| 16 NEW low cities, need NO | **+3.9c ±0.6 (932)** | +4.3 ±1.0 (390) | +3.3 ±0.7 |
| 16 NEW low cities, holds YES | **+4.2c ±1.5 (408)** | +6.1 ±2.3 | +3.2 ±1.6 |
| 3 NEW high cities, need NO | -2.7c ±1.8 (266) | -3.3 | |
| 3 NEW high cities, holds YES | +1.6c ±3.7 (141) | | |
| 20 OLD high cities, need NO (live since Nov 12) | +0.8c ±0.6 (1,621) | +0.8 ±1.2 | +0.2 |
| 20 OLD high cities, holds YES (live since Nov 12) | +3.9c ±1.2 (840) | | +2.8 |

New lows by month (need): Sep +4.2, Oct +3.8, Nov (11 days) +3.1. 14 of 16 cities
positive; Houston (-0.9), New Orleans (-0.2), Trenton (-0.1), Seattle (+0.5) flat.
About 14 extra need fills and 6 holds fills a day at 1 contract, ~+$0.55/day.

## Decision (Claude, under Alex's Nov 13 OK to start confident ideas with real money)
- 16 new low cities go REAL at 1 contract (need + holds), same caps and stops.
- Highs "need" back to paper: ~0 edge and it was using about half of the 15% cap.
- The 3 new high series stay paper.
- The 15% cap (~$7.80) was already full on Nov 12 (66 paper orders, 15 real). Raising it
  is Alex's call.

## Sports check (asked again)
Pre-game favorites on Kalshi game markets (NBA/NFL/NHL/MLB/college, 2026, taker at the
ask incl. fee): 85-96c -0.6c ±1.0 (927 games, 1h before); 90-96c +1.4c ±1.0; 96-99c
+1.3c ±0.6. A small favorite bias, below fees + noise for a real test. Script:
`research_sports_fav/fav.py` on branch sports-research-data files.
