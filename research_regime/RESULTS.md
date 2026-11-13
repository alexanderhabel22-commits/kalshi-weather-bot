# Regime filters: do our own strategies have predictable winning and losing stretches? (Nov 13, 2026)

Question (Alex, Nov 13): not copying anyone, but using OUR data: some strategies lose over
time, but are there patterns we can see BEFORE a trade that separate the winning stretches
from the losing ones, so the bot trades only in the good stretches?

**Answer: no. Every split we found behaves like a coin flip on later data.** Nothing goes
live or to paper from this study.

## Data (22 strategy ledgers, ~57,500 replayed trades)

| Ledger | Source | Trades | Period |
|---|---|---|---|
| 15-min favourite bid, today's rule and "early" rule E, on BTC, silver, gold, ETH, SOL, copper, platinum, palladium, WTI (18 ledgers) | 1-min Kalshi candles, `hist15-research-data` | 50,688 | Jul 31 / Sep 16 - Nov 10 |
| Mentions NO, big-event favorites, Fed/CPI fade | the Nov 12 audit's bot-faithful replay (`research_main/replay.py`, `main-audit-data`), maker fee taken off | 1,749 / 645 / 186 filled orders | 2025 - Nov 11 2026 |
| Weather day-before | the Nov 11 weather pass's walk-forward replay (`research_wx/sim.py`) | 4,271 | Mar 1 - Nov 10 2026 |

Market-making and parlay paper books started Nov 11: two days is far too little to split.
The live 15-min fills (~900 contracts) are a subset of what the candle replay covers.

## Method (written before looking at results)

- Features, all known before the trade: price, minutes left, spread, volume so far, underlying
  volatility (last 2h), the underlying's 1h and 24h move with/against our side, size of the 1h
  move, hour, weekday; for the main bot hours to the event, volume, event volume, spread, joined
  vs improved the bid; for weather the forecast-vs-market gap, market price, forecast spread,
  high vs low. Plus the strategy's OWN form: average P&L of its last 20 and last 100 settled
  trades, whether the last one won, current losing streak, yesterday's P&L.
- Split by time: first 60% of each ledger = TRAIN, last 40% = HOLDOUT.
- Each feature cut at its TRAIN terciles, 6 filters each (keep low/mid/high, drop low/mid/high).
  **1,692 filter candidates in total** (1,427 15-min, 196 main bot, 69 weather).
- Pick per strategy: best TRAIN result among filters that keep >= 25% of trades and whose gap is
  t >= 2 (clustered by day or event). Then look at the HOLDOUT once.

## Results

**Do train-period patterns survive?**

| Group | Candidates | Train lift kept its sign on holdout | Train-significant (t>=2) | ...of those, still better on holdout | Avg holdout lift of the significant ones |
|---|---|---|---|---|---|
| 15-min (18 ledgers) | 1,427 | 52% | 29 | 48% | **-0.3c** (train said +2.3c) |
| Main bot (3) | 196 | 54% | 4 | 75% | +0.7c (train said +4.8c) |
| Weather day-before | 69 | 34% | 3 | 33% | -2.9c for the pick |

50% is what pure noise gives. About 2.5% of candidates should pass t >= 2 by luck alone, i.e.
~40 of 1,692; we got 36. So the "significant" patterns are about as many as chance makes, and
they don't hold up.

**The per-strategy picks on the holdout** (cents per contract, ± 1 s.e.):

- BTC early: 24h move not strongly with us -> +2.3 ± 1.5 vs +1.3 all. BTC today: -0.9 vs -0.1.
- Silver: spread > 1c -> +0.3 (today) / +5.1 ± 3.6 on 7% of trades (early). Noise.
- Gold: +0.1 vs +1.1. ETH, SOL: picks lose (-3.7, -7.1, -0.6).
- Copper / platinum / palladium replay +4-6c but their REAL fills were -4c and -18c: the candle
  replay can't fill thin books honestly, so these aren't usable regardless of filter.
- Mentions: no filter even passed on train. Favorites: orders placed after 5 PM ET +7.0 ± 1.3
  vs +5.7 all (inside noise). Fed/CPI: 75 holdout orders, too few to split.
- Weather: the best train filter (biggest forecast-vs-market gap) went from +10.3c to -0.1c.

**Streaks: does a good stretch predict the next one?** (switches.py, fixed textbook rules, no tuning)

- Correlation of one day's result with the next day's: 15-min -0.21 to +0.13 (mostly ~0 or
  slightly negative); Mentions +0.31 and weather +0.14 come from the same event / same weather
  system spanning two days, not from a switchable mood of the market.
- Correlation of the last-20-trade average with the next trade: -0.09 to +0.05 everywhere.
- "Trade only when the last 20 trades are up": helped on 9 of 22 ledgers, hurt on 12.
  "Yesterday was up": helped 10, hurt 11. "Pause after 3 losses": changes almost nothing.
  The contrarian version is just the mirror image. No consistent direction.

The one thing recent form does catch is SLOW DECAY: weather day-before's "last 100 up" switch
would have turned it mostly off from September (on +6.3c, off +0.6c). That is a trend, not an
on/off cycle, and the weekly allocator grade plus the review schedule already catch it.

## Verdict

- No regime filter clears +3c/contract on the holdout, or even beats doing nothing reliably.
  Nothing goes live. Nothing goes to paper either: a coin-flip filter on paper would just waste
  a slot.
- Why the losing strategies lose is not timing. On the 15-min markets it's fill quality and a
  fading average edge (pro bots in the last minutes); on Mentions the price is fair; on weather
  the market's forecast got better. None of those switch on and off.
- Where it could still be worth a look later: once the market-making and parlay paper books have
  ~4 weeks of fills, rerun `research_regime/regime.py` on them (same method, same holdout).

## Reproduce

```
python research_regime/ledger_m15.py <hist15 runs/20261111 dir> m15.pkl
python research_regime/run_m15.py m15.pkl m15_cands.pkl
python research_main/replay.py <main-audit run dir> mentions|favorites|macro main_<x>.pkl
python research_regime/run_main.py main_cands.pkl main_mentions.pkl main_favorites.pkl main_macro.pkl
python research_wx/build.py data.pkl <backtest-data backtest/> <wx-research-data run>; python research_wx/a2_resid.py data.pkl 36
python research_regime/ledger_wx.py data.pkl.rows36.pkl wx_ledger.pkl; python research_regime/run_wx.py wx_ledger.pkl wx_cands.pkl
python research_regime/switches.py <dir holding the ledgers>
```
Raw outputs: /mnt/project-files/analysis/regime-filters/*_out.txt
