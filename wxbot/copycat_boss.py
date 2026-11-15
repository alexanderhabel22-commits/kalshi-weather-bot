"""Copycat's boss, v2 (Nov 15, 2026): a SHADOW judge of the traders closest to real money.

History: v1 (Nov 10) fed the model the 25 BUSIEST accounts (mostly market makers and bots we never
copy), could bench accounts for 24 h, and its reviews quietly died after being billed (3,000-token
limit with effort "medium": the answer got cut off), so its summary froze around Nov 13-14 while it
kept spending ~$0.11/day. Copycat's own tests (Nov 11-15) found that nothing measurable predicts
which traders keep winning, so the boss gets NO power until it proves it beats plain code:

  * Once a day it reads only the traders near the passing bar (score 74+), anyone who passes, and
    anyone we have copied with real money (max 20), with what plain code can't summarise well:
    where their profit seems to come from, the gap between their price and what a copy pays,
    how the price moves after their trade, maker share, price band, real vs paper results, leeway.
  * Per trader it makes a call: keep / skip / less_leeway, a guess at where the profit comes from,
    and a predicted c/contract for the next copies. Calls are LOGGED (boss.json "calls", uploaded
    in status.json) next to a plain-code baseline ("rule_skip": mostly maker fills, or nearly all
    copies at <=5c or >=95c). Nothing is benched: benches() is always empty in shadow mode.
  * tools/boss_scoreboard.py scores the calls on later paper copies. The boss only gets real skip
    power (a reviewed PR) if traders it would skip lose >= 3c/contract more than the ones it keeps
    over 200+ paper copies, and beat the plain rule. It can never add a trader, raise sizes or
    touch the dollar rails.

Cost: one Haiku call a day (~$0.01-0.04), hard cap $0.25/day via ai_budget (with the Foreman, all
server AI stays under $1/day, Alex's rule).
"""
from __future__ import annotations

import json
import logging
import os
import statistics as st
import time

from wxbot.ai_budget import Budget, estimate

log = logging.getLogger("wxbot")
HAIKU = "claude-haiku-5-5"
REVIEW_MODEL = os.environ.get("COPYCAT_BOSS_MODEL", HAIKU)   # was Opus until Nov 12 ($24/day)
SHADOW = True                          # no benches until the scoreboard says it earns them
TRIAGE_EVERY_S = 3600                  # how often step() looks at whether a review is due
DEEP_EVERY_S = 22 * 3600               # about one review a day
RETRY_S = 3 * 3600                     # after a failed review
MAX_DEEP_PER_DAY = 2
DAILY_CAP_USD = float(os.environ.get("COPYCAT_BOSS_CAP_USD", "0.25"))
MAX_ACCOUNTS = 20
NEAR_SCORE = 74.0                      # "near the bar" (copied at 80)
MAX_DIGEST_CHARS = 14000
MAX_TOKENS = 8000                      # v1's 3,000 got cut off
KEEP_CALLS = 3000
SOURCES = ["market_making", "near_certain_buys", "live_in_game", "long_shots", "informed_pre_news",
           "skill_unclear"]

