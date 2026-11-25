"""Earthquake NO strike ladder: 1-CONTRACT REAL-MONEY TEST from Nov 25, 2026 (Alex, Nov 25 12:37 AM ET:
"Go live with it", on taking the ladder paper test real).

quake_no's exact rule (wxbot/strats/quake_no.py: during the UTC day, USGS max so far < X - 0.1,
NO at 1 - yes_bid in 3-97c when the USGS-climatology edge after the taker fee is 5c+, one entry
per market) on the in-between strikes the live quake_no skips: 6.2, 6.4 and 6.8. Climatology =
the same 2015-2024 ComCat counts (quake_no.GUARD_DAYS: 682 / 460 / 213 of 3,653 days).

HONEST STATUS: replay Sep 15-Nov 23, 2026 (/mnt/project-files/analysis/quake-ladder/RESULTS.md)
+8.4c +- 8.5 per contract on 73 trades / 36 days, NOT significant; strikes 6.0 and below lose.
The ladder strikes lose together on a big-quake day (up to ~3 x 90c = ~$2.70 at 1 contract).

HARD LOSS CAP $4, ITS OWN (separate from quake_no's $4): settled real P&L of quake_ladder + the
full cost of every open quake_ladder position + the new order must stay within $4. Once settled
losses leave no room, it stops for good (state data/quake_ladder_state.json) with one push.
Kill switch: [trading.quake_ladder] live = false in config.toml (or TRADING_MODE=off for all).
Paper twin quake_ladder_paper keeps logging the same rule at 2 contracts. Review at 10 real fills."""
from __future__ import annotations

from ..util import DATA_DIR
from . import quake_no

NAME = "quake_ladder"
STRIKES = (6.2, 6.4, 6.8)
STATE = DATA_DIR / "quake_ladder_state.json"
DEFAULTS = {**quake_no.DEFAULTS, "contracts": 1, "max_loss_usd": 4.0}


def propose(ctx):
    p = {**DEFAULTS, **(ctx.params or {}), "strikes": STRIKES}
    if ctx.mode == "live":
        room, settled = quake_no.budget(ctx, p, NAME)
        if quake_no.stopped(ctx, p, settled, NAME, STATE, "Earthquake ladder test"):
            return []
        if room < float(p["min_price"]):
            return []
        return quake_no.fit_budget(quake_no.scan(ctx, p, NAME), room)
    return quake_no.scan(ctx, p, NAME)
