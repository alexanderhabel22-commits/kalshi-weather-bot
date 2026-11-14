"""Daily, weekly and monthly reports on every strategy (paper and live).

Written to reports/daily/YYYY-MM-DD.md, reports/weekly/YYYY-Www.md and
reports/monthly/YYYY-MM.md, and pushed to the phone (ntfy) with a link.
Schedule (Eastern time, first trade run after the hour):
  daily    every day at 9 AM, covering yesterday
  weekly   Mondays at noon (after the ~10 AM settlements), covering Mon-Sun of last week
  monthly  the 1st at noon, covering last month
"""
from __future__ import annotations

import math
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import aicost, daybefore, ledger, notify, trader
from .util import BANKROLL_FILE, DATA_DIR, parse_iso, read_json, write_json

REPORTS = DATA_DIR.parent / "reports"     # repo/reports (next to data/)
STATE = DATA_DIR / "digest_state.json"
SEND_HOUR = 9
HOURS = {"daily": 9, "weekly": 12, "monthly": 12}     # Alex, Nov 7: weekly Mondays at noon


@dataclass
class Result:
    strategy: str
    mode: str          # live (real money) | paper | shadow (silent alert) | alert (sent, you trade)
    day: date          # settlement day (local)
    cost: float
    pnl: float
    contracts: int
    label: str
    model_p: float | None = None


# What each strategy is, why we think it works, and what would change our mind.
THESIS = {
    "LOCK alerts": ("Readings already decide the bracket; buy the sure side if it's still cheap.",
                    "Every LOCK should win. Seen so far: locks are priced in (already 98c+) before we see them.",
                    "Any loss = stop and diagnose."),
    "EDGE (silent)": ("Forecast-based same-day/next-day calls, logged silently.",
                      "Needs 50+ settled, a profit, and a Brier score better than the market's.",
                      "Market's Brier has been better so far: stays silent."),
    "Near-lock (silent)": ("Late-day calls the model rates 95%+ before readings decide them.",
                           "Should win ~95%+ of the time at 80-97c to be worth it.",
                           "Stays silent until it beats its price."),
    "Weather day-before": ("Rest a limit order 1c inside the spread 36h before close when the "
                           "forecast+market blend says the side is worth 6c+ more.",
                           "Backtest: +10-25%/month Jan-Aug 2026, +2-4% Sep-Oct 2026 (not significant).",
                           "Go live only if paper results are positive; stop if the edge goes negative."),
    "Mentions NO": ("Rest NO limit orders at 75-90c on 'Will X say Y?' words: buyers overpay for long shots.",
                    "21-month history: about +4c per filled contract, 74% of events ahead.",
                    "Judge after 4 weeks or 100 filled contracts; stop early at the loss stop."),
}
NAMES = {"mentions": "Mentions NO", "weather": "Weather day-before", "weather_late": "Late-day weather",
         "favorites": "Big-event favorites", "favorites_wide": "Election favorites", "favorites_late": "Favorites, last 24h (paper)",
         "macro_fade": "Fed/CPI fade", "macro_wide": "Gas + jobless claims fade", "ai_fade": "Best-AI fade",
         "rain_evening": "Evening rain fade", "spx_range": "S&P range fade"}
# How each mode reads to a person: only live and alert results are money.
MODE_LABEL = {"live": "REAL money", "alert": "old alerts (money only if you traded them)",
              "paper": "practice, no money", "shadow": "silent test, no money"}


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def collect(tz: ZoneInfo, include_edge: bool = False) -> list[Result]:
    out: list[Result] = []

    def local(s):
        t = parse_iso(s)
        return t.astimezone(tz).date() if t else None

    for r in ledger.load():
        if r.get("status") not in ("won", "lost"):
            continue
        d = local(r.get("settled_at"))
        if d is None:
            continue
        name = {"lock": "LOCK alerts", "model": "EDGE (silent)", "near": "Near-lock (silent)"}.get(
            r.get("kind"), "EDGE (silent)")
        if name == "EDGE (silent)" and not include_edge:
            continue                      # EDGE test turned off (Nov 5)
        if r.get("kind") == "lock" and ledger.is_shadow(r):
            continue
        cost = (_f(r.get("cost"), 0) or 0) + (_f(r.get("fee"), 0) or 0)
        out.append(Result(name, "shadow" if ledger.is_shadow(r) else "alert", d, cost,
                          _f(r.get("pnl"), 0) or 0, int(_f(r.get("contracts"), 0) or 0),
                          f"{r.get('city')} {r.get('metric')} {r.get('bracket')} {r.get('side', '').upper()}",
                          _f(r.get("model_p"))))
    for o in daybefore.load_orders():
        if o.get("status") != "filled" or o.get("pnl") in (None, ""):
            continue
        d = local(o.get("settled_at"))
        if d:
            out.append(Result("Weather day-before", "paper", d, _f(o["price"], 0), _f(o["pnl"], 0), 1,
                              f"{o['city']} {o['metric']} {o['bracket']} {o['side'].upper()}",
                              _f(o.get("blend_p"))))
    for r in trader.load_all():         # archived months too (weekly / monthly reports)
        if r.get("status") != "settled" or r.get("pnl") in (None, ""):
            continue
        d = local(r.get("settled_at"))
        if d is None:
            continue
        n = int(_f(r.get("filled"), 0) or 0)
        if r.get("strategy") == "weather" and r.get("mode") == "paper":
            continue                      # same orders as the day-before paper ledger
        out.append(Result(NAMES.get(r["strategy"], r["strategy"]), r["mode"], d,
                          n * (_f(r["price"], 0) or 0), _f(r["pnl"], 0) or 0, n,
                          (r.get("title") or r["ticker"])[:70] + f" {r['side'].upper()}"))
    return out