SYSTEM = """You judge traders for Copycat, a small bot that copies the public trades of Kalshi \
traders with real money (1 contract per copy, ~$57 bankroll). A copy happens 10-20 seconds after \
the trader's fill, at whatever price is left, plus Kalshi's taker fee.

Facts from our own tests (Nov 11-15): copying the average trader loses about 3c per contract; \
past results, recent form, category and price band have NOT predicted who keeps winning. So be \
humble: most traders' edge, if any, does not survive the copy delay. The score formula already \
decides who is copied. Your job is only what the numbers can't show:

- Where does this trader's profit seem to come from? market_making (resting orders on both sides: \
copying their fills is pointless), near_certain_buys (95c+ "free money" that a copy pays more for), \
live_in_game (speed during games: gone by the time we copy), long_shots (lottery tickets), \
informed_pre_news (they buy before the price moves: the one style a copy can still catch), or \
skill_unclear.
- Is it copyable? Look at the gap between their price and our pay price, and whether the price \
moved our way or against us after their trade (follow_5m).
- MARKET MAKING (Alex, Nov 15: this is your job, for every trader you see): decide whether the \
trader is a market maker (yes / partly / no) and whether we should filter: none, skip_maker_fills \
(copy only their taker trades) or skip_trader. Known results (Nov 11-15): copies of maker-role \
fills from heavy-maker accounts made -1.2c +/-1.0 overall, but olekbannick7's maker fills made \
+1.6c +/-1.5, so "mostly maker" alone does NOT mean uncopyable; our 2c-of-their-price rule already \
drops maker fills we can't match. Look at our pay vs their price and follow_5m on maker fills. \
Never filter a trader who is on real money unless their REAL results support it.
- Call: keep (copying looks fine), skip (we should not copy them, with a concrete reason), or \
less_leeway (copy, but stop sooner in a slump). And predict our c/contract on their next copies.
Be specific and short (reason max 25 words, note max 25 words, plain English). This is a test of \
your judgement: your calls are scored later against what actually happened."""

REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["summary", "accounts"],
    "properties": {
        "summary": {"type": "string"},
        "accounts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["nick", "note", "call", "source", "predicted_c", "market_maker", "mm_filter", "reason"],
            "properties": {"nick": {"type": "string"}, "note": {"type": "string"},
                           "call": {"type": "string", "enum": ["keep", "skip", "less_leeway"]},
                           "source": {"type": "string", "enum": SOURCES},
                           "predicted_c": {"type": "number"},
                           "market_maker": {"type": "string", "enum": ["yes", "partly", "no"]},
                           "mm_filter": {"type": "string", "enum": ["none", "skip_maker_fills", "skip_trader"]},
                           "reason": {"type": "string"}}}}}}


def candidates(status: dict, live: dict | None) -> list[dict]:
    real = set(((live or {}).get("pnl") or {}))
    on = lambda a: a["nick"] in real or bool(a.get("on_real")) or (a.get("real_events") or 0) > 0  # noqa: E731
    out = [a for a in status.get("accounts", [])
           if a.get("verdict") in ("passes", "ready") or (a.get("score") or 0) >= NEAR_SCORE or on(a)]
    out.sort(key=lambda a: (not on(a) and a.get("verdict") not in ("passes", "ready"), -(a.get("score") or 0)))
    return out[:MAX_ACCOUNTS]


def _gap(det: dict) -> float | None:
    g = [t["pay_c"] - t["their_c"] for t in (det.get("latest") or [])
         if t.get("pay_c") is not None and t.get("their_c") is not None]
    return round(st.mean(g), 1) if g else None


MM_MIN_FILLS, MM_MAKER_PCT = 50, 70      # Copycat thread's classifier (analysis/copycat-two-stage/role.py)


def plain_mm(a: dict) -> bool:
    """Plain-code market-maker fallback for EVERY trader: 50+ logged fills, 70%+ of them maker role."""
    return (a.get("fills") or 0) >= MM_MIN_FILLS and ((a.get("detail") or {}).get("maker_pct") or 0) >= MM_MAKER_PCT


def rule_skip(a: dict) -> str:
    """Plain-code baseline the boss must beat: '' = keep."""
    det = a.get("detail") or {}
    if plain_mm(a):
        return "market maker (50+ fills, 70%+ maker)"
    bands = det.get("bands") or {}
    tot = sum(bands.values())
    if tot >= 20 and bands.get("other", 0) / tot >= 0.6:
        return "mostly <=5c or >=95c"
    return ""


