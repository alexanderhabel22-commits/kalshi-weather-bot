"""Copycat's boss (Nov 10, 2026): an AI manager for the accounts Copycat follows.

Every hour Claude Haiku looks over the report card (cheap triage). A full review (also Haiku since
Nov 12) runs every 6 hours when anything changed, or when triage asks for it, at most 4 a day.
Nov 11 the old setup (Opus reviews of the whole report card) cost $24 in one day, so every call
now goes through a hard daily dollar cap (ai_budget.py, $0.30/day by default) and the report card
sent to the model is trimmed to the busiest accounts. The boss:
  - writes a short note per account (shown on the dashboard), in plain words;
  - can BENCH an account for 24 hours (renewable) when something looks wrong that the numbers
    can't see yet: a style change (new market type, much bigger or smaller bets, different prices),
    a streak of losses in one market type, trading that looks like a market maker (copying their
    fills is pointless), a bot that went haywire;
  - can lift a bench it set itself.
It can't promote anyone, raise sizes or change the money rails: those come from the score formula
and copycat_control.json. Cost: a few cents a day, never more than the cap.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter

from wxbot.ai_budget import Budget, estimate

log = logging.getLogger("wxbot")
HAIKU = "claude-haiku-5-5"
REVIEW_MODEL = os.environ.get("COPYCAT_BOSS_MODEL", HAIKU)   # was Opus until Nov 12 ($24/day)
TRIAGE_EVERY_S = 3600
DEEP_EVERY_S = 6 * 3600
MAX_DEEP_PER_DAY = 4
DAILY_CAP_USD = float(os.environ.get("COPYCAT_BOSS_CAP_USD", "0.30"))
MAX_ACCOUNTS = 25                      # busiest accounts only in the model's report card
MAX_DIGEST_CHARS = 12000
BENCH_S = 24 * 3600

SYSTEM = """You are the boss of Copycat, a small bot that copies the public trades of profitable \
Kalshi traders with real money (1-3 contracts per copy, about $50 bankroll). Copies happen about \
10-20 seconds after the trader's own fill, at whatever price is left, plus Kalshi's fee.

Each account has a score (0-100, like a test: 80 = break-even even in the pessimistic case, 90+ = \
clearly profitable) and a letter grade, computed by a fixed formula from every copy so far. The \
formula decides who is copied and at what size. Your job is the judgement the formula can't make:

- Read each account's numbers and its recent trades. Write one short plain-English note per \
account that a non-trader understands (max 25 words): what they trade and how it's going for us.
- BENCH an account (stop copying it for 24 hours) only for a concrete reason the numbers can't \
show yet: it changed style (new kind of market, much bigger or smaller bets, very different \
prices), it looks like a market maker (huge counts of tiny fills on both sides - copying a market \
maker's fills is pointless), its recent losses are concentrated in one market type it never \
traded before, or it looks broken. Be specific in the reason.
- Don't bench an account just for a normal losing run; the formula already gives proven accounts \
leeway and new ones none. Don't bench on hunches. UNBENCH an account you benched earlier when the \
reason no longer holds.
- Be candid. If the whole program looks like it isn't working, say so in the summary.

