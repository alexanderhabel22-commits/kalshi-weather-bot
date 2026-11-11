# Weather optimization pass (Nov 11, 2026)

Question (Alex): take a good crack at optimizing the weather bot and the information it
pulls. Data: 13 months of settled Kalshi daily high/low markets with hourly prices
(8,115 city-days, Oct 2025 - Nov 3 2026), the 4 forecast models the bot uses, NWS
station guidance (National Blend, GFS MOS), and hourly station readings.
Scripts are in this folder; every number below is reproducible from them.

## 1. The day-before strategy has no edge left (moved back to paper)

**The market's own forecast is now better than ours.** Turning the bracket prices
into one implied temperature and scoring it like a forecast (36 hours before close,
average miss in degrees F, highs):

| Source | Oct 25 - Feb 26 | Mar - Aug 26 | Sep - Nov 26 |
|---|---|---|---|
| Kalshi prices | 1.69 | 1.64 | **1.31** |
| Our 4-model mix (the live recipe) | about 1.9 | 1.66 | 1.60 |
| Best single model (ICON) | 1.79 | 1.98 | 1.80 |
| National Blend (NBS) | 2.09 | 2.10 | 1.88 |
| GFS MOS | 2.20 | 2.20 | 2.04 |
| ECMWF | 2.21 | 2.22 | 2.09 |

Until about July the market was only as good as our mix, which is why the strategy
used to backtest at +7.5c per contract. Since September the market has been about 20%
more accurate than our mix (lows: 1.49 vs about 1.9).

**How much do our forecasts know that the price doesn't?** Slope of
(actual - market) on (forecast mix - market); 0 means the price already holds
everything the forecast knows:

| When | Oct 25 - Feb 26 | Mar - Aug 26 | Sep - Nov 26 |
|---|---|---|---|
| 36h before close | 0.54 ± 0.08 | 0.39 ± 0.05 | 0.13 ± 0.05 |
| 24h before close | 0.17 ± 0.07 | 0.07 ± 0.03 | 0.02 ± 0.04 |

**Replay of the live recipe** (resting order 1c inside the spread, 6c edge):
Jan-Aug 2026 +7.5c ± 0.7 per contract; Sep-Nov 2026 +2.3c ± 1.2 (and the forecast
archive is a little fresher than what the bot really has at order time, so this
flatters it). Order lifetime (1h to until-close), depth inside the spread and the edge
threshold make no real difference.

**Live results agree.** Paper since Nov 5: 162 fills, -2.0c ± 3.3 per contract.
Real money Nov 5-11: 43 contracts, -$3.95. Of the 208 graded paper orders, all 46 that
never filled would have won: the price moves away from the good orders and only the
bad ones get hit.

**Decision (Nov 11):** weather day-before goes back to paper (`[trading.weather]
live = false`). It keeps logging so the Jan-Feb re-check costs nothing.

## 2. New lead, now on paper: late-day orders on LOW markets

From about noon on, the station has usually recorded its low for the day. The market
then prices the chance that the evening gets colder than the morning before midnight.
It pays too much for that. Rule tested: once between 12 and 6 hours before close, on
every bracket below the low so far whose spread is 4c or wider, rest a NO buy 1c
inside the offer and hold to settlement. No forecast needed.

| Period | Fills | Per contract | Fills/day | $ per month per contract |
|---|---|---|---|---|
| Oct 2025 - Aug 2026 | 1,964 | **+5.7c ± 0.6** | 7.5 | about +$13 |
| Sep - Nov 2026 | 495 | **+3.7c ± 1.2** | 7.7 | about +$9 |

- Positive in 11 of 12 months (Aug 2026 was -1.6c), and in all 7 cities over the long period.
- Holds when only counting orders that a trade went straight through (+4.9c / +4.0c).
- Lives in wide spreads: spread 8c+ earned +10c / +6.6c; spreads of 3c or less earned nothing.
- Risk at 1 contract per order: average +$0.41 a day, worst day -$3.10, 27% losing
  days, 85% winning weeks, worst week -$1.64. Each order risks about 74c to make about 26c.
