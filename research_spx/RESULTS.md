# Kalshi stocks and leverage: is there an edge? (Nov 13, 2026)

**Verdict: NO on stock perps (leverage). One small YES-maybe on S&P daily-close brackets (not perps, no leverage).**

## 1. What Kalshi's stock product is

- **Single-stock perpetual futures**, launched Nov 3-5, 2026, on 50+ large NYSE/Nasdaq names (NVDA, GOOGL, COST, SNDK, SPCX, ...) plus SPY/QQQ index perps. Separate from the prediction markets: you apply for a perps margin account (Kalshi Prime / Kinetics FCM), cleared by Kalshi Klear. Regulated as security futures (CFTC + SEC).
- **Contract** = 0.02 shares (50 contracts = 1 share). Long or short, cash-settled, no expiry, no shareholder rights.
- **Leverage**: up to ~6.45x (15.5% minimum margin); per stock it is 1.6x (SNDK) to ~4.9x (GOOGL, COST, SPY/QQQ). Isolated margin in the app, cross-margin via API.
- **Fees**: third-party sources say 0.12% taker / 0.02% maker on full notional (with leverage), each way, at the entry tier. Kalshi's own fee page did not render the perps section, so this is unverified.
- **Funding**: once a day at 4:00 PM ET for stocks. Observed rates on Kalshi's page: 0.0000% to 0.0173%.
- **Hours**: stock perps are NOT 24/7. They follow a schedule with closures and "may reopen at a different price". Liquidation is market orders by the clearinghouse; losses can exceed margin.
- **North Carolina**: no source names any state restriction for perps (the state restrictions found are for sports contracts). Not confirmed from Kalshi directly; the application screen would say.
- **Legal**: exchanges/market makers are reportedly challenging the classification in court; trading continues.

## 2. Structural edges in perps at our size: none

- **Basis/funding arb** needs a stock position elsewhere to hedge. Funding is ~0-0.02%/day; a round trip costs ~0.24% of notional in taker fees. On ~$250 notional (5x a $50 margin) that is ~$0.60 per round trip vs ~$0.03/day of funding. Loses.
- **Overnight/weekend gaps**: stock perps close when the market closes, so there is no "trade the perp while the stock is shut" window like offshore 24/7 stock perps.
- **Versus just buying shares**: free at any broker. Perps add a 0.12% fee and a liquidation risk. The only thing they add is leverage and easy shorting, which is risk, not edge.
- At 5x, a normal 4% stock move = 20% of the account; SNDK-type names move that in a day.

## 3. "Claude stock bot success" claims

Read 5 of 7 widely shared posts (2 failed to load). Every one is a single short run (48 h to 3 months), paper or unverifiable, with the best model/strategy picked after the fact. The vendor post that "beat the S&P" admits backtests are 30-50% too optimistic and 3 months is too small; one bot's emails reported gains that weren't real. The best-controlled test (Elm Wealth, play money) found LLMs read direction about as well as experts but **size positions far too aggressively** (7-12x), which is exactly the leverage risk. No audited evidence of a durable retail edge.

## 4. Replay: S&P 500 daily-close brackets (KXINX) vs options-implied (VIX) odds

Data: every settled KXINX daily-close event Feb 2025 - Nov 12, 2026 (436 events, ~13k brackets), all trades between the prior close (4:05 PM ET) and 8:25/9:25 AM ET (before the open, so the index is frozen at the known prior close). Model: lognormal with VIX-scaled daily vol. Results clustered by day.

- **Model vs Kalshi**: the VIX model's Brier score beats Kalshi's pre-open prices (0.115 vs 0.121). But the profit from "trade when model disagrees" (+3 to +11c) is **all explained by one simple rule**, no model needed:
- **25-point brackets priced 30c+ before the open are overpriced.** With ~0.8-1% daily vol (~60-75 S&P points), the most central 25-pt bracket can be worth at most ~15-20%. YES takers paying 30-39c lose 15c, 40-49c lose 20c.
- **Back-of-queue maker test** (rest an offer selling 1 YES at price L on each bracket before the open; count a fill only if a buyer later paid MORE than L, 1c maker fee assumed):

| L | fills | hit rate | per fill | per day |
|---|---|---|---|---|
| 20c | 1,011 | 18% | +0.5c | ~0 |
| 25c | 735 | 19% | +5.1c | +$0.12 ±0.03 |
| 30c | 479 | 21% | +7.9c | +$0.15 ±0.03 |
| **35c** | **293** | **21%** | **+12.8c** | **+$0.21 ±0.04** |
| 40c | 167 | 22% | +16.8c | +$0.26 ±0.05 |

At 35c it is positive in all four periods (2025: +11.5c, Jan-Apr 2026: +14.7c, May-Aug: +12.1c, Sep-Nov: +14.8c).

**The catch: it is tiny.** ~14 fills/month at 1 contract = **~$1.80/month**. At 4 contracts ~$7/month. Each fill ties up ~65c of collateral and loses ~65c about 1 time in 5. Resting offers on ~8 brackets near the prior close reserve ~$5.

Cost: $0 (runs inside the main bot, no AI calls). Break-even: needs a per-fill edge above 0 after the 1c fee; replay says +12.8c, so it would take a hit rate above ~35% (vs 21% observed) to lose.

Caveats: fills are judged from public trades (traded-through), not the real queue; only the pre-open window was tested (intraday needs a live index feed); a 5-min bot may miss quick fills. Earlier "Financials NO-GO" (Nov 5) tested longshot fading below 30c, which agrees with the ~0 at 15-20c here.

Scripts: `scripts/` (fetch*.py download, an*.py model replay, mk.py maker test).
