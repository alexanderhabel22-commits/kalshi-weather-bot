"""PAPER twin of the quake_ladder real test (wxbot/strats/quake_ladder.py, real 1 ct from Nov 25, 2026).
PAPER test of a strike ladder for the earthquake NO rule (Nov 24, 2026; Alex: "Is there a way to
basically step the earthquake one? Like obviously there's a lesser chance of a 7.0 than a 6.6").

quake_no's exact rule (during the UTC day, USGS max so far < X - 0.1, NO at 1 - yes_bid in 3-97c
when the USGS-climatology edge after the taker fee is 5c+, one entry per market), run on the three
in-between strikes the live test skips: 6.2, 6.4 and 6.8. Climatology = the same 2015-2024 ComCat
counts (quake_no.GUARD_DAYS: 682 / 460 / 213 of 3,653 days).

Replay Sep 15-Nov 23, 2026 (70 UTC days, hourly candles, USGS known 15-30 min after origin;
/mnt/project-files/analysis/quake-ladder/RESULTS.md): 6.2 / 6.4 / 6.8 together +8.4c +- 8.5 per
contract on 73 trades over 36 days (both halves positive), NOT significant. Strikes 6.0 and below
lose (-3c), so they are left out. Ladder strikes lose together on a big-quake day.
Never live (config keeps live = false); logs 2 ct with no cap so the review has data."""
from __future__ import annotations

from . import quake_no

NAME = "quake_ladder_paper"
STRIKES = (6.2, 6.4, 6.8)


def propose(ctx):
    p = {**quake_no.DEFAULTS, **(ctx.params or {}), "strikes": STRIKES}
    return quake_no.scan(ctx, p, NAME)
