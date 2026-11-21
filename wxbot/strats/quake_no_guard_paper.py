"""PAPER test of the fast-quake guard (Nov 21, 2026, thread "Get faster quake data for quake bet").

Same earthquake NO rule as quake_no, plus the guard in quake_no.guard(): skip when GFZ or EMSC
already shows a quake near the strike, or when Kalshi prices a strike USGS hasn't reached far above
its climatology (the market has seen a quake USGS hasn't posted yet). Compared with the unguarded
paper twin quake_no_paper at the 20/50-fill reviews. Never live (config keeps live = false)."""
from __future__ import annotations

from . import quake_no

NAME = "quake_no_guard_paper"


def propose(ctx):
    p = {**quake_no.DEFAULTS, **(ctx.params or {}), "guard": True}
    return quake_no.scan(ctx, p, NAME)