def summarize(rs: list[Result]) -> dict:
    n = sum(r.contracts for r in rs) or len(rs)
    pnl = sum(r.pnl for r in rs)
    cost = sum(r.cost for r in rs)
    wins = sum(r.pnl > 0 for r in rs)
    per = [r.pnl / max(r.contracts, 1) for r in rs]
    m = sum(per) / len(per) if per else 0.0
    sd = math.sqrt(sum((x - m) ** 2 for x in per) / (len(per) - 1)) if len(per) > 1 else 0.0
    return {"trades": len(rs), "contracts": n, "wins": wins, "pnl": pnl, "cost": cost,
            "roi": pnl / cost if cost else 0.0, "per": m, "se": sd / math.sqrt(len(per)) if per else 0.0}


def verdict(s: dict) -> str:
    if s["trades"] < 10:
        return "too few results to judge yet"
    lo, hi = s["per"] - 2 * s["se"], s["per"] + 2 * s["se"]
    if lo > 0:
        return f"clearly positive (+{lo * 100:.1f}c to +{hi * 100:.1f}c per contract)"
    if hi < 0:
        return f"clearly negative ({lo * 100:+.1f}c to {hi * 100:+.1f}c per contract): rethink"
    return f"could still be luck ({lo * 100:+.1f}c to {hi * 100:+.1f}c per contract)"


def _money(x):
    return f"{'+' if x >= 0 else '-'}${abs(x):.2f}"


