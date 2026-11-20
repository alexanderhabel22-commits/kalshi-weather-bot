"""Paper twin of fade_leads (Jobs/GDP/Starship fade), Nov 20, 2026: the same macro_fade rule on
KXU3, KXGDP and KXSPACEXSTARSHIP, logged on paper at 1 contract with no dollar cap, so the
10- and 30-fill reviews keep a full record even if the real test stops at its $6 cap.
Never live (config keeps live = false). Started as the paper-only test (PR #334); the real
2-contract version is wxbot/strats/fade_leads.py (Alex, Nov 20 2:13 PM ET).

Research: /mnt/project-files/analysis/fade-leads/RESULTS.md. Replay Dec 2024-Nov 2026:
KXU3 +12.6c (61 fills), KXGDP +14.9c (29), KXSPACEXSTARSHIP +12.2c (36); together +13.0c,
0 losing fills; walk-forward +12.6c on 68 fills.
"""
from __future__ import annotations

from . import fade_leads

NAME = "fade_leads_paper"
SERIES = fade_leads.SERIES


def candidates(markets: list[dict], p: dict, now, held: dict | None = None) -> list:
    return fade_leads.candidates(markets, p, now, held, NAME, "fade leads (paper)")


def propose(ctx) -> list:
    return fade_leads.scan(ctx, ctx.params or {}, NAME, "fade leads (paper)")
