"""Fed/CPI longshot fade on Kalshi's weekly "Best AI" market (KXLLM1).

Research (Nov 14, 2026, thread "Find more Fed-style fade events",
/mnt/project-files/analysis/fade-events/RESULTS.md): the exact macro_fade replay
(hourly real bid/ask, NO at best bid + 1c in the 71-95c band, 2 h to 21 days
before close, rest to close, fill only on a later print, 1c fee) on ~120
non-economic series. Pooled it made ~0 (+0.4c), so the Fed edge does NOT
carry over in general. KXLLM1 was the one series that held up:
  +5.1c +- 1.8 per fill, 85 fills, 40 of 43 events positive,
  2025 +2.5c, 2026 +6.2c; Polymarket's "best AI model" markets +3.2c.
Caveats: one series picked from ~70 tries; its sibling KXTOPMODEL lost; the
rule fired 0 times Sep 14 - Nov 9 2026 (longshots sat at 1-4c). One upset
costs ~90c a contract, so keep the size small.

Same rule and code as macro_fade, logged as `ai_fade`. REAL money since
Nov 14 (Alex: "+5c is +5c let's add it").
"""
from __future__ import annotations

import dataclasses
import logging
import os

from ..trader import held_by_event
from ..util import HttpError
from . import macro_fade
from .mentions import _series_markets

NAME = "ai_fade"
SERIES = ["KXLLM1"]
log = logging.getLogger("wxbot")


def candidates(markets: list[dict], p: dict, now, held: dict | None = None) -> list:
    return [dataclasses.replace(i, strategy=NAME, note=f"AI fade; {i.note}")
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
            log.info("ai_fade %s: %s", s, exc)
    out = candidates(markets, p, ctx.now, held_by_event(ctx.orders, NAME, ctx.mode))
    log.info("ai_fade: %d open markets, %d candidates", len(markets), len(out))
    return out
