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


def _short(title: str, n: int = 48) -> str:
    t = (title or "").strip()
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def day_story(rows: list[dict], midnight: datetime) -> list[str]:
    """Plain lines on what the main bot DID today (Nov 16, Alex: "make it not as boring"):
    orders placed, fills, settled record, best and worst result, biggest open bet, month pace."""
    live = [r for r in rows if r.get("mode") == "live"]
    placed = [r for r in live if (parse_iso(r.get("placed_at")) or midnight) >= midnight
              and r.get("placed_at")]
    filled_today = [r for r in live if r.get("filled_at") and (parse_iso(r.get("filled_at")) or midnight) >= midnight]
    today = _settled(live, "live", midnight)
    strats = sorted({r.get("strategy") for r in placed if r.get("strategy")})
    ct = sum(int(float(r.get("count") or 0)) for r in placed)
    fct = sum(int(float(r.get("filled") or 0)) for r in filled_today)
    pnl = sum(float(r["pnl"]) for r in today)
    out = [f"Main bot today: placed {len(placed)} orders ({ct} contracts)"
           + (f" in {len(strats)} strategies" if strats else "")
           + f", {len(filled_today)} filled ({fct} contracts), "
           + (f"settled {_record(today)} for {_money(pnl)}." if today else "nothing settled yet.")]
    if today:
        best = max(today, key=lambda r: float(r["pnl"]))
        worst = min(today, key=lambda r: float(r["pnl"]))
        out.append(f"Best: {_money(float(best['pnl']))} {best.get('strategy')}, {_short(best.get('title'))}")
        if worst is not best and float(worst["pnl"]) < 0:
            out.append(f"Worst: {_money(float(worst['pnl']))} {worst.get('strategy')}, {_short(worst.get('title'))}")
    held = [r for r in live if r.get("status") == "filled"]
    if held:
        big = max(held, key=lambda r: float(r.get("filled") or 0) * float(r.get("price") or 0))
        out.append(f"Biggest bet riding: {int(float(big.get('filled') or 0))} x {str(big.get('side', '')).upper()} "
                   f"@ {round(float(big.get('price') or 0) * 100)}c, {_short(big.get('title'))} ({big.get('strategy')})")
    first = midnight.replace(day=1)
    month = _settled(live, "live", first)
    if month:
        import calendar
        mp = sum(float(r["pnl"]) for r in month)
        days = calendar.monthrange(midnight.year, midnight.month)[1]
        pace = mp / max(1, midnight.day) * days
        out.append(f"This month: {_money(mp)} real ({len(month)} results), on pace for {_money(pace)}/month")
    return out


def format_summary(rows: list[dict], midnight: datetime, alert_line: str = "",
                   problems: list[str] = ()) -> tuple[str, str]:
    lines = []
    try:
        lines += day_story(rows, midnight)
    except Exception:                               # the story is a nice-to-have; never block the push
        pass
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
    td = sum(float(r["pnl"]) for r in today)
    return f"Kalshi bot: daily money summary · {_money(td)} today", "\n".join(lines)
