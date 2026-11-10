# Spot-signal research (Nov 10, 2026)

Paper/replay only. Data: branch `spot-research-data` (runs/<date>-<series>/: markets, sample,
trades = Kalshi tapes of each market's last 11 min, spot = 1-min Coinbase or Yahoo futures).

- `fetch.py` / `pyth.py`: history pulls (GitHub Actions, push-triggered on the research branch).
- `agree.py MARKETS SPOT`: how often the spot feed's open->close move matches Kalshi's result.
- `replay.py SAMPLE TRADES SPOT PRODUCT MAXLEFT rel`: resting favourite bids (back-of-queue
  "paper" and touch "pessimistic" fills) split by spot-implied fair value and recent drift.
- `taker.py SAMPLE TRADES SPOT PRODUCT MAXLEFT`: Brier of Kalshi's price vs a 1-min spot
  model, and buying as a taker when the model says Kalshi is cheap after fees.

Findings: Kalshi's price beats a 1-minute spot model on BTC and all metals tested; the
1-minute skip rule adds no profit. Next step is second-level data: the shadow logger in
`wxbot/m15_fv.py` (logs only) on the 15-minute server.
