# Audit of the main bot's other strategies (Nov 12, 2026)

Question (Alex, Nov 11): put Mentions NO, big-event favorites and the Fed/CPI fade
through the same fine-toothed comb as the weather audit.

**How this differs from the earlier backtests.** The earlier studies scored "the average
maker fill", weighted by contracts, so a few very busy markets set the answer. This audit
replays the bot's own rule: every hour (the bot scans every 30 min) it looks at the real
bid/ask, places one fixed-size order at best bid + 1c if the live rule would, lets it rest
until the market closes, and fills it only when a later trade prints at our price. Each
order counts once. ± is one standard error with all bets on one event (or one release day)
counted together. Data: `research_main/fetch.py` (hourly bid/ask + trade tapes, pulled
Nov 11-12, on branch `main-audit-data`), replay `research_main/replay.py`, tables
`research_main/tables_*.md`.

## Summary

| Strategy | Earlier claim | Exact replay, per filled order | Last 90 days | Fills/day the rule finds | Verdict |
|---|---|---|---|---|---|
| Mentions NO 75-90c | +4 to +6c | **+0.9c ± 0.9** (Jun 19 - Nov 11, 726 events) | **-0.4c ± 1.3** | 11.7 | No measurable edge |
| Favorites 85-96c, 1M+ events | +3.7c | **+3.2c ± 0.9** (22 months, 341 events); **+2.0c ± 1.5** on the markets the live rule can actually reach | +7.5c ± 1.0 (election season) | 0.95 | Small real edge, thin |
| Fed/CPI fade 71-95c | +5 to +12c | **+9.4c ± 2.1**, +8.7c after the maker fee (22 months, 52 release days) | +8.6c ± 5.1 | 0.28 | Real, the best of the three, but rare |

At today's bankroll (about $64) all three together are worth a few dollars a month.
None of them is a path to $500/month by itself.

## 1. Mentions NO: the edge is not there at our size

- Replay of the live rule (75-90c, 1,000+ contracts traded, event 1-168 hours away, 3 words
  per event): 1,868 orders, 94% filled, **+0.9c ± 0.9** per filled order. By month:
  Jun +3.5, Jul +1.1, Aug +0.3, Sep +0.9, Oct -2.8, Nov 1-11 +8.2 (43 events). Last 90 days
  -0.4c ± 1.3. Back-of-queue fills only: +0.5c ± 1.0.
- **The market is priced right.** Our average price was 81.5c and the word stayed unsaid
  82.4% of the time. There is about one point between price and truth, not five.
- **Why the old number was higher.** It was weighted by contracts: the busiest words and
  events carried it, and we cannot buy more of those with 4 contracts a word. Counted one
  order per word, the old data says the same thing as the replay: about 0 through 2026.
  Sorting on a word's final volume is also a trap, because said words get heavy trading.
- **Same fill problem as weather, smaller.** The 6% of orders that never filled would have
  won 97.5% of the time (+15c each). The price runs away from the good ones.
- **Nothing to tune.** Price band (75-80c +1.0, 80-85c +0.9, 85-90c +1.7), spread, hours
  before the event, volume, speaker: no slice is clearly different from zero. The words the
  3-per-event limit skips earn the same (+1.9c ± 1.1), so the wider paper shadow
  (`mentions_wide`) should not be expected to help.
- Live so far: 7 settled words, 6 won, +$1.20. That is luck-sized; the replay has 250 times
  the sample.

**Recommendation: move Mentions to paper now** rather than wait for Dec 3. The Dec 3 live
sample (about 100 contracts) cannot tell +1c from -1c; this replay can, and it says about
zero. Real money on a zero-edge bet is only risk, and Mentions may use up to half the
bankroll. This changes a live market before its review, so it is Alex's call.

## 2. Big-event favorites: holds, but smaller than advertised where we can trade

