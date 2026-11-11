# Main bot: status and new edges (Nov 10, 2026, ~8:30 PM ET)

## 1. Real-money results so far (live since Nov 5-6, from data/trade_orders.csv)

| Strategy | Settled | Contracts | P&L | Still open |
|---|---|---|---|---|
| Mentions NO (75-90c) | 7 words, 6 won | 22 | **+$1.20** | 3 Trump words x4 ct (~$9.88 cost, settle by Nov 16) |
| Big-event favorites (85-96c) | 6 bets, 6 won | 16 | **+$1.14** | Hegseth-out NO 4 ct @88c; 2 Ebenezer RT bids resting |
| Fed/CPI fade (macro_fade) | 2, 1 won | 2 | **-$0.74** | core PCE NO 1 ct @89c |
| Weather day-before | 33 | 33 | **-$1.50** | ~13 Nov 10-11 positions not yet booked |
| **Total main bot** | | | **+$0.10** | |

Account total $53.64 (cash $32.86 + positions $20.78) at 7:55 PM ET.

Read: everything is far too small to judge. Mentions and favorites are positive and behaving as the backtests said; the one Mentions loss ("Crypto/Bitcoin", -$1.74) ate most of a week's gains, which is exactly the shape of that strategy (many small wins, rare full loss). Weather is the drag, as before. Freeze stands until each market's 500-contract review.

## 2. New Kalshi series since Nov 5 (launch watcher, 260 found)

Mostly one-off politics/elections (2028 primaries, House votes, cabinet exits) with no volume and 98c spreads, plus 34 sports. The recurring new ones:
- **"X after economic events"** (FX: EUR/USD, GBP/USD, USD/JPY, AUD, CAD, MXN; Treasury 2/5/10/30Y; gold, silver, copper). New Nov 5-10, zero volume so far. Same assets were NO-GO in the Nov 5 financials test and the Nov 8 15-min FX/rates test (-1.7c, -6.5c). Watch only.
- **Home values monthly** (16 cities), **TikTok Shop / credit-card spend arrays**, **Dutch TTF gas daily/weekly/monthly**, **AAA gas by state**, **BTC hit-by-date**. Zero or near-zero volume; gas and crypto above/below are already NO-GO. Nothing tradeable to test yet.

## 3. Backtest: trading Mentions markets in the seconds after a word is said

The one big untested idea left on the main-bot side was "listen to the live speech/earnings call and trade the instant the word is said" (parked Nov 7 for lack of a server; the m15 droplet now exists). Mentions was not part of the Nov 10 source-lag test.

Data: hist-research-data (Nov 7 mine), 3,693 Mentions markets Feb 2025-Nov 2026 with full second-level trade tapes. A "jump" = 10-min median price <= 50c, then a trade >= 60c followed by >= 85c within 60 s. 866 jumps. For each, every trade at <= 85c from 10 s to 5 min after the jump, P&L for a taker buying YES after the taker fee (an upper bound: assumes we got those exact fills).

Results:
- **The first 3 seconds belong to faster bots**: 118k contracts change hands there, versus ~20k in 3-60 s.
- **Jumps are not certainty.** 298 of 866 jumps to 85c+ settled **NO** (35% for earnings calls). The stale-looking prices after a jump are mostly real doubt (did the word count under the rules, was it the right speaker), not slowness.
- Following every jump after 10 s: earnings +$4.0k, other +$9.0k, Trump -$1.8k in the sample. **Without the top 5 jumps: earnings -$11.3k, other -$3.8k, Trump -$2.6k.** Median jump: $0. Only 23-25% of jumps make money.
- **2026 only: earnings -$6.9k, other +$0.9k, Trump -$1.9k.** The positive total comes from 2025.
- Defensive angle (could a listener cancel our resting NO bids before they get picked off?): about 1% of all 75-90c NO-maker fills happen in the 2 min before or 3 s after a jump, and none later, so a listener with even 3-5 s latency would save almost nothing.

**Verdict: NO-GO.** Following jumps blind loses in 2026, and the only way it could work is a listener that is both faster than 3 s and better than the market at judging whether a word counts. We can't backtest that without transcripts. Not worth a paid speech-to-text feed (~$15-25/mo for earnings calls alone, over the $10 limit) on this evidence. Revisit only if someone finds a way to check rule-edge cases cheaply.

## 4. What this leaves for the main bot

- Already on the list, unchanged: Mentions verdict ~Dec 3, favorites judge ~Dec 3, macro_fade judge mid-Dec, weather back to paper review per the blind-spot plan.
- Best open leads for main-bot money, in order: (1) source lag on RCP approval / Arena / RT (logger on the droplet, grade ~Nov 18-25), (2) Fed-decision favorites 90-98c (+2.6c, 16/17 meetings, next FOMC Dec 9-10; one surprise costs ~95c/contract), (3) Trump-series Mentions 40-70c (+4.1c OOS on 134 events, found after the fact).
- Platform-wide, every category with real volume has now been tested at least once (coverage map Nov 8, edge mine Nov 7, this test). The re-test sweep is due ~Feb 2027.

Files: ml.py / ml2.py / ml3.py (run against a local copy of the hist-research-data runs), mjumps.csv (one row per jump).
