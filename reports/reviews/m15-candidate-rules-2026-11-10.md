# 15-min bot: patterns in live fills (Nov 10, 2026, ~5 PM ET)

Data: m15-data branch, markets-2026-11-07..10.csv (Nov 7 10:30 PM ET to Nov 10 5 PM ET, ~2.7 days).
Live: 679 markets, 1,138 contracts, -$8.17 all coins. BTC 406 ct (+$2.47, +0.6c), silver 234 ct (+$10.02, +4.3c).
Paper cross-check: 3,018 touched markets across 17 coins, PESSIMISTIC rule (1 contract at last bid if the tape printed at our level).
Ranges are 90% cluster bootstraps by window (markets closing together count as one draw). ~50 cells were tested,
so expect a few "significant" cells by luck. Scripts: load.py, an.py (live), pess.py (paper pessimistic).

Caveat on data: doubles only log the blended average price, not each fill's price, so the 2nd contract can't be
judged on its own. "Last bid" is the highest level the bot quoted, which partly knows the future (a favourite that
kept rising), so price rules are judged on AVERAGE ENTRY PRICE in live data, not last bid.

## What showed up

1. BTC: entry price matters. Positive on all 3 days.
   - Fills at 86c+: 71 ct, +7.9c/ct (range +2.3 to +11.1). By day: Nov 8 +13c, Nov 9 +10.4c, Nov 10 +4.1c.
   - Fills at 81c+: 156 ct, +4.9c (range -0.8 to +9.9).
   - Fills under 81c: 250 ct, -2.1c (range -7.8 to +3.5). All of BTC's losses live here.
   - Paper pessimistic agrees in direction for BTC (76-80c: -13c; 86-89c: +7c) and in 13 of 17 coins the
     86c+ band beats the 71-80c band.
2. Silver: no price effect (every band +3.5c to +7c). Time of day looks like the signal:
   - Midnight-noon ET: 137 ct, +9.0c (range +3.4 to +13.9). Night (12-6 AM) +12.6c, 97% wins, on BOTH nights.
   - Noon-midnight ET: 97 ct, -2.3c (range -11.3 to +6.2).
   - Only 2 nights of data, so this is the weakest of the three.
3. BTC has no time-of-day pattern (every quarter of the day -1c to +2c).
4. Doubles vs singles: doubles look better (BTC +1.8c vs -3.2c) but that's mostly because singles are markets
   where the favourite never got to 80c. Paper suggests a 2nd contract at 80-85c is ~break-even and at 86-87c
   is positive. Not provable from the current log.
5. Window position: BTC (last 10 min) and silver (last 5 min) fill almost entirely 2-10 min before close; nothing
   to split on.
6. BTC spot move at fill: fills right after spot moved 4+ bps toward our side lost -11c (21 markets). Watch only.
7. 90c+ fills: 96.8% wins live across all coins (3 losses, all copper/WTI). Break-even is ~94%. Unproven.
8. Paper coins (metals, alt crypto) at 86c+ only: still mixed (alts -8c to +7c). Not a reason to bring them back.

## Candidate rules for the 500-contract reviews (no live changes before then; freeze)

| # | Market | Candidate | Adopt at its 500 review if | Expected effect |
|---|---|---|---|---|
| A | BTC | min_fav 0.71 -> 0.81 (BTC only) | fills >=81c still >= +3c AND fills <81c still <= 0 on the NEW contracts since this note (after ~406) | Kill the -2c tail. ~40% of the contracts at ~+5c instead of all at ~+0.6c: ~$2.7/day vs ~$0.9/day at 1-2 ct |
| A2 | BTC | min_fav 0.86 (stricter) | only if A passes and 86c+ keeps its range above 0 | Fewer contracts (~25/day), ~+8c |
| B | Silver | bid midnight-noon ET only, or put the size-up (2->3 base) only in those hours | midnight-noon range stays above 0 AND noon-midnight stays <= +1c on new data | Weakest: 2 nights. Treat as a hint |
| C | Both | 2nd contract band 80-87c -> 85-90c | needs per-fill prices first (see below) | Unknown until logged |

Untouched: BTC time of day, window position, metals/alt crypto (stay paper).

## One small logging fix that would help (not a rule change)
Log each fill's price (or the 1st/2nd contract price) in markets-*.csv so rule C and the true entry-price split can
be judged. Today a double at 77c + 85c just shows as 81c.

## Update Nov 11, 2026: 80-day candle replay (thread "Grow the 15-minute market edge")

Full write-up in the project files (analysis/m15-edge/RESULTS.md). Kalshi 1-min candles, BTC 7,689 markets
Aug 21 - Nov 10, buy at the bid on the first minute in the band; train Aug 21 - Oct 9, test Oct 10 - Nov 10.

- Today's BTC rule fades: +1.6c (Aug), +2.1c (Sep), 0.0c (Oct), -0.6c (Nov); test -0.03c/ct.
- Candidate A (min 81c) is NOT supported over 80 days (<81c +1.0c, >=81c +0.6c). Judge it on live data as planned,
  but expect it to fail.
- Candidate B (silver midnight-noon) weakly supported: +1.7c vs +1.1c (test +2.1c vs -0.3c).
- Candidate C: 2nd contract at 80-87c is -0.6c in the test period; no other band holds. Consider "no 2nd contract".

| # | Market | Candidate | Adopt if | Expected effect |
|---|---|---|---|---|
| E | BTC | Bid from 14:00 to 10:00 before close, favourite 80-95c, join the bid, 1 contract, no 2nd contract; skip the window if no such favourite by 10:00 left | on windows after Nov 11 2026 (>= 600 qualifying, ~3 weeks): replay at bid >= +1c AND beats today's rule on the same days AND pay-the-ask version >= 0; then real (paper or live) fills within 1c of the replay | replay train +3.2c, test +1.4c (pay ask +0.4c), ~31 windows/day; ~$20-50/mo at 4 ct |
| E2 | BTC | E, only when the favourite is against the last hour's move (strike now vs strike 1 h ago) | judged inside E, same bar | test +2.1c on ~16/day |

Freeze: E is a new way to trade BTC, so it starts on paper. Real money needs Alex's OK.