def digest(status: dict, rows: list[dict], live: dict | None, benches: dict | None = None) -> str:
    """Report card for the traders that matter (near the bar, passing, or real-copied)."""
    accts = candidates(status, live)
    rp = (live or {}).get("pnl") or {}
    lines = [f"Now: {status.get('generated')}. Copycat mode: {status.get('mode')}. Rule: {status.get('rule', '')[:400]}",
             f"Real money: total ${(live or {}).get('total_pnl', 0):.2f}, open ${(live or {}).get('open_cost', 0):.2f} "
             f"of ${(live or {}).get('pool', 0):.2f} pool. Prior (average trader): {(status.get('prior') or {}).get('mu')}c/contract.",
             f"{len(accts)} traders below (near the bar, passing or real-copied); "
             f"{max(0, len(status.get('accounts', [])) - len(accts))} others left out."]
    for a in accts:
        det = a.get("detail") or {}
        ser = ", ".join(f"{x['s']} {x['n']} ({x['avg_c']}c)" for x in (det.get("series") or [])[:4])
        lines.append(
            f"- {a['nick']}: score {a.get('score')} {a.get('grade')}, verdict {a.get('verdict')}, expected "
            f"{a.get('edge_c')}c +/-{a.get('edge_sd')}, {a.get('settled')} counted copies in {a.get('events')} events "
            f"over {a.get('days')}d, last-{a.get('window')} avg {a.get('settle_c')}c, paper $ per 1-ct copy "
            f"{a.get('banked')}, leeway {a.get('leeway_c')}c. Real: {a.get('real_events') or 0} events, "
            f"${rp.get(a['nick'], 0):.2f}. Lifetime Kalshi profit ${a.get('pnl')}, cats {a.get('cats')}. "
            f"Copyability: our pay minus their price {_gap(det)}c (last 10), follow_5m {a.get('follow_5m_c')}c, "
            f"lag {a.get('lag_s')}s, maker {det.get('maker_pct')}%, median price {det.get('median_price_c')}c, "
            f"median size {det.get('median_size')}, price bands {det.get('bands')}. Markets: {ser}."
            + (f" ON REAL MONEY ({(a.get('on_real') or {}).get('tier', 'yes')})." if a.get("on_real") else "")
            + (" Plain classifier: market maker." if plain_mm(a) else "")
            + (f" Blocked categories: {a.get('blocked_cats')}." if a.get("blocked_cats") else ""))
    return "\n".join(lines)[:MAX_DIGEST_CHARS]


