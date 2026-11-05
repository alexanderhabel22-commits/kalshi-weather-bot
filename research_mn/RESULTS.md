# Mentions NO: overnight walk-forward study (Nov 5, 2026)

Data: every settled Kalshi Mentions market Jan 2025 to Nov 5 2026 (72,712 markets,
4,127 events, 22.9M trades), pulled by `research_mn/pull.py` (output on branch
`mentions-research-data`). Trades are bucketed by time before the event ended.
Method: a resting NO bid earns what the average maker NO fill at that price earned.
Walk-forward: each test month (Oct 2025 to Nov 2026) is scored with rules that use
only earlier months. CIs resample whole events. Full tables: `analysis_tables.md`.

## What we found

| question | answer |
|---|---|
| Overall edge (75-90c, test months) | +5.7c/contract, 13 of 14 months up; last 6 months +4.3c (shrinking) |
| Filter by speaker / event type (series) | No help out of sample (5.9-6.0c with or without the filter) |
| Word type (one word vs phrase, "X / Y" options, words per event) | No stable pattern between halves |
| Price band | 75-90c stays the best all-round pick. 50-70c paid more early but fell to ~0-4c lately. 75-85c was the steadiest in all 3 periods. No change. |
| Busier words (volume traded before our order) | Small, steady gain. vol >= 1000: +6.1c vs +5.9c, beat the base in 10 of 14 months, last 6 months +5.1c vs +4.8c. Costs ~14% of fills. **Applied: min_volume 100 -> 1000.** |
| Timing (how early the order rests) | No gain from only quoting within 12/24/48/72h. Keep 1-168h. |
| Adverse selection during the live event | Under 1% of fills happen in the last 30 min (markets close when the word is said). Edge there is noisy (+2c, CI -10 to +9); cancelling 30m-2h before the end changes the result by <0.2c. No cancel logic needed. |
| Event risk | Per event: mean +6.3% per $ filled, sd 29%, 22% of events lose, worst 10% lose 32%+. About 16% of words we'd hold get said. |

## Sizing (applied)

Quarter-Kelly per event is ~19% of the bankroll; the current 25% event cap and 3 words
are inside half-Kelly. Size now scales with the bankroll: `word_pct = 0.05` (5% per
word, ~2 contracts at $37, same as before), capped by `contracts = 3` and the global
`max_contracts_per_order = 3`. Raise those caps as the bankroll grows.

## Capacity (last 3 months, 75-90c)

About 6.9 events/day, ~92k maker-NO contracts/day ($76k/day). Median per market 308
contracts (p25 53, p10 11); median per event 3,360.

| bankroll | per word | per event | fits the market? |
|---|---|---|---|
| $37 | ~2 contracts | ~$5 | yes, trivially |
| $100 | ~6 | ~$15 | yes |
| $500 | ~30 | ~$75 | yes in most markets (~10% of a median word); use 4-5 words/event |
| $2,000 | ~120 | ~$300 | spread over 6-8 words; thin words (p25) can't take it, edge likely drops from queue position. Practical ceiling roughly $2-5k. |

The bankroll is the limit, not the market, up to ~$2k.

## Caveats

- The edge is shrinking: ~6-10c/contract early 2026, ~3-5c Aug-Oct, and the first 5 days of Nov are -3c (43 events, noise-level, but watch it).
- "Average maker" fills: our fill rate and queue position are unknown until paper/live data comes in.
- 0 fees assumed (Mentions maker fee 0 as of Nov 5).
