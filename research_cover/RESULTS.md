# Coverage sweep: Kalshi markets the live main-bot rules don't scan (Nov 13, 2026)

Question (Nov 13): the late-day weather thread found 16 cities the bot never saw (PR #227).
Do the same for big-event favorites, the Fed/CPI fade (macro_fade), the gas + jobless-claims
fade (macro_wide) and the election-favorites shadow (favorites_wide). For each missed
series, replay the existing live rule honestly and go live at 1 contract only if it clears
about +3c per contract after fees, on a decent sample, and holds in recent months.

**Answer: nothing new clears the bar. No strategy changes; caps and stops untouched.**

## 1. Is discovery broken anywhere? No.

- **favorites / favorites_wide** find series by category and volume, not by settlement
  links. Fresh catalog pull (16,341 series): 583 big (1M+ contracts) series in Elections,
  Politics and Entertainment; the bot's own state holds 582, with 189 of them active vs 190
  in my pull (the difference is one listed in the last hour). No series has
  Elections/Politics/Entertainment only in its secondary `categories` list.
- Latent risk fixed: the open-event listing stopped at 150 pages. Kalshi has about 21,500
  open events (108 pages) today, so the scan had about 40% headroom left before it
  silently dropped series. The cap is now 400 pages, with a warning if it is ever reached.
- **macro_fade / macro_wide** use hand-typed series lists, so nothing breaks, but anything
  not on the list is never seen. That was the real gap, tested below.

## 2. The fade rule on 51 more scheduled-number series

Same replay as the Nov 12 audit (`research_main/replay.py`, unchanged): NO at best bid +
1c in the 71-95c band, rest to the close, fill on a later print at our price. One order =
one unit. **1c maker fee charged on every fill** (pessimistic). Jan 2025 - Nov 12 2026,
7,149 settled markets. ± clusters all orders on one release day together.

| Family | Net c/fill, all | Last 6 months | Last 90 days | Verdict |
|---|---|---|---|---|
| All 51 series pooled | **+0.5 ± 1.3** (860 fills) | | +0.1 ± 2.4 | no edge |
| Foreign central banks (12: BoJ, ECB, BoE, Canada, Mexico, Brazil, ...) | **-5.1 ± 2.6** | -5.6 | -10.6 | NO, loses |
| US jobs versions (U3, ECONSTATU3, UE, ADP, NFP, combo) | +4.1 ± 2.6 (62 days) | +5.7 ± 2.9 | +4.4 ± 4.1 | under 2 sigma |
| US CPI versions (ECONSTAT*, core YoY, gas CPI, ...) | +2.2 ± 2.9 | +0.5 | -7.1 | NO |
| GDP / ISM / retail / housing / PPI | -5.7 ± 7.1 | -7.9 | +5.0 | NO |
| Fed extras (Fed combo, dissents) | +2.8 ± 3.2 (9 days) | | | too few |
| Daily/weekly numbers (Truflation, 10Y yield, ERCOT, SOFR, mortgage, SPR) | -1.3 ± 2.8 | -1.7 | +2.8 | NO |
| Monthly prices (gas monthly, diesel, eggs, oil) | +4.4 ± 3.5 | -1.3 | -1.8 | fading, NO |

The Fed/CPI fade works on the six original series (+8.7c) because those markets have
heavy casual longshot buying. Copying the rule onto the side series gives about zero on
average, and the foreign central banks clearly lose (their longshots really do hit).

**One to watch, not trade: KXU3 (unemployment rate).** 53 fills on 22 release days,
+13.4c, never lost, positive in all four half-years. But it is one series picked out of 51
(about 2 of 51 zero-edge series would show a clean streak like that by luck), the other
two versions of the same jobs number (ECONSTATU3 +6.4 ± 6.2, UE -1.2 ± 5.6) did lose, and
it fills about 3 times a month (about $0.35/month at 1 contract). Re-check at the Nov 26
main-bot check-in if anyone wants it; it does not meet "confident" today.

## 3. The favorites rule in categories it skips

Same replay (85-96c favorite, best bid + 1c, 1M+ contract events, close within 7 days) on
162 big series in Science & Tech, Companies, Economics, Financials, Crypto, World, Health
and Climate one-offs (no 15-min/hourly/daily price ladders). 2,100 markets, 187 events.

- All: **+1.9c ± 1.6** (262 fills over 22 months, about 0.4 a day); back-of-queue +1.5;
  last 90 days +0.5 ± 6.3.
- Economics +3.3 ± 1.5, but those are the same Fed/CPI/gas markets the fade strategies
  already trade from the other side, so it adds risk on the same releases, not new
  coverage. Without Economics: about +0.5c. Crypto +0.8, Science & Tech +0.5,
  Financials -4.9 (a Tesla robotaxi and a Pikachu auction each cost about 87c).
- NO-GO. Matches the Nov 6 category study (AI and tech favorites NO-GO).

## Files

`research_cover/fetch_cover.py` (download), `analyze_cover.py` (scoring),
`families.py` (pooled by release day), `RAW_*.txt` (full tables). Data is in the
session scratchpad only (about 60 MB, re-pull takes about 2.5 hours).
