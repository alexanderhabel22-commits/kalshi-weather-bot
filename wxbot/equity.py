"""Value history for the dashboard charts (read-only bookkeeping).

After each trade run, append one row per mode (paper / live) to
data/equity.csv: realized P&L so far, the open positions marked at the
market mid ("up/down before selling"), money at risk, and how many positions
are held, plus the same per strategy in a compact column. The dashboard draws
its Robinhood-style charts from this file.

Rows older than 7 days are thinned to one per hour so the file stays small.
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timedelta

from .util import DATA_DIR, parse_iso

log = logging.getLogger("wxbot")

EQUITY = DATA_DIR / "equity.csv"
FIELDS = ["at", "mode", "realized", "unrealized", "exposure", "held", "by_strategy"]
KEEP_FULL_DAYS = 7
MARKS = DATA_DIR / "marks.json"
MARKS_KEEP_H = 3


def _f(x, d=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _rows(path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def side_mid(q: dict, side: str) -> float | None:
    """Mid price of our side (bid alone if there is no ask)."""
    yb, ya = q.get("yes_bid"), q.get("yes_ask")
    if side == "yes":
        bid, ask = yb, ya
    else:
        bid = round(1 - ya, 4) if ya is not None else None
        ask = round(1 - yb, 4) if yb is not None else None
    if bid is not None and ask is not None and ask > 0:
        return (bid + ask) / 2
    return bid


def marks(quotes: dict) -> dict[str, dict[str, dict]]:
    """{mode: {strategy: {realized, unrealized, exposure, held}}} from the ledgers."""
    out: dict[str, dict[str, dict]] = {}

    def acc(mode, strat):
        return out.setdefault(mode, {}).setdefault(
            strat, {"realized": 0.0, "unrealized": 0.0, "exposure": 0.0, "held": 0})

    trader_wx = set()
    for r in _rows(DATA_DIR / "trade_orders.csv"):
        if r.get("strategy") == "weather":
            trader_wx.add((r.get("ticker"), (r.get("side") or "").lower()))
        a = acc(r.get("mode") or "paper", r.get("strategy") or "?")
        st, price = r.get("status"), _f(r.get("price"))
        filled, count = int(_f(r.get("filled"))), int(_f(r.get("count")))
        if st == "settled" and r.get("pnl") not in (None, ""):
            a["realized"] += _f(r["pnl"])
        if st in ("resting", "filled"):
            a["exposure"] += (count if st == "resting" else filled) * price
            held = filled if filled else (count if st == "filled" else 0)
            if held:
                a["held"] += 1
                q = quotes.get(r.get("ticker") or "")
                m = side_mid(q, (r.get("side") or "").lower()) if q else None
                if m is not None:
                    a["unrealized"] += (m - price) * held
    # weather day-before research orders (paper): realized only once graded
    for r in _rows(DATA_DIR / "paper_orders.csv"):
        if (r.get("ticker"), (r.get("side") or "").lower()) in trader_wx:
            continue  # same order already counted from the trader ledger
        if r.get("status") == "filled" and r.get("pnl") not in (None, ""):
            acc("paper", "weather")["realized"] += _f(r["pnl"])
    return out


def _thin(rows: list[dict], now: datetime) -> list[dict]:
    cut = now - timedelta(days=KEEP_FULL_DAYS)
    kept, seen = [], set()
    for r in rows:
        t = parse_iso(r.get("at"))
        if t is None:
            continue
        if t >= cut:
            kept.append(r)
            continue
        key = (r.get("mode"), t.strftime("%Y-%m-%dT%H"))
        if key not in seen:
            seen.add(key)
            kept.append(r)
    return kept


def record(now: datetime, quotes: dict) -> list[dict]:
    m = marks(quotes)
    new = []
    for mode, strats in sorted(m.items()):
        tot = {k: sum(s[k] for s in strats.values()) for k in ("realized", "unrealized", "exposure", "held")}
        by = ";".join(f"{name}:{s['realized']:.2f}:{s['unrealized']:.2f}:{s['exposure']:.2f}:{s['held']}"
                      for name, s in sorted(strats.items()))
        new.append({"at": now.isoformat(timespec="seconds"), "mode": mode,
                    "realized": f"{tot['realized']:.2f}", "unrealized": f"{tot['unrealized']:.2f}",
                    "exposure": f"{tot['exposure']:.2f}", "held": int(tot["held"]), "by_strategy": by})
    rows = _thin(_rows(EQUITY) + new, now)
    EQUITY.parent.mkdir(parents=True, exist_ok=True)
    tmp = EQUITY.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(EQUITY)
    try:
        record_marks(now, quotes)
    except Exception as exc:  # per-bet history is a dashboard extra
        log.warning("marks failed: %s", exc)
    log.info("equity: %s", ", ".join(f"{r['mode']} up/down {r['unrealized']}" for r in new))
    return new


def record_marks(now: datetime, quotes: dict) -> dict:
    """Rolling 3 h of each held bet's mid (our side) for the dashboard's "biggest movers".

    data/marks.json = {"at": iso, "marks": {"TICKER|side": [[unix_s, mid], ...]}}
    """
    old = {}
    if MARKS.exists():
        try:
            old = json.loads(MARKS.read_text(encoding="utf-8")).get("marks", {})
        except (ValueError, OSError):
            old = {}
    cut, ts = now.timestamp() - MARKS_KEEP_H * 3600, int(now.timestamp())
    held = set()
    for r in _rows(DATA_DIR / "trade_orders.csv"):
        st = r.get("status")
        if st == "filled" or (st == "resting" and _f(r.get("filled")) > 0):
            held.add((r.get("ticker") or "", (r.get("side") or "").lower()))
    out = {}
    for tk, side in sorted(held):
        key = f"{tk}|{side}"
        pts = [p for p in old.get(key, []) if p[0] >= cut]
        q = quotes.get(tk)
        m = side_mid(q, side) if q else None
        if m is not None:
            pts.append([ts, round(m, 4)])
        if pts:
            out[key] = pts
    MARKS.parent.mkdir(parents=True, exist_ok=True)
    tmp = MARKS.with_suffix(".tmp")
    tmp.write_text(json.dumps({"at": now.isoformat(timespec="seconds"), "marks": out}, separators=(",", ":")), encoding="utf-8")
    tmp.replace(MARKS)
    return out
