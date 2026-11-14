# Overpriced middle brackets beyond the S&P (Nov 14, 2026)

Question: the live `spx_range` rule (sell YES at 35c on S&P daily-close brackets before the open) works because the index is frozen overnight and a 25-pt bracket can't be worth more than ~15-20%. Does the same rule work on other bracket markets?

Method: same as research_spx/mk.py. Rest an offer selling 1 YES at L; count a fill only if a later trade printed ABOVE L in the window (back of queue); 1 fill max per bracket; 1c fee; P&L = L - outcome - 1c. Clustered by day. Kalshi data: /historical/trades (Jan 2025 - Sep 15 2026) + live hourly/1-min candlesticks (Sep 8 - Nov 14 2026).

## Result 1: Nasdaq-100 daily brackets (KXNASDAQ100), before the open: YES

Window: 4 PM ET prior close to 9 AM ET (7-23.5 h before the 4 PM close). 100-pt brackets.

History Jan 2025 - Sep 15 2026 (426 days, 4,573 brackets):

| L | fills | days | lose | per fill | $/day |
|---|---|---|---|---|---|
| 25c | 291 | 147 | 14% | +10.3c ±1.9 | +0.20 |
| 30c | 197 | 113 | 13% | +16.3c ±2.5 | +0.28 |
| **35c** | **138** | **79** | **12%** | **+22.4c ±3.2** | **+0.39** |
| 40c | 70 | 50 | 14% | +24.7c ±4.3 | +0.35 |

Recent Sep 8 - Nov 13 2026 (candles, 49 days): 35c 25 fills, 1 loser, +30.0c ±3.7.
Every period positive at 30c and 35c (2025 H1 +12/+34c on few fills, 2025 H2 +17/+20, 2026 Jan-Apr +19/+24, May-Aug +12/+17, Sep-Nov +20/+34).
Control: S&P (KXINX) through the same candle code, recent 49 days: 35c +12.3c ±8 (matches the original +12.8c).
Same window AFTER the open (0-6.5 h before close): -3 to -6c per fill. The edge is only while the index is shut.

Size: ~7 fills/month at 35c -> ~$1.60/month at 1 contract (~$6/mo at 4 ct). Each fill risks 65c and loses ~1 in 8.

## Result 2: FX daily brackets (EUR/USD, USD/JPY; 10 AM ET close): NO
Recent 47 days. EUR/USD 12-24 h out: +5 to +10c but ±5-11 on 15-58 fills; 3-12 h out -6 to +4c; last 3 h -11 to -15c. USD/JPY: almost no fills 12-24 h out, 3-12 h out -8 to -14c. FX trades 24h, so there is no frozen window.

## Result 3: BTC / ETH hourly brackets: NO (clear loss)
538 hourly events each, Sep 7 - Nov 14 2026 (every 3rd hour), 1-min candles.
BTC 30-60 min before close: -1.3 to +3c (noise); 15-30 min -2 to -5c; 5-15 min -6 to -9c.
ETH: -3 to -16c everywhere. Price moves INTO the bracket are what fill you (adverse selection).

## Also checked
- WTI daily (KXWTI): only above/below strikes, no brackets. Skipped.
- ERCOT peak demand daily: volume is ~1 contract per market now. Dead.

## Takeaway
The edge is "bracket priced far above its maximum fair value while the underlying cannot move". Nasdaq pre-open is the same mechanism as spx_range and is stronger. Anything that trades 24h (FX, crypto) fails.

Recommendation: `ndx_range` = copy of spx_range on KXNASDAQ100, 1 contract REAL, sell YES 35c on the ~6 likeliest brackets 4:05 PM - 8:30 AM ET, cancel 8:45 AM. Needs Alex's named OK.
