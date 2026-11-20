"""Paper twin of quake_no (Nov 20, 2026): the exact same earthquake NO rule, logged on paper at
2 contracts, with no dollar cap, so the 20- and 50-fill reviews have a full record even after the
real-money test stops at its $4 cap. Never live (config keeps live = false)."""
from __future__ import annotations

from . import quake_no

NAME = "quake_no_paper"


def propose(ctx):
    p = {**quake_no.DEFAULTS, **(ctx.params or {})}
    return quake_no.scan(ctx, p, NAME)
