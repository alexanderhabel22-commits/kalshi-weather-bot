"""Writes data/REPORT.md: the bot grading itself in plain English."""
from __future__ import annotations

import csv
import os
import statistics
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from . import daybefore, kalshi, latepaper, launches, ledger, rain
from .util import DATA_DIR

REPORT = DATA_DIR / "REPORT.md"
MY_TRADES = DATA_DIR / "my_trades.csv"
MY_FIELDS = ["ticker", "side", "contracts", "price", "note"]


def report_url() -> str | None:
    repo = os.environ.get("GITHUB_REPOSITORY")
    return f"https://github.com/{repo}/blob/main/data/REPORT.md" if repo else None


def _money(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):.2f}"


def _pct(n: int, d: int) -> str:
    return f"{n / d * 100:.0f}%" if d else "–"


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def load_my_trades() -> list[dict]:
    if not MY_TRADES.exists():
        return []
    with open(MY_TRADES, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if (r.get("ticker") or "").strip()]


def ensure_my_trades_file() -> None:
    if not MY_TRADES.exists():
        MY_TRADES.parent.mkdir(parents=True, exist_ok=True)
        MY_TRADES.write_text(",".join(MY_FIELDS) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- sections

def _live_section(rows: list[dict], tz: ZoneInfo) -> list[str]:
    live = [r for r in rows if not ledger.is_shadow(r)]
    s = ledger.stats(rows)
    out = ["## Alerts sent to your phone", ""]
    if not live:
        return out + ["None yet.", ""]
    out.append(f"**Record: {s['wins']}-{s['losses']}**, {_money(s['pnl'])} at suggested sizes "
               f"({s['roi'] * 100:+.1f}% on ${s['staked']:.2f} staked). "
               f"Open: {s['open']} (${s['open_cost']:.2f}).")
    by_kind = []
    for kind in ("lock", "near", "model"):
        k = ledger.stats(rows, kind=kind)
        if k["n"]:
            by_kind.append(f"{kind.upper()} {k['wins']}-{k['losses']} ({_money(k['pnl'])})")
    if by_kind:
        out.append("By type: " + ", ".join(by_kind) + ".")
    out += ["", "| When | City | Bracket | Side | Price | Lasted | Result | P&L |",
            "|---|---|---|---|---|---|---|---|"]
    for r in sorted(live, key=lambda r: r["alerted_at"], reverse=True)[:20]:
        t = datetime.fromisoformat(r["alerted_at"]).astimezone(tz)
        lasted = f"{r['avail_min']}m+" if r.get("avail_min") else "–"
        res = r["status"] if r["status"] != "open" else "pending"
        pnl = _money(_f(r.get("pnl"), 0)) if r.get("pnl") else ""
        out.append(f"| {t:%m/%d %H:%M} | {r['city']} {r['metric']} {r['date'][5:]} | {r['bracket']} | "
                   f"{r['side'].upper()} {r['kind']} | {_f(r['price']) * 100:.0f}¢ | {lasted} | {res} | {pnl} |")
    losses = [r for r in live if r["status"] == "lost"]
    if losses:
        out += ["", "**Every loss, explained:**"]
        for r in losses:
            out.append(f"- {r['city']} {r['metric']} {r['date']}: bought {r['side'].upper()} on "
                       f"{r['bracket']} at {_f(r['price']) * 100:.0f}¢ ({r['kind']}). Bot saw observed "
                       f"extreme {r.get('obs_extreme') or '–'}°F, forecast median {r.get('pred_median') or '–'}°F; "
                       f"official settlement {r.get('cli_value') or '?'}°F.")
    return out + [""]


def _speed_section(rows: list[dict]) -> list[str]:
    live = [r for r in rows if not ledger.is_shadow(r)]
    t15 = [r for r in live if r.get("ask_15m")]
    t60 = [r for r in live if r.get("ask_60m")]
    out = ["## Did the prices last long enough to buy?", ""]
    if not t15:
        return out + ["Not enough follow-up data yet (needs alerts that are 15+ minutes old).", ""]
    still15 = sum(_f(r["ask_15m"], 1) <= _f(r["price"]) + 1e-9 for r in t15)
    still60 = sum(_f(r["ask_60m"], 1) <= _f(r["price"]) + 1e-9 for r in t60)
    lasted = [_f(r.get("avail_min"), 0) for r in live if r.get("ask_15m")]
    out.append(f"- Still buyable at the alert price **15 min later: {_pct(still15, len(t15))}** "
               f"({still15}/{len(t15)}); 60 min later: {_pct(still60, len(t60))} ({still60}/{len(t60)}).")
    out.append(f"- Typical time the price lasted: {statistics.median(lasted):.0f}+ minutes.")
    share = still15 / len(t15)
    if len(t15) >= 8:
        if share < 0.4:
            out.append("- Verdict: prices vanish fast. Speed matters: a faster server (and maybe "
                       "auto-ordering) would capture more of these.")
        elif share > 0.7:
            out.append("- Verdict: prices usually stick around. A faster server would not add much.")
        else:
            out.append("- Verdict: mixed. Some locks get priced in quickly; worth watching.")
    return out + [""]


def _census_section(census: dict, min_edge: float, max_price: float, lock_p: float = 0.995) -> list[str]:
    out = ["## Lock census: every bracket the readings had already decided", ""]
    days = sorted(census)[-7:]
    if not days:
        return out + ["No data yet.", ""]
    out += ["| Day | Locks seen | Cheap enough to alert | Priced ≥98¢ when first seen |",
            "|---|---|---|---|"]
    total = cheap_total = 0
    for d in days:
        items = census[d].values()
        n = len(items)
        cheap = sum(1 for it in items
                    if it["min_ask"] <= max_price
                    and lock_p - it["min_ask"] - kalshi.fee_per_contract(it["min_ask"]) >= min_edge)
        rich = sum(1 for it in items if it["first_ask"] >= 0.98)
        total += n
        cheap_total += cheap
        out.append(f"| {d} | {n} | {cheap} | {rich} |")
    out.append("")
    out.append(f"Over these days, {_pct(cheap_total, total)} of decided brackets were ever cheap enough "
               f"to be worth buying. The rest were already priced in by other traders.")
    return out + [""]


def _my_trades_section(rows: list[dict], mine: list[dict]) -> list[str]:
    out = ["## Your actual trades (from `data/my_trades.csv`)", ""]
    if not mine:
        return out + ["None logged. Add a line per trade you place: `ticker,side,contracts,price,note` "
                      "(price in dollars, e.g. `0.94`). Or tell Claude and it will add them.", ""]
    by_key = {(r["ticker"], r["side"]): r for r in rows}
    total = staked = 0.0
    wins = losses = pending = 0
    for t in mine:
        side = (t.get("side") or "").strip().lower()
        n = int(_f(t.get("contracts"), 0))
        p = _f(t.get("price"), 0.0)
        r = by_key.get((t["ticker"].strip(), side))
        status = r["status"] if r else "unknown"
        if status in ("won", "lost"):
            fee = kalshi.taker_fee(n, p)
            pnl = (n if status == "won" else 0) - n * p - fee
            total += pnl
            staked += n * p + fee
            wins += status == "won"
            losses += status == "lost"
        else:
            pending += 1
    out.append(f"**{wins}-{losses}**, {_money(total)} on ${staked:.2f} "
               f"({(total / staked * 100) if staked else 0:+.1f}%). Pending/unmatched: {pending}.")
    return out + [""]


SHADOW_KINDS = {
    "model": ("Silent EDGE test (forecast calls logged but not sent)", "EDGE"),
    "near": ("Silent near-lock test (late-day calls the model rates 95%+, not yet certain)",
             "near-lock"),
}


def _shadow_section(rows: list[dict], kind: str = "model") -> list[str]:
    title, name = SHADOW_KINDS[kind]
    s = ledger.stats(rows, kind=kind, mode="shadow")
    out = [f"## {title}", ""]
    if not s["n"]:
        pending = sum(1 for r in rows if ledger.is_shadow(r) and r.get("kind") == kind
                      and r.get("status") == "open")
        return out + [f"Nothing settled yet ({pending} pending).", ""]
    pending = sum(1 for r in rows if ledger.is_shadow(r) and r.get("kind") == kind
                  and r.get("status") == "open")
    out.append(f"**Record: {s['wins']}-{s['losses']}**, {_money(s['pnl'])} if you had taken them "
               f"({s['roi'] * 100:+.1f}%). Pending: {pending}.")
    better = False
    if s["brier_model"] is not None and s["brier_market"] is not None:
        better = s["brier_model"] < s["brier_market"]
        out.append(f"Accuracy (Brier, lower is better): model {s['brier_model']:.3f} vs market "
                   f"{s['brier_market']:.3f} → **{'model ahead' if better else 'market ahead'}**.")
    done = [r for r in rows if ledger.is_shadow(r) and r.get("kind") == kind
            and r["status"] in ("won", "lost")]
    bins = defaultdict(list)
    for r in done:
        p = _f(r["model_p"], 0)
        bins[min(int(p * 10), 9)].append(r)
    out += ["", "| Model said | Calls | Market said (avg) | Actually won |", "|---|---|---|---|"]
    for b in sorted(bins):
        g = bins[b]
        mk = [_f(r["market_p"]) for r in g if r.get("market_p")]
        out.append(f"| {b * 10}-{b * 10 + 9}% | {len(g)} | "
                   f"{(sum(mk) / len(mk) * 100) if mk else 0:.0f}% | "
                   f"{_pct(sum(r['status'] == 'won' for r in g), len(g))} |")
    ok = s["n"] >= 50 and s["pnl"] > 0 and better
    out += ["", "Verdict: " + (f"worth considering turning {name} alerts on (small sizes)." if ok else
                               f"keep {name} alerts silent. Needs 50+ settled calls, a profit, and the "
                               "model beating the market.")]
    return out + [""]


def _forecast_section(frows: list[dict]) -> list[str]:
    out = ["## Forecast accuracy (every city-day the bot priced)", ""]
    done = [r for r in frows if r.get("actual")]
    if not done:
        return out + ["Nothing graded yet (fills in the morning after each day settles).", ""]
    buckets = [("More than 24h out", 24, 99), ("12–24h out", 12, 24), ("6–12h out", 6, 12),
               ("Last 6h", 0, 6)]
    out += ["| Lead time | City-days | Model bias | Model avg miss | 80% range held | NWS avg miss |",
            "|---|---|---|---|---|---|"]
    for label, lo, hi in buckets:
        g = [r for r in done if lo <= _f(r["hours_to_end"], 0) < hi]
        if not g:
            continue
        err = [_f(r["actual"]) - _f(r["median"]) for r in g]
        cover = sum(_f(r["p10"]) <= _f(r["actual"]) <= _f(r["p90"]) for r in g)
        nws = [abs(_f(r["actual"]) - _f(r["nws"])) for r in g if r.get("nws")]
        out.append(f"| {label} | {len(g)} | {statistics.fmean(err):+.1f}°F | "
                   f"{statistics.fmean(abs(e) for e in err):.1f}°F | {_pct(cover, len(g))} (target 80%) | "
                   f"{(statistics.fmean(nws) if nws else float('nan')):.1f}°F |")
    out += ["", "Bias is actual minus model median (positive = model too cold). If the 80% range "
            "holds far more than 80% of the time, the model is too unsure; far less, too sure."]
    city = defaultdict(list)
    for r in done:
        if _f(r["hours_to_end"], 0) >= 12:
            city[(r["city"], r["metric"])].append(_f(r["actual"]) - _f(r["median"]))
    worst = sorted(((k, statistics.fmean(v), len(v)) for k, v in city.items() if len(v) >= 5),
                   key=lambda x: -abs(x[1]))[:5]
    if worst:
        out += ["", "Biggest city biases (12h+ out): " + "; ".join(
            f"{c} {m} {b:+.1f}°F (n={n})" for (c, m), b, n in worst) + "."]
    return out + [""]


def build_report(rows: list[dict], census: dict, frows: list[dict], cfg, now: datetime,
                 runtime: dict | None = None) -> str:
    tz = ZoneInfo(cfg.tz)
    sp = cfg.strategy_params
    lines = ["# Weather bot report", "",
             f"Updated {now.astimezone(tz):%a %b %d, %I:%M %p} · bankroll ${cfg.bankroll:.2f} · "
             f"risk level {cfg.risk_level} · EDGE alerts {cfg.get('alerts', 'edge_alerts', 'shadow')}", ""]
    lines += _live_section(rows, tz)
    lines += _speed_section(rows)
    lines += _census_section(census, float(sp.get("min_edge_lock", 0.03)),
                             float(sp.get("max_price_lock", 0.97)))
    lines += _my_trades_section(rows, load_my_trades())
    if str(cfg.get("alerts", "edge_alerts", "shadow")).lower() == "shadow":
        lines += _shadow_section(rows, "model")   # silent EDGE test (off since Nov 5)
    lines += _shadow_section(rows, "near")
    lines += _forecast_section(frows)
    lines += rain.report_section(rain.load())
    lines += daybefore.report_section(daybefore.load_orders())
    lines += latepaper.report_section(latepaper.load_orders())
    from . import trader
    lines += trader.report_section(trader.load_all(), cfg)
    lines += launches.report_section(launches.load())
    if runtime:
        scans = runtime.get("scans", {})
        probs = runtime.get("problems", {})
        lines += ["## Bot health", "",
                  "Scans per day: " + (", ".join(f"{d[5:]}: {n}" for d, n in sorted(scans.items())) or "–"),
                  ""]
        for d, ps in sorted(probs.items()):
            lines.append(f"- {d}: " + "; ".join(ps))
        lines.append("")
    return "\n".join(lines)


def write_report(*args, **kw) -> str:
    text = build_report(*args, **kw)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(text, encoding="utf-8")
    return text
