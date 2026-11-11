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

## 3. Better forecast sources

Download running (about 20 more models from Open-Meteo incl. HRRR, NAM, NBM, ECMWF
9 km, AIFS, GraphCast, UKMO; NWS guidance NBE, LAMP, NAM/ECMWF MOS). Results will be
added here.