def build(kind: str, start: date, end: date, results: list[Result], cfg, ai: dict | None = None) -> tuple[str, str]:
    """Markdown report for [start, end] and a 1-3 line phone summary."""
    period = [r for r in results if start <= r.day <= end]
    groups = defaultdict(list)
    for r in period:
        groups[(r.strategy, r.mode)].append(r)
    alltime = defaultdict(list)
    for r in results:
        if r.day <= end:
            alltime[(r.strategy, r.mode)].append(r)
    title = {"daily": f"Daily report: {start:%a %b %d, %Y}",
             "weekly": f"Weekly report: {start:%b %d} - {end:%b %d, %Y}",
             "monthly": f"Monthly report: {start:%B %Y}"}[kind]
    money = [r for r in period if r.mode == "live"]
    tot = summarize(money)
    bank = read_json(BANKROLL_FILE, {}) or {}
    bal = f" Balance now **${float(bank['total']):.2f}**." if bank.get("total") is not None else ""
    lines = [f"# {title}", "",
             f"Real money (the bot's live orders): **{_money(tot['pnl'])}** on "
             f"{tot['trades']} results.{bal} Practice and old-alert results are not money.", ""]
    net = aicost.period(ai, start, end) if ai else None
    if net:
        n = tot["pnl"] + net["m15"] - net["ai"]
        lines += [f"**After AI cost:** main bot {_money(tot['pnl'])} + 15-min bot {_money(net['m15'])} "
                  f"- Claude API {_money(net['ai'])[1:]} = **{_money(n)}** net.", ""]
    lines += [
             "| Strategy | Mode | Results | Won | P&L | Return | Per contract | All-time P&L |",
             "|---|---|---|---|---|---|---|---|"]
    keys = sorted(set(groups) | set(alltime))
    for k in keys:
        s, a = summarize(groups.get(k, [])), summarize(alltime.get(k, []))
        lines.append(f"| {k[0]} | {MODE_LABEL.get(k[1], k[1])} | {s['trades']} | {s['wins']} | {_money(s['pnl'])} "
                     f"| {s['roi'] * 100:+.1f}% | {s['per'] * 100:+.1f}c | {_money(a['pnl'])} ({a['trades']}) |")
    if not keys:
        lines.append("| (nothing settled yet) | | | | | | | |")
    lines += ["", "## Reasoning by strategy", ""]
    for k in keys:
        name = k[0]
        th = THESIS.get(name, ("", "", ""))
        a = summarize(alltime.get(k, []))
        lines += [f"### {name} ({MODE_LABEL.get(k[1], k[1])})", "",
                  f"- **Idea:** {th[0]}", f"- **Expected:** {th[1]}",
                  f"- **So far (all time):** {a['trades']} results, {a['wins']} won, {_money(a['pnl'])}, "
                  f"{a['per'] * 100:+.1f}c per contract: {verdict(a)}.",
                  f"- **Rule:** {th[2]}", ""]
    # trading safety status
    t = trader.settings(cfg)
    st = read_json(trader.STATE, {}) or {}
    rows = trader.load()
    lines += ["## Safety", "",
              f"Trading mode **{t['mode']}**, bankroll ${t['bankroll']:.2f}. Open now ${trader.exposure(rows):.2f} "
              f"of ${t['max_open_dollars']:.2f} allowed. Stops: daily {_money(t['daily_loss_stop'])}, "
              f"total {_money(t['total_loss_stop'])}" + (f" · **HALTED: {', '.join(st['halted'])}**" if st.get("halted") else "") + ".", ""]
    if kind in ("weekly", "monthly"):
        lines += _detail(period, start, end, kind)
    lines += ["", "_Generated automatically by the bot._", ""]
    phone = phone_text(groups, alltime, tot, rows, bank)
    if net:
        phone += (f"\nAI cost {_money(net['ai'])[1:]} · net after AI (main + 15-min) "
                  f"{_money(tot['pnl'] + net['m15'] - net['ai'])}")
    return "\n".join(lines), phone


def phone_text(groups, alltime, tot, rows, bank) -> str:
    """Real money first: total, each live strategy (period and all time), balance,
    open risk; practice in one line; old alerts dropped."""
    phone = [f"REAL money: {_money(tot['pnl'])} ({tot['wins']}-{tot['trades'] - tot['wins']})"
             + (f" · balance ${float(bank['total']):.2f}" if bank.get("total") is not None else "")]
    for k in sorted(k for k in set(groups) | set(alltime) if k[1] == "live"):
        s, a = summarize(groups.get(k, [])), summarize(alltime.get(k, []))
        phone.append(f"  {k[0]}: {_money(s['pnl'])} ({s['trades']}) · all time {_money(a['pnl'])}")
    live_open = [r for r in rows if r.get("mode") == "live"]
    phone.append(f"Open: ${trader.exposure(live_open):.2f} at risk "
                 f"({sum(r.get('status') == 'filled' for r in live_open)} positions)")
    prac = [r for k, v in groups.items() if k[1] in ("paper", "shadow") for r in v]
    if prac:
        p = summarize(prac)
        phone.append(f"Practice (no money): {_money(p['pnl'])} ({p['trades']} results)")
    return "\n".join(phone)


def _detail(period: list[Result], start: date, end: date, kind: str) -> list[str]:
    out = ["## Day by day" if kind == "weekly" else "## Week by week", "",
           "| Period | Results | Won | P&L (all modes) |", "|---|---|---|---|"]
    buckets = defaultdict(list)
    for r in period:
        key = r.day.isoformat() if kind == "weekly" else f"week of {r.day - timedelta(days=r.day.weekday())}"
        buckets[key].append(r)
    for k in sorted(buckets):
        s = summarize(buckets[k])
        out.append(f"| {k} | {s['trades']} | {s['wins']} | {_money(s['pnl'])} |")
    out.append("")
    ranked = sorted(period, key=lambda r: r.pnl)
    if ranked:
        out += ["## Biggest results", "", "| | Strategy | Mode | Trade | P&L |", "|---|---|---|---|---|"]
        for tag, r in [("worst", x) for x in ranked[:3]] + [("best", x) for x in ranked[::-1][:3]]:
            out.append(f"| {tag} | {r.strategy} | {r.mode} | {r.label} | {_money(r.pnl)} |")
        out.append("")
    cal = [r for r in period if r.model_p is not None]
    if kind == "monthly" and cal:
        out += ["## Calibration (did our probabilities come true?)", "",
                "| Our probability | Results | Won | Expected wins |", "|---|---|---|---|"]
        for lo in (0.0, 0.5, 0.7, 0.8, 0.9, 0.95):
            hi = {0.0: 0.5, 0.5: 0.7, 0.7: 0.8, 0.8: 0.9, 0.9: 0.95, 0.95: 1.01}[lo]
            b = [r for r in cal if lo <= r.model_p < hi]
            if b:
                out.append(f"| {lo:.0%}-{min(hi, 1):.0%} | {len(b)} | {sum(r.pnl > 0 for r in b)} "
                           f"| {sum(r.model_p for r in b):.1f} |")
        out.append("")
    return out