class Boss:
    def __init__(self, out_dir, client=None, clock=time.time, alert=None, ask=None):
        self.out = out_dir
        self.client = client
        self.clock = clock
        self.alert = alert or (lambda t, b: None)
        self._ask = ask
        p = out_dir / "boss.json"
        try:
            self.state = json.loads(p.read_text())
        except (OSError, ValueError):
            self.state = {}
        if self.state.get("v") != 2:          # v1 state: frozen summary, stale notes, benches
            self.state = {"v": 2, "calls": self.state.get("calls", []), "usage": self.state.get("usage")}
        self.state.setdefault("notes", {})
        self.state.setdefault("summary", "")
        self.state.setdefault("calls", [])
        self.state.setdefault("triage_at", 0.0)
        self.state.setdefault("deep_at", 0.0)
        self.state.setdefault("ok_at", 0.0)
        self.state.setdefault("deep_day", "")
        self.state.setdefault("deep_n", 0)
        self.state.setdefault("last_error", "")
        self.state["usage"] = self.state.get("usage") or {"in": 0, "out": 0}
        self.model = REVIEW_MODEL
        self.budget = Budget(out_dir / "boss_spend.json", DAILY_CAP_USD, clock=clock)

    def save(self) -> None:
        (self.out / "boss.json").write_text(json.dumps(self.state, indent=1))

    def benches(self, now: float) -> dict:
        """Shadow mode: the boss benches nobody (its calls are only logged and scored)."""
        return {}

    def ask(self, model: str, text: str, schema: dict, effort: str, max_tokens: int) -> dict:
        if not self.budget.allow(estimate(model, SYSTEM, text, max_tokens=max_tokens)):
            raise RuntimeError(f"daily AI cap ${self.budget.cap:.2f} reached")
        if self._ask:
            self.budget.charge(model, len(text) / 3, 200)
            return self._ask(model, text, schema)
        r = self.client.messages.create(
            model=model, max_tokens=max_tokens, system=SYSTEM,
            messages=[{"role": "user", "content": text}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}})
        u = self.state["usage"]
        u["in"] += getattr(r.usage, "input_tokens", 0)
        u["out"] += getattr(r.usage, "output_tokens", 0)
        self.budget.charge(model, getattr(r.usage, "input_tokens", 0), getattr(r.usage, "output_tokens", 0))
        if r.stop_reason == "refusal":
            raise RuntimeError("model refused")
        if r.stop_reason == "max_tokens":
            raise RuntimeError(f"answer cut off at {max_tokens} tokens")
        text_out = next((b.text for b in r.content if b.type == "text"), None)
        if text_out is None:
            raise RuntimeError("no text in the answer")
        return json.loads(text_out)

    def summary(self, status: dict | None = None) -> dict:
        mm = sorted(a["nick"] for a in (status or {}).get("accounts", []) if plain_mm(a))
        return {"plain_mm": {"count": len(mm), "nicks": mm[:300]}, "summary": self.state.get("summary"), "shadow": SHADOW, "usage": self.state.get("usage"),
                "reviews_today": self.state.get("deep_n"), "last_ok": self.state.get("ok_at"),
                "last_error": self.state.get("last_error"), "spend": self.budget.summary(),
                "model": self.model, "benches": {}, "calls": self.state["calls"][-400:]}

    def step(self, status: dict, rows: list[dict], live: dict | None) -> bool:
        """Runs the daily review when due. Returns True when it logged new calls."""
        now = self.clock()
        if (self.client is None and self._ask is None) or now - self.state["triage_at"] < TRIAGE_EVERY_S:
            return False
        self.state["triage_at"] = now
        accts = candidates(status, live)
        if not accts:
            return False
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        if day != self.state["deep_day"]:
            self.state.update(deep_day=day, deep_n=0)
        wait = DEEP_EVERY_S if self.state["ok_at"] >= self.state["deep_at"] else RETRY_S
        if self.state["deep_n"] >= MAX_DEEP_PER_DAY or now - self.state["deep_at"] < wait:
            self.save()
            return False
        # count the review before the call: a failed call is still billed (Nov 11 lesson)
        self.state.update(deep_at=now, deep_n=self.state["deep_n"] + 1)
        try:
            r = self.ask(self.model, digest(status, rows, live), REVIEW_SCHEMA, "low", MAX_TOKENS)
        except Exception as exc:  # noqa: BLE001
            self.state["last_error"] = f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(now))}Z {exc}"[:300]
            log.warning("copycat boss review: %s", exc)
            self.save()
            return False
        by = {a["nick"]: a for a in accts}
        self.state.update(ok_at=now, last_error="", summary=(r.get("summary") or "")[:600])
        for c in r.get("accounts", []):
            a = by.get(c.get("nick"))
            if a is None:
                continue
            self.state["notes"][a["nick"]] = (c.get("note") or "")[:200]
            self.state["calls"].append({
                "at": round(now), "nick": a["nick"], "call": c.get("call"), "source": c.get("source"),
                "predicted_c": c.get("predicted_c"), "reason": (c.get("reason") or "")[:200],
                "market_maker": c.get("market_maker"), "mm_filter": c.get("mm_filter"),
                "plain_mm": plain_mm(a), "on_real": bool(a.get("on_real")),
                "rule_skip": rule_skip(a), "score": a.get("score"), "verdict": a.get("verdict"),
                "settled": a.get("settled"), "banked": a.get("banked")})
        self.state["calls"] = self.state["calls"][-KEEP_CALLS:]
        self.save()
        return True


def make_client():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    return anthropic.Anthropic(api_key=key, max_retries=0, timeout=120)