Triage mode: decide only whether a full review is needed now (something changed in a way that \
might need a bench or unbench). Full review: notes for every account with copies, plus actions."""

TRIAGE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["review_now", "why"],
                 "properties": {"review_now": {"type": "boolean"}, "why": {"type": "string"}}}
REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["summary", "accounts", "alert"],
    "properties": {
        "summary": {"type": "string"},
        "alert": {"type": "string", "description": "empty unless Alex should get a phone push"},
        "accounts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["nick", "note", "action", "reason"],
            "properties": {"nick": {"type": "string"}, "note": {"type": "string"},
                           "action": {"type": "string", "enum": ["none", "bench", "unbench"]},
                           "reason": {"type": "string"}}}}}}


def digest(status: dict, rows: list[dict], live: dict | None, benches: dict) -> str:
    """Compact text for the model: report card + each account's recent trade pattern."""
    by = {}
    for r in rows:
        if r.get("kind") == "fill" and not r.get("backfill"):
            by.setdefault(r["nick"], []).append(r)
    lines = [f"Now: {status.get('generated')}. Mode: {status.get('mode')}."]
    if live:
        lines.append(f"Real money: total P&L ${live.get('total_pnl', 0):.2f}, open ${live.get('open_cost', 0):.2f} "
                     f"of ${live.get('pool', 0):.2f} pool, own-stop benches: {list((live.get('benched') or {}))}.")
    lines.append("Boss benches in force: " + (", ".join(f"{k} ({v['reason']})" for k, v in benches.items()) or "none"))
    accts = sorted((a for a in status.get("accounts", []) if a.get("fills")),
                   key=lambda a: (a["nick"] not in benches, -(a.get("fills") or 0)))
    if len(accts) > MAX_ACCOUNTS:
        lines.append(f"({len(accts) - MAX_ACCOUNTS} quieter accounts left out.)")
    for a in accts[:MAX_ACCOUNTS]:
        f = by.get(a["nick"], [])[-60:]
        ser = Counter(x["ticker"].split("-")[0] for x in f if x.get("ticker"))
        sizes = sorted(float(x.get("count") or 0) for x in f)
        prices = sorted(round((x["yes_price"] if x.get("side") == "yes" else 1 - x["yes_price"]) * 100)
                        for x in f if x.get("yes_price") is not None)
        roles = Counter(x.get("role") for x in f)
        med = lambda v: v[len(v) // 2] if v else None  # noqa: E731
        lines.append(
            f"- {a['nick']}: score {a.get('score')} {a.get('grade')}, verdict {a.get('verdict')}, "
            f"{a.get('fills')} copies/{a.get('settled')} settled over {a.get('days')}d, last-{a.get('window')} avg "
            f"{a.get('settle_c')}c (pessimistic {a.get('settle_low_c')}c), leeway {a.get('leeway_c')}c, "
            f"lifetime Kalshi profit ${a.get('pnl')}, cats {a.get('cats')}, real P&L "
            f"${(live or {}).get('pnl', {}).get(a['nick'], 0):.2f}. Recent {len(f)} fills: markets "
            f"{dict(ser.most_common(4))}, median size {med(sizes)}, price range {prices[:1]}-{prices[-1:]}c "
            f"(median {med(prices)}c), maker/taker {dict(roles)}.")
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
        self.state.setdefault("benches", {})   # nick -> {"reason", "until"}
        self.state.setdefault("notes", {})
        self.state.setdefault("summary", "")
        self.state.setdefault("triage_at", 0.0)
        self.state.setdefault("deep_at", 0.0)
        self.state.setdefault("deep_day", "")
        self.state.setdefault("deep_n", 0)
        self.state.setdefault("last_fp", "")
        self.state.setdefault("usage", {"in": 0, "out": 0})
        self.model = REVIEW_MODEL
        self.budget = Budget(out_dir / "boss_spend.json", DAILY_CAP_USD, clock=clock)

    def save(self) -> None:
        (self.out / "boss.json").write_text(json.dumps(self.state, indent=1))

    def benches(self, now: float) -> dict:
        b = {k: v for k, v in self.state["benches"].items() if v.get("until", 0) > now}
        self.state["benches"] = b
        return b

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
        if r.stop_reason == "refusal":
            raise RuntimeError("model refused")
        u = self.state["usage"]
        u["in"] += getattr(r.usage, "input_tokens", 0)
        u["out"] += getattr(r.usage, "output_tokens", 0)
        self.budget.charge(model, getattr(r.usage, "input_tokens", 0), getattr(r.usage, "output_tokens", 0))
        return json.loads(next(b.text for b in r.content if b.type == "text"))

    def step(self, status: dict, rows: list[dict], live: dict | None) -> bool:
        """Runs triage/review when due. Returns True when it changed notes or benches."""
        now = self.clock()
        if (self.client is None and self._ask is None) or now - self.state["triage_at"] < TRIAGE_EVERY_S:
            return False
        self.state["triage_at"] = now
        if not any(a.get("fills") for a in status.get("accounts", [])):
            return False
        text = digest(status, rows, live, self.benches(now))
        fp = str(sorted((a["nick"], a.get("settled"), a.get("verdict")) for a in status.get("accounts", [])))
        changed = fp != self.state["last_fp"]
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        if day != self.state["deep_day"]:
            self.state.update(deep_day=day, deep_n=0)
        deep = False
        if self.state["deep_n"] < MAX_DEEP_PER_DAY:
            if changed and now - self.state["deep_at"] >= DEEP_EVERY_S:
                deep = True
            else:
                try:
                    t = self.ask(HAIKU, "TRIAGE.\n" + text, TRIAGE_SCHEMA, "low", 400)
                    deep = bool(t.get("review_now"))
                except Exception as exc:  # noqa: BLE001
                    log.warning("boss triage: %s", exc)
        if not deep:
            self.save()
            return False
        # count the review before the call: a failed call is still billed (Nov 11 lesson)
        self.state.update(deep_at=now, deep_n=self.state["deep_n"] + 1)
        try:
            r = self.ask(self.model, "FULL REVIEW.\n" + text, REVIEW_SCHEMA, "medium", 3000)
        except Exception as exc:  # noqa: BLE001
            log.warning("boss review: %s", exc)
            self.save()
            return False
        self.state.update(last_fp=fp, summary=r.get("summary", "")[:600])
        for a in r.get("accounts", []):
            n = a.get("nick")
            if not n:
                continue
            self.state["notes"][n] = (a.get("note") or "")[:200]
            if a.get("action") == "bench":
                self.state["benches"][n] = {"reason": (a.get("reason") or "")[:200], "until": now + BENCH_S}
            elif a.get("action") == "unbench":
                self.state["benches"].pop(n, None)
        if r.get("alert"):
            self.alert("Copycat boss", r["alert"][:400])
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
