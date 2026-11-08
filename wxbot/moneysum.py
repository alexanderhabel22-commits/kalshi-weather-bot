"""The 9 PM daily push: real-money results first (balance, P&L today and all
time, settled and sold-early trades, open risk), practice in one line, the old
weather-alert record only as a footnote."""
from __future__ import annotations

import csv
from datetime import datetime

from . import trader
from .equity import EQUITY
from .util import BANKROLL_FILE, parse_iso, read_json


def _money(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):.2f}"


def _last_unrealized(mode: str) -> float | None:
    try:
        with open(EQUITY, newline="") as fh:
            rows = [r for r in csv.DictReader(fh) if r.get("mode") == mode]
        return float(rows[-1]["unrealized"]) if rows else None
    except (OSError, ValueError, KeyError):
        return None


def _settled(rows, mode, since=None):
    out = []
    for r in rows:
        if r.get("mode") != mode or r.get("status") != "settled" or r.get("pnl") in (None, ""):
            continue
        if since and (parse_iso(r.get("settled_at")) or since) < since:
            continue
        out.append(r)
    return out


def _record(rs) -> str:
    w = sum(float(r["pnl"]) > 0 for r in rs)
    sold = sum(r.get("result") == "exit" for r in rs)
    s = f"{w}-{len(rs) - w}"
    return s + (f", {sold} sold early" if sold else "")


def format_summary(rows: list[dict], midnight: datetime, alert_line: str = "",
                   problems: list[str] = ()) -> tuple[str, str]:
    lines = []
    bank = read_json(BANKROLL_FILE) or {}
    if bank.get("total") is not None:
        lines.append(f"Balance: ${float(bank['total']):.2f} (cash ${float(bank.get('cash') or 0):.2f}"
                     f" + positions ${float(bank.get('positions') or 0):.2f})")
    live = [r for r in rows if r.get("mode") == "live"]
    today = _settled(live, "live", midnight)
    allt = _settled(live, "live")
    tot = sum(float(r["pnl"]) for r in allt)      # rows = trader.load_all(): archives included
    lines.append(f"Real P&L today: {_money(sum(float(r['pnl']) for r in today))}"
                 + (f" ({_record(today)})" if today else " (nothing settled)"))
    lines.append(f"Real P&L all time: {_money(tot)}"
                 + (f" ({_record(allt)})" if allt else ""))
    open_ = [r for r in live if r.get("status") in trader.OPEN]
    filled = [r for r in open_ if r["status"] == "filled"]
    resting = [r for r in open_ if r["status"] == "resting"]
    risk = trader.exposure(live)
    unr = _last_unrealized("live")
    lines.append(f"Open: {len(filled)} positions + {len(resting)} waiting orders, "
                 f"${risk:.2f} at risk" + (f", marked {_money(unr)}" if unr is not None else ""))
    names = sorted({r.get("strategy") for r in live if r.get("strategy")})
    for k in names:
        td = sum(float(r["pnl"]) for r in today if r.get("strategy") == k)
        at = [r for r in allt if r.get("strategy") == k]
        risk_k = trader.exposure(live, strategy=k)
        lines.append(f"  {k}: today {_money(td)} · all time {_money(sum(float(r['pnl']) for r in at))}"
                     f" ({_record(at) if at else 'none settled'}) · ${risk_k:.2f} open")
    paper = _settled(rows, "paper")
    if paper:
        lines.append(f"Practice (no money) all time: {_money(sum(float(r['pnl']) for r in paper))}"
                     f" ({_record(paper)})")
    if alert_line:
        lines.append(alert_line)
    for p in list(problems)[:5]:
        lines.append(f"Problem: {p}")
    return "Kalshi bot: daily money summary", "\n".join(lines)
