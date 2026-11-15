# Late-day weather filters (Nov 15, 2026)

Question (Alex, Nov 15 1:30 AM ET): can the weather bets be made better? Rule for tonight:
only filters that skip or delay orders, and only if they clear a strict bar (clear gain in
cents per contract beyond the error bars, holding on a time-split holdout).

Data: 13 months of Kalshi hourly candles (backtest-data + wx-research-data run
20261111-2021), 7 low cities and 20 high cities, Oct 2025 - Nov 11 2026, IEM station
readings. Scripts: `research_wx/filters/` (rec.py builds every chance 12h..4h before
close with trend features; s1 = slices, s2 = policies, s3 = day-block bootstrap of the
difference). Policies keep the live mechanics: one order per bracket, written at the
first scan where it passes, so a skipped bracket can still be written later.
Dev period Oct 2025 - Aug 2026, holdout Sep - Nov 2026. Cents per filled contract,
± 1 s.e. clustered by day.

## What passed

### 1. Highs "holds": wait while the temperature is still rising
Buy YES on the bracket holding today's high only once the last hour's rise is under 1F.
This is the afternoon-warming loss (NYC/Dallas/Austin/San Antonio highs on Nov 14).

| | Oct25-Aug26 | Sep-Nov26 (holdout) |
|---|---|---|
| live rule | +0.5c ±0.6 (3,906) $+18 | +2.2c ±1.1 (945) $+21 |
| wait until not rising | **+5.0c ±0.8** (2,357) $+117 | **+4.7c ±1.6** (557) $+26 |
| difference (95% day bootstrap) | +4.5c [+3.4, +5.5] | +2.5c [+0.5, +4.3] |

Live paper Nov 11-14 can't test it yet (trend wasn't logged; it is now, column `trend`).

### 2. Lows "need": require an 8c+ spread (was 4c)
Known from the Nov 11 study (the edge lives in wide spreads); not newly tuned.

| | Oct25-Aug26 | Sep-Nov26 (holdout) |
|---|---|---|
| live rule (4c+) | +5.0c ±0.6 (1,991) $+100 | +3.2c ±1.0 (548) $+18 |
| 8c+ spread | **+9.4c ±0.9** (1,278) $+120 | **+6.6c ±1.4** (352) $+23 |
| difference (95%) | +4.3c [+3.3, +5.4] | +3.3c [+1.6, +5.3] |

Fewer fills (about -35%) but more total dollars in both periods, and less cash tied up
(cash cap was nearly full on Nov 14). Live paper Nov 11-14 agrees: 8c+ +11.5c (15 fills)
vs under 8c +1.2c (45).

**16 newer low cities** (independent check, Kalshi data fetched Nov 15, Sep 1 - Nov 14):
live rule +2.9c ±0.7 (925 fills) $+26.6 vs 8c+ +4.3c ±1.0 (634) $+27.1; difference
+1.4c [+0.3, +2.6]. Oct 15 - Nov 14 alone: +2.6 -> +5.1c. Smaller than on the old cities
but the same sign, and the same dollars for a third less cash.
Flat or negative cities either way (not dropped, too noisy to single out): Houston,
New Orleans, Trenton, Boston, Seattle.

## Status (Nov 15, under Alex's 1:32 AM ET OK)
- Highs holds wait-while-rising: merged (PR #285), live from the next scan.
- Lows need 8c+ spread: merged in the follow-up PR, live from the next scan.
- Both are in `wxbot/latepaper.py` DEFAULTS, so the paper log and the real mirror
  (weather_late) follow them. To undo: set `min_spread_by = {}` / `wait_while_rising = []`.

## Looked at, not changed (did not clear the bar)
- Lows "need", skip while still falling: +3.2 -> +3.9c holdout, inside the noise.
- Lows "need", max price 90c: +3.2 -> +4.4c holdout, total $ about flat.
- Lows "need", stop after 4 PM local: small, noisy.
- Lows "holds", 8c spread: +1.4 -> +3.3c holdout, difference 95% [-0.3, +4.3]. Close, not clear.
- Highs "need" (paper): wait not rising + 2F+ away: +0.3 -> +2.4c holdout (noisy); stays paper.
- Highs "holds" wait-not-rising + after 2 PM: picked after looking, not used.
