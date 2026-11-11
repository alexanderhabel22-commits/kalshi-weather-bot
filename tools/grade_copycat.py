"""Grade Copycat shadow mode (wxbot/copycat.py). Read-only.

Usage: python tools/grade_copycat.py DIR      (DIR holds copycat-*.jsonl or .jsonl.gz files,
                                               e.g. a checkout of branch copycat-data/copycat)

For every live (non-backfill) fill it measures, in cents per contract, on the sharp's side:
  move_N   = mid price N seconds after we saw the fill, minus the sharp's own fill price
  follow_N = mid at N s minus the ask we would have paid when we saw it (copying cost included)
  settle   = settlement value minus that ask (needs the market result; fetched from Kalshi's
             public market API when the snapshots don't already have it)
A copy strategy is worth a paper test only if the pessimistic (lower 90%) follow-to-settle
estimate is >= 0 over a few hundred fills, per account.
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wxbot.copycat import grade, load_rows  # noqa: E402

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"


def load(d: Path) -> list[dict]:
    return load_rows(d)


def summary(vals: list[float]) -> str:
    if not vals:
        return "-"
    m = st.mean(vals)
    se = st.pstdev(vals) / len(vals) ** 0.5 if len(vals) > 1 else 0
    return f"{m:+.1f}c (low {m - 1.645 * se:+.1f}, n={len(vals)})"


def fetch_results(tickers) -> dict[str, str]:
    res = {}
    s = requests.Session()
    for t in tickers:
        try:
            m = s.get(f"{KALSHI}/markets/{t}", timeout=15).json().get("market") or {}
            if m.get("result") in ("yes", "no"):
                res[t] = m["result"]
        except Exception:  # noqa: BLE001
            pass
    return res


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    rows = load(Path(argv[0] if argv else "."))
    tickers = {r["ticker"] for r in rows if r.get("kind") == "fill" and not r.get("backfill")}
    results = fetch_results(sorted(tickers)) if "--no-fetch" not in argv else {}
    out = grade(rows, results)
    cols = ["move_60", "move_300", "follow_60", "follow_300", "follow_1800", "settle"]
    print("account | median lag s | " + " | ".join(cols))
    for nick in sorted(out, key=lambda n: (n != "ALL", -len(out[n]["lag"]))):
        o = out[nick]
        print(f"{nick} | {st.median(o['lag']):.0f} | " + " | ".join(summary(o[c]) for c in cols))
    return 0


if __name__ == "__main__":
    sys.exit(main())
