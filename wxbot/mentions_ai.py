"""SHADOW test: an AI guess at how likely each Mentions word is to be said.

Before a Mentions NO bet goes out, Claude Haiku 5.5 (with a couple of web
searches for current news) estimates the chance the speaker says the word.
The score is only LOGGED: next to the bet in trade_orders.csv (note column,
"ai p=0.35") and in data/mentions_ai.csv. Nothing about sizing or which words
we bet changes. At the Mentions review (~Dec 3) `python bot.py mentions-ai`
shows whether skipping the high scores would have helped, e.g. the topical
Paramount "Netflix" NO that fell 82 -> 51c.

Cost: one call per event (all of its words together), at most 2 searches per
call, at most `max_calls_per_day` calls: about $3-5/month. Without
ANTHROPIC_API_KEY it does nothing. It can never stop or slow trading by more
than `budget_s` seconds per run.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import time
from datetime import timedelta
from zoneinfo import ZoneInfo

from .util import DATA_DIR, parse_iso

log = logging.getLogger("wxbot")
ET = ZoneInfo("America/New_York")
LOG = DATA_DIR / "mentions_ai.csv"
FIELDS = ["scored_at", "event", "ticker", "word", "no_price", "p_said", "topical", "reason",
          "model", "searches", "in_tok", "out_tok", "event_at"]

DEFAULTS = {
    "enabled": True,
    "model": "claude-haiku-5-5",
    "max_calls_per_day": 25,        # ~7 Mentions events a day; one call each
    "max_events_per_run": 3,        # the rest are scored next run
    "budget_s": 60,                 # stop scoring this many seconds into a run
    "max_searches": 2,              # web searches per call ($10 per 1,000)
    "web_search_tool": "web_search_20250305",
    "keep_days": 21,
}
SEARCH_TOOLS = ("web_search_20250305", "web_search_20260209")

SYSTEM = """You forecast Kalshi "Mentions" markets: will a speaker say a given word or phrase during an
upcoming event (speech, press briefing, interview, broadcast)?

Search the web for CURRENT news (last few days) about the speaker, the event and the topics in the word
list. A word is TOPICAL when it is tied to something in the news right now that the speaker is likely to
be asked about or want to talk about (a deal, a lawsuit, a feud, a company in the headlines). Topical
words get said far more often than their usual rate.

For each word give p_said: your calibrated probability (0 to 1) that it is said, following the market's
rules (usually exact word or close variants, plurals count). Most words in these lists are not said, so
do not inflate. Keep each reason under 20 words and mention the news item if there is one.

