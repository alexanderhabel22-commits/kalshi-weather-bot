"""PAPER shadow: the Fed/CPI longshot fade on other weekly scheduled numbers.

Research (Nov 12, 2026, thread "Prioritize and scale the winning main-bot
strategies"): the exact macro_fade replay (research_main/replay.py, same rule:
NO at best bid + 1c in the 71-95c band, rest to close, fill on a later print)
run on 11 more scheduled-number series, Nov 2025 - Nov 2026:
  weekly US gas price (KXAAAGASW)      +6.0c +- 3.2 per fill, 44 weeks, 380 fills
  weekly jobless claims (KXJOBLESSCLAIMS) +8.7c +- 4.8, 46 weeks, 99 fills
  together +6.6c +- 2.7 (90 events, about 8 events a month)
Payrolls lost badly (-33c +- 20) and TSA / diesel were about 0, so they are
left out. Two winners picked from eleven tries is a weak result on its own,
which is why this runs on paper first.

Same rule and code as macro_fade, 1 contract, logged as `macro_wide`. Never
trades real money (keep [trading.macro_wide] live = false).
"""
from __future__ import annotations

import dataclasses
import logging
import os

from ..trader import held_by_event
from ..util import HttpError
from . import macro_fade
from .mentions import _series_markets

NAME = "macro_wide"
SERIES = ["KXAAAGASW", "KXJOBLESSCLAIMS"]
log = logging.getLogger("wxbot")


def candidates(markets: list[dict], p: dict, now, held: dict | None = None) -> list:
    return [dataclasses.replace(i, strategy=NAME, note=f"paper shadow; {i.note}")
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
            log.info("macro_wide %s: %s", s, exc)
    out = candidates(markets, p, ctx.now, held_by_event(ctx.orders, NAME, ctx.mode))
    log.info("macro_wide (paper shadow): %d open markets, %d candidates", len(markets), len(out))
    return out