- Replay of the live rule: 681 orders, 95% filled, **+3.2c ± 0.9**; back-of-queue only
  +2.7c ± 1.0. Average price 92.3c, won 95.5%. By period: 2025 H1 +0.1, 2025 H2 +1.8,
  2026 H1 +2.6, Jul-Aug +2.5, Sep-Nov 11 +7.4.
- The volume floor matters, as the first study said: events at 300k-1M contracts earn
  +0.5c ± 0.6.
- **Catch: the best part is out of the live rule's reach.** Elections earned +6.5c ± 1.4,
  Entertainment +2.2c ± 1.3, Politics +0.6c ± 1.9. But most election markets list a close
  date months after the vote and close early when the race is called, and the bot only
  looks at markets whose listed close is within 7 days. On markets that closed on their
  listed schedule (what the bot really trades: Rotten Tomatoes scores, charts, dated
  politics), the edge is **+2.0c ± 1.5**, and Entertainment alone is +0.2c ± 2.2.
- Risk shape: each order risks about 92c to make 8c. Losers are sudden news (a cabinet
  exit, a chart upset).
- Live so far: 6 of 6 won, +$1.14.

**Decision: keep live, unchanged.** It is positive in every period and survives the
pessimistic fill test, but expect about +2c, not +3.7c, and under one fill a day.

## 3. Fed/CPI fade: the one that clearly works, and it is small

- Replay of the live rule: 220 orders, 85% filled, **+9.4c ± 2.1** per filled order
  (+8.7c after the 1c maker fee four of the six series charge on a 1-contract order).
  Every half-year +8.9 to +10.1c. Back-of-queue only +9.3c. Average price 85.2c, won 94.6%:
  here the market really does overpay for longshots, by about 9 points.
- 45 of 52 release days were up. The bad days are real: Feb 12 2025 (hot January CPI) cost
  206c on 6 orders, Nov 13 2025 cost 106c, and this week's CPI (Nov 10) cost 46c.
- It is rare: about 8 fills a month, held about 20 days each. At 1 contract that is under
  $1 a month.
- Bookkeeping gap: the bot does not record the maker fee on live fills, so the Fed/CPI
  P&L it shows is up to 1c per order too high. Check against the Kalshi statement before
  fixing (the fee rounding could not be confirmed from here).
- Live so far: 1 of 2, -$0.74 (the Nov 10 CPI miss).

**Recommendation: raise Fed/CPI from 1 to 3 contracts per market.** The 10%-per-release
and 30% strategy caps stay, so the most that one release can lose does not change (about
$6 today); the bot just reaches that limit more often. The fee per contract drops from
about 0.7c to 0.3c. Worth roughly $2 a month now and about $15-20 a month at a $500
bankroll. It is a sizing change on a live market, so it waits for Alex's OK.

## 4. New ideas (paper first)

1. **Election and other "called early" favorites.** The strongest favorites result
   (+6.5c) sits in markets the live rule skips because their listed close is far away.
   A paper shadow could use the expected decision date instead. Flow is seasonal (few
   elections before 2027 primaries), so this is worth building before the next election
   run, not this week.
2. **More macro series with the same shape.** The longshot gap is 9 points in Fed/CPI
   and about 1 point in Mentions. The same replay can be pointed at GDP, jobs, gas and
   other scheduled-number series to see where else the gap is still open. One more
   download.
3. **Do not expand Mentions.** Wider bands, more words per event and mid-priced words all
   test at zero or worse here and in the Nov 10 study.

## Caveats

- Hourly bid/ask, not the full order book. An order that improved the bid is assumed first
  in line at its price; the back-of-queue numbers show the pessimistic case.
- Money caps are not applied: every order is one unit. The real bot places fewer orders
  than the replay finds (about 2 Mentions orders a day against 12) because of its caps.
- Mentions covers Jun 19 - Nov 11 2026 (the download ran out of time before older months;
  the older data was covered by the first pass with the Nov 5 pull).
- Favorites: a settled market only shows its actual close, so "closed on schedule" is
  inferred from the expected expiration time.