Answer with JSON only, no other text:
{"words": [{"ticker": "...", "p_said": 0.0, "topical": false, "reason": "..."}]}"""


def settings(cfg) -> dict:
    p = dict(DEFAULTS)
    try:
        p.update((cfg.raw.get("trading", {}).get("mentions", {}) or {}).get("ai", {}) or {})
    except AttributeError:
        pass
    return p


def make_client():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    try:
        import anthropic
    except ImportError:
        log.info("mentions ai: anthropic package missing")
        return None
    return anthropic.Anthropic(api_key=key, max_retries=1, timeout=45)


def word_of(m: dict) -> str:
    w = (m.get("yes_sub_title") or m.get("subtitle") or "").strip()
    if w:
        return w
    t = m.get("title") or ""
    hit = re.search(r"say ['\"“]?(.+?)['\"”]?\??$", t)
    return hit.group(1) if hit else (m.get("ticker") or "").split("-")[-1]


def prompt(event: str, markets: list[dict]) -> str:
    m0 = markets[0]
    when = parse_iso(m0.get("occurrence_datetime")) or parse_iso(m0.get("close_time"))
    when_s = when.astimezone(ET).strftime("%a %b %d %Y %I:%M %p ET") if when else "unknown"
    rules = (m0.get("rules_primary") or "")[:700]
    lines = [f"Event: {event}", f"Market question example: {m0.get('title') or ''}",
             f"Event time: {when_s}"]
    if rules:
        lines.append(f"Rules: {rules}")
    lines.append("Words:")
    lines += [f"- ticker {m.get('ticker')}: {word_of(m)}" for m in markets]
    return "\n".join(lines)


def parse(text: str) -> list[dict]:
    hit = re.search(r"\{.*\}", text or "", re.S)
    if not hit:
        return []
    try:
        js = json.loads(hit.group(0))
    except ValueError:
        return []
    out = []
    for w in js.get("words") or []:
        try:
            p = min(1.0, max(0.0, float(w.get("p_said"))))
        except (TypeError, ValueError):
            continue
        out.append({"ticker": str(w.get("ticker") or ""), "p": round(p, 3),
                    "topical": bool(w.get("topical")), "reason": str(w.get("reason") or "")[:200]})
    return out


def ask(client, p: dict, state: dict, text: str) -> tuple[list[dict], dict]:
    """One Haiku call with web search. Tries the other search-tool version once if
    the configured one is rejected, and remembers the one that worked."""
    import anthropic
    first = state.get("search_tool") or p["web_search_tool"]
    tried = [first] + [t for t in SEARCH_TOOLS if t != first]
    last = None
    for tool in tried:
        try:
            r = client.messages.create(
                model=p["model"], max_tokens=4000, system=SYSTEM,
                messages=[{"role": "user", "content": text}],
                tools=[{"type": tool, "name": "web_search", "max_uses": int(p["max_searches"])}],
                output_config={"effort": "low"},
            )
        except anthropic.BadRequestError as exc:
            last = exc
            continue
        state["search_tool"] = tool
        if r.stop_reason == "refusal":
            raise RuntimeError("model refused")
        body = "".join(getattr(b, "text", "") for b in r.content if b.type == "text")
        u = r.usage
        stu = getattr(u, "server_tool_use", None)
        usage = {"in": getattr(u, "input_tokens", 0) or 0, "out": getattr(u, "output_tokens", 0) or 0,
                 "searches": (getattr(stu, "web_search_requests", 0) or 0) if stu else 0}
        return parse(body), usage
    raise RuntimeError(f"web search rejected: {last}")


def append_log(rows: list[dict]) -> None:
    if not rows:
        return
    new = not LOG.exists()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)


def note(score: dict) -> str:
    return f"ai p={score['p']:.2f}" + (" topical" if score.get("topical") else "")


def annotate(ctx, intents: list, markets: list[dict], client=None, clock=time.monotonic) -> int:
    """Score the words we are about to bet (shadow) and add the score to each
    intent's note. Returns how many calls were made. Never raises."""
    try:
        return _annotate(ctx, intents, markets, client, clock)
    except Exception as exc:          # the shadow test must never break trading
        log.warning("mentions ai failed: %s", exc)
        return 0


def _annotate(ctx, intents, markets, client, clock) -> int:
    p = settings(ctx.cfg)
    if not p["enabled"] or not intents:
        return 0
    st = ctx.state.setdefault("ai", {})
    scores = st.setdefault("scores", {})
    cutoff = ctx.now - timedelta(days=float(p["keep_days"]))
    for k in [k for k, v in scores.items() if (parse_iso(v.get("at")) or ctx.now) < cutoff]:
        del scores[k]
    day = ctx.now.astimezone(ET).date().isoformat()
    if st.get("day") != day:
        st["day"], st["calls"] = day, 0

    need: dict[str, list] = {}
    for it in intents:
        if it.ticker not in scores:
            need.setdefault(it.event, []).append(it)
    calls = 0
    if need:
        client = client if client is not None else make_client()
    if need and client is not None:
        by_t = {m.get("ticker"): m for m in markets}
        t0 = clock()
        for ev in sorted(need)[:int(p["max_events_per_run"])]:
            if st["calls"] >= int(p["max_calls_per_day"]) or clock() - t0 > float(p["budget_s"]):
                break
            its = need[ev]
            ms = [by_t.get(it.ticker) or {"ticker": it.ticker, "title": it.title} for it in its]
            st["calls"] += 1
            calls += 1
            try:
                got, usage = ask(client, p, st, prompt(ev, ms))
            except Exception as exc:
                log.warning("mentions ai %s: %s", ev, exc)
                continue
            got = {g["ticker"]: g for g in got}
            at = ctx.now.isoformat(timespec="seconds")
            rows = []
            for it, m in zip(its, ms):
                g = got.get(it.ticker)
                if not g:
                    continue
                scores[it.ticker] = {"p": g["p"], "topical": g["topical"], "at": at}
                rows.append({"scored_at": at, "event": ev, "ticker": it.ticker, "word": word_of(m),
                             "no_price": f"{it.price:.2f}", "p_said": g["p"], "topical": int(g["topical"]),
                             "reason": g["reason"], "model": p["model"], "searches": usage["searches"],
                             "in_tok": usage["in"], "out_tok": usage["out"],
                             "event_at": m.get("occurrence_datetime") or ""})
            append_log(rows)
            log.info("mentions ai %s: %s (%d searches, %d in / %d out tokens)", ev,
                     ", ".join(f"{r['word']}={r['p_said']:.2f}" for r in rows),
                     usage["searches"], usage["in"], usage["out"])
    for it in intents:
        s = scores.get(it.ticker)
        if s and "ai p=" not in (it.note or ""):
            it.note = f"{it.note} | {note(s)}" if it.note else note(s)
    return calls


