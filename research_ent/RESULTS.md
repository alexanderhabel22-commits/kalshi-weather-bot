# Box office + Rotten Tomatoes (Nov 10, 2026): logging + first test

Research only; the bot is unchanged. Logger: `research_ent/logger.py`, workflow `ent-log.yml`
(every 2 h, ~1-2 min, own concurrency group). Data: `ent-log-data` branch, folder `log/`.

## What Kalshi lists
- Rotten Tomatoes: 187 open markets (KXRT movies, KXRTTV shows, KXRTCOMPARE), settling Nov 16 to Dec 28.
  Rule: "Tomatometer score above X on <date> 10:00 AM ET". 237 settled films since 2024, 163M contracts.
- Box office: **no gross / opening-weekend markets exist** (only Golden Globe award series). Box Office
  Mojo daily charts are still logged once a day (cheap) in case Kalshi adds them; 2025-26 history saved.

## First test: does the live Tomatometer beat the RT price? NO-GO
Score history rebuilt free from Internet Archive snapshots: 1,419 scored snapshots, 168 films.
Price taken from the first hourly candle ending 1 h after each snapshot (14.6k market-snapshots).

| score minus strike | final yes rate | market mid |
|---|---|---|
| -4..-2 | 0.03 | 0.07 |
| -2..0 | 0.09 | 0.12 |
| 0..+2 | 0.40 | 0.38 |
| +2..+4 | 0.75 | 0.70 |
| +6..+8 | 0.88 | 0.86 |

The market already tracks the current score. Trading the gaps at the ask after fees, model fit
leave-one-film-out, 4c min edge: -7.3c/contract (152 films, SE 1.8c). Calibration of prices alone
(72 h to 1 h before close) is within a few cents in every bucket.

## What's left
- Speed at review-embargo lift (score first appears, price jumps). Needs minute-level RT polling on the
  always-on droplet; the 2 h log only shows it coarsely. Not built.
- Forward log keeps running so this can be re-checked ~Feb 2027.
