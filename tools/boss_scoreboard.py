"""Score the Copycat boss's shadow calls (Nov 15, 2026). Read-only.

Usage: python tools/boss_scoreboard.py DIR STATUS
  DIR    holds copycat-*.jsonl(.gz) hourly logs (branch copycat-data, folder copycat/)
  STATUS copycat/status.json from the same branch (boss.calls are in it)

For every call (trader, time, keep/skip/less_leeway, plus the plain rule's skip/keep) it takes that
trader's counted copies (buyable, settled) AFTER the call and before that trader's next call, and
averages our c/contract. The boss earns real skip power only if, over 200+ forward copies:
  * traders it said "skip" lost >= 3c/contract more than traders it said "keep", AND
  * that gap beats the plain rule's gap (rule skip vs rule keep) on the same calls.
Market results come from the logs' snapshots/results (unsettled copies are ignored).
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wxbot.copycat import grade, load_rows  # noqa: E402

GAP_C = 3.0
MIN_COPIES = 200


def forward(calls: list[dict], trades: dict[str, list[dict]]) -> list[dict]:
    """Each call with the c/contract values of that trader's counted copies until its next call."""
    by = defaultdict(list)
    for c in calls:
        by[c["nick"]].append(c)
    out = []
    for nick, cs in by.items():
        cs.sort(key=lambda c: c["at"])
        tr = [t for t in trades.get(nick, []) if t.get("ok") and t.get("c") is not None and t.get("ts")]
        for i, c in enumerate(cs):
            end = cs[i + 1]["at"] if i + 1 < len(cs) else float("inf")
            w = [t for t in tr if c["at"] < t["ts"] <= end]
            out.append({**c, "fwd": [t["c"] for t in w], "fwd_maker": [t["c"] for t in w if t.get("role") == "maker"]})
    return out


def _mean(v):
    return round(st.mean(v), 2) if v else None


def score(scored: list[dict]) -> dict:
    groups = {"boss_skip": [], "boss_keep": [], "boss_less": [], "rule_skip": [], "rule_keep": []}
    for c in scored:
        k = {"skip": "boss_skip", "keep": "boss_keep", "less_leeway": "boss_less"}.get(c.get("call"))
        if k:
            groups[k] += c["fwd"]
        groups["rule_skip" if c.get("rule_skip") else "rule_keep"] += c["fwd"]
    res = {k: {"copies": len(v), "c": _mean(v)} for k, v in groups.items()}
    keep = groups["boss_keep"] + groups["boss_less"]
    n = len(keep) + len(groups["boss_skip"])
    boss_gap = (st.mean(keep) - st.mean(groups["boss_skip"])) if keep and groups["boss_skip"] else None
    rule_gap = (st.mean(groups["rule_keep"]) - st.mean(groups["rule_skip"])
                if groups["rule_keep"] and groups["rule_skip"] else None)
    passed = (n >= MIN_COPIES and boss_gap is not None and boss_gap >= GAP_C
              and (rule_gap is None or boss_gap > rule_gap))
    # market-making filter: what "skip their maker fills" would have removed (helps only if clearly < 0)
    boss_mm = [x for c in scored if c.get("mm_filter") == "skip_maker_fills" for x in c.get("fwd_maker", [])]
    plain_mm = [x for c in scored if c.get("plain_mm") for x in c.get("fwd_maker", [])]
    res["mm_filter"] = {"boss_removed": {"copies": len(boss_mm), "c": _mean(boss_mm)},
                        "plain_removed": {"copies": len(plain_mm), "c": _mean(plain_mm)},
                        "note": "a filter helps only if the copies it removes average clearly below 0 "
                                "(baseline Nov 11-15: maker copies of heavy makers -1.2c +/-1.0, olek +1.6c)"}
    res.update(forward_copies=n, boss_gap_c=None if boss_gap is None else round(boss_gap, 2),
               rule_gap_c=None if rule_gap is None else round(rule_gap, 2),
               verdict="PASS: earns skip power (needs a reviewed PR)" if passed
               else ("too few copies yet" if n < MIN_COPIES else "FAIL: no better than plain code"))
    return res


def main(argv: list[str]) -> int:
    d, status = Path(argv[1]), json.loads(Path(argv[2]).read_text())
    calls = ((status.get("boss") or {}).get("calls") or [])
    g = grade(load_rows(d), {})
    trades = {k: v.get("trades") or [] for k, v in g.items() if k != "ALL"}
    print(json.dumps(score(forward(calls, trades)), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
