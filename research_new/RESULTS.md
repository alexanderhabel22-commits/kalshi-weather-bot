# New angles, Nov 13 2026: daily rain and hourly temperature markets

Two Kalshi market families no strategy of ours had touched, both high frequency.
Research only, except the one rule that cleared the bar (rain_evening, below).
Scripts and notes: project files `analysis/new-angles/`. Returns are cents per
contract after the taker fee (0.07·p·(1−p), rounded up to the cent, 1 contract);
± is a 95% interval. No look-ahead: every decision uses only prices and station
reports available at that time (station reports at least 5 minutes old).

## Data
- KXRAIN ("Will it rain in <city> today?", >0 inches, Weather Company data, closes
  at local midnight or early once it rains): 3,012 settled markets in 35 cities,
  Jul 16 - Nov 12 2026, ~28k contracts traded per market. Hourly bid/ask candles for
  all live markets, plus the exact trade tape after 5 PM local for the 349 city-days
  that were still dry then.
- Hourly temperature (KXTEMPMIAH, NYC, NYC metro, LA, LA coastal; Kalshi Weather
  Index, settles on the top-of-hour index): 9,235 markets, Oct 30 - Nov 13, 1-minute
  bid/ask candles.
- Airport reports from the Iowa Environmental Mesonet: hourly METAR rain (35
  stations), 1-minute temperatures for the 5 Miami index airports, GFS MOS
  rain chances (6-hour PoP).
- The airport's METAR rain matches the settled KXRAIN result on 98.6% of
  city-days (Chicago is O'Hare, 98%; Houston Hobby 96%, left out).

## Verdicts
| Angle | Result | Verdict |
|---|---|---|
| Rain: morning forecast fade (GFS MOS PoP vs price at 9 AM local, fit Jul-Sep, test Oct-Nov) | market Brier 0.044 vs model 0.061; trades -3 to -5c | NO |
| Rain: buy YES after the airport reports rain (lag) | markets close early when it rains; only 41 chances, noisy | NO |
| Rain: NO when dry so far, by time of day (hourly candles) | ~0 before 6 PM local; positive late | lead |
| **Rain: NO at the next real print after 8 PM local, dry so far, NO 75-95c** | **+6.1c ±3.9, 109 trades (Jul-Sep +6.3, Oct-Nov +5.9); 9 PM: +6.3 ±4.1 (94); 7 PM: +4.0 ±4.2** | **1-ct REAL test** |
| Rain: same, resting NO 1c inside the offer | +8.5c ±11 on only 29 fills | too few fills |
| Hourly temp: buy the favourite (60-99c) at 3-45 min left | -0.1 to -9c in every cell (n≈200-600 each) | NO |
| Hourly temp: Miami nowcast from the 5 index airports (IEM, whole °F) | model Brier ~0.10 vs market 0.033; trades -4 to -7c | NO |

Hourly temperature is a speed-and-data game: the index is built from Synoptic's
paid 1-minute feed in tenths of a degree, and the market already prices it to
within a few cents. Our free data (whole-°F, 14+ hours late at IEM; whole-°C at
NWS) can't compete. A paid Synoptic feed is the only way in; not costed.

## rain_evening (real money, 1 contract, from Nov 13)
Rule: between 8 and 10 PM local, if today's KXRAIN market for the city is open, the
airport's METAR/SPECI reports since local midnight show no nonzero P group (and at
most 2 hourly reports are missing), and NO costs 75-95c, buy 1 NO at the ask. Hold.
About 1 trade a day across 34 cities; wins are 5-25c, a loss is 75-95c.
Caveats: the 8 PM start was picked after looking at 5 PM-9 PM (7 PM was +4.0), so
the true edge is likely below +6c; and the sample is one summer/fall. Reviews at
100/300/500 contracts.
