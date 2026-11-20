"""Fed/CPI fade rule on three single-series leads: PAPER ONLY (Nov 20, 2026).

Series: KXU3 (US unemployment rate above X), KXGDP (real GDP growth above X),
KXSPACEXSTARSHIP (Starship flight launches before date X). Same rule and code as
macro_fade (resting NO at best bid + 1c, 71-95c, closing 2 h to 21 days out),
logged as `fade_leads_paper`. Never live: config keeps live = false.

Research: thread "Dig into unemployment, GDP, SpaceX leads", Nov 20, 2026,
/mnt/project-files/analysis/fade-leads/RESULTS.md. Replay Dec 2024-Nov 2026
(hourly candles incl. Sep-Nov 2026, fill only on a later print, 1c fee):
  KXU3 +12.6c (61 fills, 22/22 events +), KXGDP +14.9c (29, 8/8),
  KXSPACEXSTARSHIP +12.2c (36, 9/9); together +13.0c, 0 losing fills.
Walk-forward: picked as the top series on data before Sep 2025, they made
+12.6c on 68 fills / 23 events after it, while the other ~40 series lost -2.9c.
Caveats: zero losses so far means the error bar is really the chance of one
surprise release (a 2-3 fill loss of ~85c each); ~5 fills a month in total.
Rule changes or real money wait for the Fed/CPI fade's 500-contract review
(freeze rule) and Alex's OK.
"""
from __future__ import annotations

import dataclasses
import logging
import os

from ..trader import held_by_event
from ..util import HttpError
from . import macro_fade
from .mentions import _series_markets

NAME = "fade_leads_paper"
SERIES = ["KXU3", "KXGDP", "KXSPACEXSTARSHIP"]
log = logging.getLogger("wxbot")


def candidates(markets: list[dict], p: dict, now, held: dict | None = None) -> list:
    return [dataclasses.replace(i, strategy=NAME, note=f"fade leads (paper); {i.note}")
            for i in macro_fade.candidates(markets, p, now, held)]


def propose(ctx) -> list:
    if ctx.now.minute % 30 >= 5 and not os.environ.get("TRADE_FORCE_SCAN"):
        return []
    p = ctx.params
    markets = []
    for s in p.get("series") or SERIES:
        try:
            markets += _series_markets(ctx, s)
        except HttpError as exc:
            log.info("fade_leads_paper %s: %s", s, exc)
    out = candidates(markets, p, ctx.now, held_by_event(ctx.orders, NAME, ctx.mode))
    log.info("fade_leads_paper: %d open markets, %d candidates", len(markets), len(out))
    return out
