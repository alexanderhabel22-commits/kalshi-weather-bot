"""Hard daily dollar cap for every Claude API caller on the droplet (Nov 12, 2026).

Nov 11 the Copycat boss spent $24 on Opus in one day (its estimate was $0.20-0.70). Every AI job
now books what each call costs into its own small ledger and refuses a call that could push the
day (UTC) over its cap. The check runs before the call with a worst-case estimate (the whole
prompt plus max_tokens of output), so the cap holds even when a call fails after billing.
Prices are list prices per million tokens, rounded up, so the ledger over-counts rather than under.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

PRICES = {"haiku": (1.0, 5.0), "sonnet": (3.0, 15.0), "opus": (5.0, 25.0), "fable": (15.0, 75.0)}


def price(model: str) -> tuple[float, float]:
    m = (model or "").lower()
    for k, v in PRICES.items():
        if k in m:
            return v
    return PRICES["fable"]                       # unknown model: assume the most expensive


def cost(model: str, tok_in: float, tok_out: float) -> float:
    pi, po = price(model)
    return (tok_in * pi + tok_out * po) / 1e6


def estimate(model: str, *texts: str, max_tokens: int) -> float:
    """Worst case before the call: ~3 characters per token for the prompt, full max_tokens out."""
    return cost(model, sum(len(t or "") for t in texts) / 3 + 200, max_tokens)


class Budget:
    def __init__(self, path: Path | str, cap_usd: float, clock=time.time):
        self.path = Path(path)
        self.cap = float(cap_usd)
        self.clock = clock
        try:
            self.state = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.state = {}

    def _day(self) -> dict:
        day = time.strftime("%Y-%m-%d", time.gmtime(self.clock()))
        if self.state.get("day") != day:
            hist = dict(self.state.get("history") or {})
            if self.state.get("day"):
                hist[self.state["day"]] = round(self.state.get("usd", 0.0), 4)
            self.state = {"day": day, "usd": 0.0, "calls": 0, "refused": 0,
                          "history": dict(sorted(hist.items())[-35:])}
        for k, v in (("usd", 0.0), ("calls", 0), ("refused", 0), ("history", {})):
            self.state.setdefault(k, v)
        return self.state

    def spent(self) -> float:
        return self._day()["usd"]

    def allow(self, est_usd: float) -> bool:
        st = self._day()
        if st["usd"] + est_usd > self.cap:
            st["refused"] += 1
            self.save()
            return False
        return True

    def charge(self, model: str, tok_in: float, tok_out: float) -> float:
        st = self._day()
        c = cost(model, tok_in, tok_out)
        st["usd"] = round(st["usd"] + c, 6)
        st["calls"] += 1
        self.save()
        return c

    def save(self) -> None:
        try:
            self.path.write_text(json.dumps(self.state, indent=1))
        except OSError:
            pass

    def summary(self) -> dict:
        st = self._day()
        return {"day": st["day"], "usd": round(st["usd"], 4), "cap": self.cap, "calls": st["calls"],
                "refused": st["refused"], "history": st.get("history", {})}
