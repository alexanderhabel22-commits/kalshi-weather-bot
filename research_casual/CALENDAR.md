# Casual-money surge calendar, Nov 2026 - Mar 2027

Dates marked ~ are from memory of the usual schedule and must be confirmed when Kalshi lists the event.
Volumes are last season's, in contracts.

Standing paper rule for every row (from RESULTS.md): rest NO bids only, 1 contract, 50-90c, 1c inside the
best bid, only where the spread is 3c or more, in markets where small orders are 50%+ of the flow. Never rest
a YES bid. The paper market maker on the server picks the busiest wide-spread markets by itself every 6 hours
and now tags each fill with the casual share, so these events enter the paper test without extra work.
"Sell the field" rows are logged by the paper book in `wxbot/paperlab.py` (`m15/paper-field.json`): it enters every
one-winner event with 5+ candidates by itself, so the December and Super Bowl events are picked up when Kalshi lists them.
The live favorites strategy (85-96c in 1M+ Entertainment, Politics and Elections events) already trades the
big ones with real money.

| When (ET) | Event | Last season | Paper position | What we learn |
|---|---|---|---|---|
| Thu Nov 26 | Thanksgiving: NFL triple-header broadcast Mentions, parade and holiday novelty | NFL Mentions 2.6M in Nov, 21M in Dec | NO bids on announcer-Mention words at 50-90c before kickoff, cancel at kickoff | Whether broadcast Mentions repeat last year's -14c for small YES buyers |
| ~Nov 24-25 | Dancing with the Stars finale | 5.5M | None beyond favorites. Reality TV winners are priced right | Control group |
| ~Dec 2-4 | Spotify Wrapped (top artist, top album) | 20M + 1.7M | NO on every candidate at 10-50c YES ("sell the field"), sized so one winner costs under the total collected | Whether the field is overpriced as a set |
| ~Dec 8-11 | Google Year in Search; TIME Person of the Year; Game Awards | 18.8M; 19.6M; 3.2M | Same sell-the-field paper book for Google and TIME. Game Awards: favorites only | Same |
| Dec 9-10 | Fed meeting, Powell press conference Mentions | Fed Mention 2-4M a month | Existing Mentions NO rule (live) | Already tracked |
| ~mid Dec | Survivor finale | 2.3M | None | Control |
| Dec 25 | Christmas #1 song; NBA Christmas games Mentions | 3.0M; NBA Mentions 5.6M | Sell the field on the song. NO bids on NBA broadcast words before tip-off | Field pricing; broadcast Mentions |
| ~Jan 10 | Golden Globes | 1.1M | Favorites only | Awards are fairly priced |
| ~Jan 16 - Jan 31 | NFL playoffs: broadcast Mentions | NFL Mentions 25M in Jan | NO bids on words 50-90c before kickoff | Biggest broadcast-Mentions sample of the year |
| ~Jan 21 | Oscar nominations | 8.4M Best Picture, ~20M total | Favorites only (live strategy) | Already tracked |
| ~early Feb | Grammys | ~20M total | Favorites only | Awards are fairly priced |
| ~Feb | State of the Union / big Trump speeches | Trump Mentions 54M in Feb 2026 | Existing Mentions NO rule; paper test of 40-70c words continues | Whether casual Mentions flow returns |
| ~Sun Feb 14 | Super Bowl: first halftime song, guests, performers, setlist, ad and broadcast Mentions | 113M first song; 40M guests; 26M performers | Sell the field on first song, guests and performers. Start the paper book when the markets list (about 2 weeks ahead), because small YES buyers pile in early | The single biggest casual event. Last year small YES buyers lost 22-44c |
| ~Mar 14 | Oscars | 25M Picture, 25M Actor | Favorites only | Already tracked |
| ~mid Mar | March Madness: college basketball Mentions and games | NCAAB Mentions 3-4M | NO bids on broadcast words before tip-off | Broadcast Mentions |

If the paper book on Spotify, TIME and Google in December shows the field overpriced (positive after fees on
all three), the proposal to Alex is a small real-money version for the Super Bowl markets in February.
