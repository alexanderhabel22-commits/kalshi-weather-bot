"""PAPER shadow: the same Mentions NO bet, spread over many more words.

Idea from the leaderboard study (Nov 10, 2026): the people who made $0.4-1.7M on
Kalshi Mentions earn about the same per contract as we do (1-8c), but trade
4,000-16,000 different Mentions markets. Our live rule takes at most 3 words per
event and only words with 1,000+ contracts traded. This shadow logs 1-contract
paper orders on the EXTRA words the live rule leaves out (same 75-90c NO band,
same event window), so the Dec 3 Mentions review can see whether breadth adds
profit or just adds thin, worse words.

It never trades real money (keep [trading.mentions_wide] live = false), places
nothing the live rule would place itself, and makes no Kalshi calls of its own:
it reuses the market list the live Mentions scan read in the same run.
"""
from __future__ import annotations

import logging

from ..trader import Intent, held_by_event, per_event_pick
from . import mentions

NAME = "mentions_wide"
log = logging.getLogger("wxbot")


def extra(markets: list[dict], series: set[str], wide: dict, live: dict,
          bankroll: float | None, held: dict | None, live_held: dict | None) -> list[Intent]:
    """Words the wide rule picks that the live rule (with its own open orders) doesn't."""
    live_pick = {i.ticker for i in mentions.candidates(markets, series, live, bankroll, live_held)}
    for tks in (live_held or {}).values():
        live_pick |= set(tks)
    w = dict(wide)
    w["max_words_per_event"] = 10 ** 6            # per-event limit applied below, after removing live words
    w.pop("word_pct", None)
    allc = [i for i in mentions.candidates(markets, series, w, bankroll, None) if i.ticker not in live_pick]
    n_words = int(wide.get("max_words_per_event", 20))
    by_ev: dict[str, list[Intent]] = {}
    for i in allc:
        by_ev.setdefault(i.event, []).append(i)
    out = []
    for ev in sorted(by_ev):
        for i in per_event_pick(sorted(by_ev[ev], key=lambda x: x.price), held, n_words):
            out.append(Intent(NAME, i.ticker, i.event, i.side, i.price, int(wide.get("contracts", 1)),
                              i.close, i.title, f"wide shadow; {i.note}; vol {i.volume:.0f}", i.volume))
    return out


def propose(ctx) -> list[Intent]:
    scan = mentions.LAST_SCAN
    if not scan or scan.get("at") != ctx.now:
        return []                                  # live scan didn't run this pass (it scans every 30 min)
    live = (ctx.cfg.raw.get("trading", {}) or {}).get("mentions", {}) or {}
    out = extra(scan["markets"], scan["series"], ctx.params, live, getattr(ctx.cfg, "bankroll", None),
                held_by_event(ctx.orders, NAME, ctx.mode), held_by_event(ctx.orders, "mentions"))
    log.info("mentions_wide (paper shadow): %d extra words", len(out))
    return out
