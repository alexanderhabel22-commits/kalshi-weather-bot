# Fed outside odds vs Kalshi Fed markets (Nov 10, 2026, research only)

Question: do Kalshi Fed decision prices drift away from free outside odds (CME fed funds futures, i.e. FedWatch math; Polymarket), and can we trade it?
CPI vs Cleveland Fed nowcast was already tested Nov 5 (Kalshi wins), so it was not repeated.

## Data
- Kalshi: KXFEDDECISION/FEDDECISION hourly bid/ask, 21 days before each meeting (econ-research-data branch, Nov 5 pull), plus live Dec 2026 / Jan 2027 books.
- Fed funds futures (Yahoo): only contracts still trading have history (Nov 2026 onward). Expired contracts return 404; Barchart 403; FRED times out. So futures cover only the Oct 28, 2026 meeting and the live Dec/Jan meetings.
- Polymarket Fed decision markets: hourly history for 17 meetings, Jun 2024 to Jun 2026 (no Jul/Sep/Oct 2026 events found). 20,968 matched hours.
- Code: research_fedwatch/ on branch claude/project-thread-5u9j7c; raw data on branch fedwatch-research-data.

## Results
1. Futures vs Kalshi (Oct 2026, daily): implied hike odds matched Kalshi within ~2-5 points every day (e.g. Oct 7 futures ~18% vs Kalshi 16%; Oct 22 6% vs 6%). That gap is inside the futures' own noise (EFFR basis ~1-2bp = 4-8 points). Live Dec 9 meeting today: futures imply ~+27bp, Kalshi hike 25 at 85/86c and 50 at 10/11c, Polymarket 85.5c / 10.75c. All three agree.
2. Polymarket vs Kalshi: average gap ~0.7-1c. Brier is a tie (Polymarket very slightly better a week+ out).
   - Polymarket leads a little: after a 3c+ gap, Kalshi moves ~0.3c toward Polymarket within 1h and ~1.9c within 24h (42% of the gap). Polymarket does not move toward Kalshi.
   - Trading that convergence (buy, sell 6-72h later) LOSES 1-4c/contract after spread and fees.
   - Buying the side Polymarket favors and holding to the decision: +7.7c/contract (gap >= 2c, buy 1h later at the ask, 140 trades), but by meeting it is +4.8c +/- 2.7c over 12 meetings (7 positive), driven by May, Sep and Dec 2025. Fading Polymarket loses 12.7c, so the signal points the right way, but it is not proven.
3. The existing 90-98c Fed favorite lead (hourly, 17 meetings): taker +2.6c, 16/17 meetings positive, 2 losing trades, both on Sep 2024 (the surprise 50bp cut). That day Polymarket was 3-4c below Kalshi. With a filter "only buy if Polymarket >= Kalshi price": +3.1c, 13/13 meetings, 0 losses. This rests on ONE surprise, so it is an anecdote, not proof.

## Verdict
NO-GO for real money. Kalshi already prices Fed decisions the same as futures and Polymarket; the gaps are about a cent and fees eat the convergence.
Keep as notes: (a) the Polymarket filter for the Fed 90-98c lead; (b) follow-Polymarket-to-settle. Re-test ~Feb 2027 with the Dec + Jan meetings added (with the econ re-run).
More money: full futures history (all expired ZQ contracts) needs paid CME data; it would only firm up result 1, which already looks like "no gap".
