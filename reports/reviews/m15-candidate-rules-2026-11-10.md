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