def _url(path: Path) -> str | None:
    repo = os.environ.get("GITHUB_REPOSITORY")
    rel = path.relative_to(REPORTS.parent).as_posix()
    return f"https://github.com/{repo}/blob/main/{rel}" if repo else None


def due(now: datetime, tz: ZoneInfo, state: dict) -> list[tuple[str, date, date, str]]:
    local = now.astimezone(tz)
    today = local.date()
    y = today - timedelta(days=1)
    out = []
    if local.hour >= HOURS["daily"] and state.get("daily") != y.isoformat():
        out.append(("daily", y, y, y.isoformat()))
    if today.weekday() == 0 and local.hour >= HOURS["weekly"]:
        ws = today - timedelta(days=7)
        tag = f"{ws.isocalendar()[0]}-W{ws.isocalendar()[1]:02d}"
        if state.get("weekly") != tag:
            out.append(("weekly", ws, ws + timedelta(days=6), tag))
    if today.day == 1 and local.hour >= HOURS["monthly"]:
        me = y
        ms = me.replace(day=1)
        tag = f"{ms:%Y-%m}"
        if state.get("monthly") != tag:
            out.append(("monthly", ms, me, tag))
    return out


def anything_due(cfg, now: datetime) -> bool:
    tz = ZoneInfo(cfg.tz)
    st = read_json(STATE, {}) or {}
    local = now.astimezone(tz)
    return bool(due(now, tz, st)) or (local.hour >= int(cfg.get("alerts", "summary_hour", 21))
                                      and st.get("money_summary") != local.date().isoformat())


def run_money_summary(cfg, now: datetime, http, dry_run: bool = False) -> bool:
    """The 9 PM real-money push. Sent from the trade run, right after it has
    refreshed the Kalshi balance, synced fills and settlements and re-marked open
    positions at live prices, so the numbers are seconds old."""
    from . import moneysum
    tz = ZoneInfo(cfg.tz)
    local = now.astimezone(tz)
    day = local.date().isoformat()
    hour = int(cfg.get("alerts", "summary_hour", 21))
    state = read_json(STATE, {}) or {}
    if local.hour < hour or state.get("money_summary") == day:
        return False
    old = ledger.stats(ledger.load())
    alert_line = f"Weather alerts (old, no money): record {old['wins']}-{old['losses']}" if old["n"] else ""
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    title, body = moneysum.format_summary(trader.load_all(), midnight, alert_line)
    sent = notify.send(http, cfg, title, body, priority=2, tags=["bar_chart"], dry_run=dry_run)
    if (sent or not os.environ.get("NTFY_TOPIC")) and not dry_run:
        state["money_summary"] = day
        write_json(STATE, state)
    return sent


def run_due(cfg, now: datetime, http, dry_run: bool = False) -> list[Path]:
    tz = ZoneInfo(cfg.tz)
    state = read_json(STATE, {}) or {}
    todo = due(now, tz, state)
    if not todo:
        return []
    results = collect(tz, str(cfg.get("alerts", "edge_alerts", "off")).lower() == "shadow")
    written = []
    ai = aicost.load()
    for kind, start, end, tag in todo:
        text, phone = build(kind, start, end, results, cfg, ai)
        path = REPORTS / kind / f"{tag}.md"
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        url = _url(path)
        sent = notify.send(http, cfg, text.splitlines()[0].lstrip("# "),
                           phone + ("\nTap for the full report." if url else ""),
                           priority=3 if kind == "daily" else 4, tags=["memo"], click=url,
                           dry_run=dry_run)
        if sent or not os.environ.get("NTFY_TOPIC"):
            state[kind] = tag          # don't retry forever when no phone is set up
        written.append(path)
    if not dry_run:
        write_json(STATE, state)
    return written