- Buying YES on the bracket that holds the low so far is also positive but noisier
  (+4.4c ± 1.2, then +2.2c ± 2.5).
- Highs show the same thing only in the cool season (Sep-Oct +3c, Mar-Aug about 0).

Caveats: hourly price candles, not the full order book; a fill is assumed when a later
trade printed at our price (our order would be the best offer). Competing bots can
step in front of us, which the backtest can't show. Only 7 cities have low markets, so
this is a few dollars a month per contract, not a big number.

**Status:** paper test live in the main scan (`wxbot/latepaper.py`,
`data/paper_late.csv`, section in REPORT.md), lows and highs, both order kinds.
**Real money since Nov 11 (Alex's OK):** strategy `weather_late` trades the lows
"need" orders at 1 contract (15% of bankroll cap, own stop at -10% of bankroll).
Highs and the "holds" orders stay on paper. Reviews at 100/300/500 contracts.

**Out of sample (added Nov 11 evening):** the new download added Nov 4-9, days the rule
was never fit on: 53 fills, **+6.9c ± 1.2**, every one of the 6 days positive (+$3.64 at
1 contract). A forecast skip filter was also tested (skip when the day-old forecast says
the low reaches the bracket): it HURTS. Those nights earned +8.3c / +2.7c, and +11c / +14c
when the forecast was 2F+ inside the bracket; skipping them cuts the total from $115 to
$49 and from $18 to $14. The market overreacts to forecast cold fronts. Not added.

## 3. Better forecast sources: none beats the market

Download of Nov 11 (`runs/20261111-2021` on branch `wx-research-data`): about 20 extra
Open-Meteo models (NBM, HRRR, NAM, GFS 13 km, GraphCast, AI-GFS, ECMWF 9 km and AIFS,
UKMO, Meteo-France, JMA, GEM incl. HRDPS, ICON, KMA, CMA), Kalshi markets Oct 25 -
Nov 11, readings, and NWS guidance. The Open-Meteo part stopped after two stations (an
empty reply, likely the rate limit), so the model comparison covers **NYC and Chicago
only** (highs and lows). Script: `research_wx/a11_models.py`.

Day-old runs (`previous_day1`), each model bias-corrected walk-forward, scored on the
same city-days as the market's own implied temperature, Sep-Nov 2026:

| Source | 36h before close MAE | slope | 24h before close MAE | slope |
|---|---|---|---|---|
| **Market price** | **1.27F** | | **1.07F** | |
| Mean of all models | 1.39 | +0.30 ± 0.09 | 1.38 | +0.10 ± 0.08 |
| NBM | 1.60 | +0.03 | 1.59 | -0.01 |
| ICON | 1.67 | +0.18 | 1.70 | +0.09 |
| AI-GFS | 1.74 | -0.01 | 1.76 | -0.06 |
| ECMWF AIFS | 1.78 | +0.06 | 1.76 | +0.03 |
| ECMWF 9 km | 1.86 | +0.03 | 1.87 | -0.00 |
| HRRR | 2.00 | +0.07 | 1.95 | +0.03 |
| GFS 13 km | 2.08 | +0.03 | 2.11 | -0.02 |

(slope = how much of the gap between a source and the market turns out to be right;
0 means the market already has it.)

- **No single model beats the market** at either time, in any of the three periods since
  Mar 2026. The best (NBM, ICON) are still 0.3-0.5F worse.
- The mean of ~20 models keeps a little information at 36h (slope 0.30), but that is
  exactly where the archive's day-old runs may include runs not yet out at order time,
  and by 24h it is gone. Our 4-model day-before replay had slope 0.13 and lost money
  live, so this is not enough to bring day-before back.
- Mar-Aug 2026, ECMWF's AI model (AIFS) matched the market (1.70 vs 1.73F, slope 0.55):
  then the market caught up. Same story as section 1.
- Look-ahead check: Open-Meteo's single-run API only keeps runs from about Aug 2026 on
  (Mar 2026 and Oct 2025 runs are gone), so a clean as-issued test is possible only for
  Aug-Nov. Not worth it given the above.

**Verdict:** more or fancier models won't rescue day-before weather. The edge left in
weather is the late-day pattern in section 2, which needs no forecast.