# ---------------------------------------------------------------- review (Dec 3)

def _f(x, d=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def load_scores() -> dict[str, dict]:
    """First score per ticker (the one made before the bet)."""
    out: dict[str, dict] = {}
    if not LOG.exists():
        return out
    with LOG.open(newline="") as f:
        for r in csv.DictReader(f):
            out.setdefault(r.get("ticker") or "", r)
    return out


def review(rows: list[dict], scores: dict[str, dict], thresholds=(0.3, 0.4, 0.5, 0.6)) -> dict:
    """Settled, filled Mentions bets joined with their AI score. For each threshold:
    what the bets scored at or above it made, i.e. what skipping them would change."""
    bets = []
    for r in rows:
        if r.get("strategy") != "mentions" or r.get("status") != "settled" or _f(r.get("filled")) <= 0:
            continue
        s = scores.get(r.get("ticker") or "")
        if not s:
            continue
        bets.append({"mode": r.get("mode"), "ticker": r.get("ticker"), "p": _f(s.get("p_said")),
                     "topical": s.get("topical") in ("1", "True", "true"), "pnl": _f(r.get("pnl")),
                     "filled": _f(r.get("filled")), "price": _f(r.get("price")),
                     "won": _f(r.get("pnl")) > 0})
    out = {"bets": len(bets), "pnl": round(sum(b["pnl"] for b in bets), 2), "cuts": []}
    for t in thresholds:
        hi = [b for b in bets if b["p"] >= t]
        out["cuts"].append({"threshold": t, "n": len(hi), "lost": sum(1 for b in hi if not b["won"]),
                            "pnl_skipped": round(sum(b["pnl"] for b in hi), 2),
                            "pnl_after": round(out["pnl"] - sum(b["pnl"] for b in hi), 2)})
    top = [b for b in bets if b["topical"]]
    out["topical"] = {"n": len(top), "lost": sum(1 for b in top if not b["won"]),
                      "pnl": round(sum(b["pnl"] for b in top), 2)}
    # calibration: bucketed AI p vs how often the word was actually said (our NO lost)
    buckets = [(0, 0.1), (0.1, 0.2), (0.2, 0.35), (0.35, 0.5), (0.5, 1.01)]
    out["calibration"] = [{"bucket": f"{lo:.2f}-{min(hi, 1):.2f}",
                           "n": len(bb := [b for b in bets if lo <= b["p"] < hi]),
                           "said": sum(1 for b in bb if not b["won"])} for lo, hi in buckets]
    return out


def render(r: dict) -> str:
    lines = [f"Mentions AI shadow test: {r['bets']} settled bets with a score, P&L ${r['pnl']:+.2f}", "",
             "If we had skipped words the AI scored at or above...",
             "| skip if p >= | bets skipped | of those lost | their P&L | P&L after skipping |",
             "|---|---|---|---|---|"]
    lines += [f"| {c['threshold']:.2f} | {c['n']} | {c['lost']} | ${c['pnl_skipped']:+.2f} | "
              f"${c['pnl_after']:+.2f} |" for c in r["cuts"]]
    t = r["topical"]
    lines += ["", f"Flagged topical: {t['n']} bets, {t['lost']} lost, P&L ${t['pnl']:+.2f}", "",
              "| AI p_said | bets | word was said |", "|---|---|---|"]
    lines += [f"| {c['bucket']} | {c['n']} | {c['said']} |" for c in r["calibration"]]
    lines += ["", "Verdict rule (set Nov 10): only switch a skip rule on if it would have raised P&L "
              "on 30+ scored bets AND the high-score bucket loses clearly more often than the low ones."]
    return "\n".join(lines)


def main() -> int:
    from . import trader
    print(render(review(trader.load_all(), load_scores())))
    return 0

