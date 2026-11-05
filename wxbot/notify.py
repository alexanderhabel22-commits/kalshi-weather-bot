"""Phone notifications via ntfy (free app, no account needed)."""
from __future__ import annotations

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from .util import Config, Http, HttpError

log = logging.getLogger("wxbot")


def send(http: Http | None, cfg: Config, title: str, message: str, *, priority: int = 3,
         tags: list[str] | None = None, click: str | None = None, dry_run: bool = False) -> bool:
    topic = os.environ.get("NTFY_TOPIC") or cfg.get("alerts", "ntfy_topic", "")
    server = (os.environ.get("NTFY_SERVER") or cfg.get("alerts", "ntfy_server", "")
              or "https://ntfy.sh").rstrip("/")
    print(f"\n=== {title} ===\n{message}\n")
    if dry_run or not topic or http is None:
        if not topic and not dry_run:
            log.warning("NTFY_TOPIC not set: alert printed only")
        return False
    payload = {"topic": topic, "title": title, "message": message,
               "priority": priority, "tags": tags or []}
    if click:
        payload["click"] = click
        payload["actions"] = [{"action": "view", "label": "Open Kalshi", "url": click}]
    headers = {}
    token = os.environ.get("NTFY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        http.post_json(server, payload, headers=headers or None)
        return True
    except HttpError as exc:
        log.error("ntfy send failed: %s", exc)
        return False


def _cents(p: float) -> str:
    return f"{p * 100:.0f}¢"


def fmt_day(d) -> str:          # portable (no %-d, which Windows lacks)
    return f"{d:%b} {d.day}"


def fmt_time(dt: datetime) -> str:
    return f"{(dt.hour % 12) or 12}:{dt:%M} {dt:%p}"


def _when(day: str, tz: str, now: datetime) -> str:
    d = datetime.fromisoformat(day).date()
    today = now.astimezone(ZoneInfo(tz)).date()
    if d == today:
        return "today"
    if (d - today).days == 1:
        return "tomorrow"
    return f"{d:%a} {fmt_day(d)}"


def format_trade(opp, dm, st_name: str, st_tz: str, bankroll: float, record_line: str,
                 now: datetime) -> tuple[str, str]:
    what = "high" if opp.metric == "high" else "low"
    when = _when(opp.date, st_tz, now)
    tag = {"lock": "LOCK", "near": "NEAR-LOCK"}.get(opp.kind, "EDGE")
    title = f"{tag}: Buy {opp.side.upper()} · {opp.city} {what} {when} · {opp.label}"

    lines = [
        f"Market: {opp.city} {what} temp {when} ({fmt_day(datetime.fromisoformat(opp.date))})",
        f"Bracket: {opp.label}  →  BUY {opp.side.upper()}",
        f"Limit price: {_cents(opp.price)} or less (use a limit order)",
        f"Size: {opp.contracts} contract{'s' if opp.contracts != 1 else ''} ≈ ${opp.cost:.2f}"
        f" + ${opp.fee:.2f} fee  ({(opp.cost / bankroll * 100) if bankroll else 0:.0f}% of ${bankroll:.0f})",
    ]
    mk = "" if opp.market_p != opp.market_p else f" vs market {opp.market_p * 100:.0f}%"
    if opp.kind == "lock":
        lines.append(f"Odds: already decided by today's readings (counted as {opp.fair_p * 100:.1f}%)"
                     f"{mk} → edge +{opp.edge * 100:.1f}¢/contract")
    else:
        lines.append(f"Model: {opp.model_p * 100:.0f}% to win{mk} → edge +{opp.edge * 100:.1f}¢/contract"
                     f" (after fees, blended with market)")
    lines.append(f"Expected profit ≈ ${opp.exp_profit:.2f}")
    lines.append("Why: " + explain(opp, dm, st_name, st_tz))
    if opp.depth is not None and opp.depth < opp.contracts * 2:
        lines.append(f"Thin book: ~{int(opp.depth)} contracts at this price.")
    for n in dm.notes:
        lines.append(f"Note: {n}")
    lines.append(record_line)
    return title, "\n".join(lines)


def explain(opp, dm, st_name: str, st_tz: str) -> str:
    zone = ZoneInfo(st_tz)
    word = "high" if opp.metric == "high" else "low"
    parts = []
    if dm.obs_extreme is not None and dm.obs_extreme_time is not None:
        verb = "already hit" if opp.metric == "high" else "already dropped to"
        parts.append(f"{st_name} {verb} {dm.obs_extreme:.1f}°F "
                     f"({fmt_time(dm.obs_extreme_time.astimezone(zone))})")
    if dm.current is not None and dm.last_obs_time is not None:
        parts.append(f"now {dm.current:.1f}°F")
    if dm.remaining_median is not None and dm.obs_extreme is not None and dm.remaining_hours > 0:
        parts.append(f"rest of day forecast {word} ≈{dm.remaining_median:.0f}°F")
    elif dm.remaining_median is not None:
        rng = f"{dm.quantile(0.1)}–{dm.quantile(0.9)}°F"
        parts.append(f"forecast {word} {dm.median}°F (80% range {rng})")
        if dm.nws is not None:
            parts.append(f"NWS says {dm.nws:.0f}°F")
    if opp.kind == "lock":
        if opp.metric == "high":
            parts.append(f"settlement can't come in below {dm.guaranteed}°F")
        else:
            parts.append(f"settlement can't come in above {dm.guaranteed}°F")
    return "; ".join(parts) + "."


def format_summary(stats_all: dict, stats_today: dict, alerts_today: int, scans_today: int,
                   open_rows: int, problems: list[str], shadow: dict | None = None) -> tuple[str, str]:
    title = "Weather bot daily summary"
    lines = [f"Alerts sent today: {alerts_today} · scans: {scans_today}"]
    if stats_today["n"]:
        lines.append(f"Settled today: {stats_today['wins']}-{stats_today['losses']}, "
                     f"{'+' if stats_today['pnl'] >= 0 else '-'}${abs(stats_today['pnl']):.2f}")
    if stats_all["n"]:
        lines.append(f"All-time paper record: {stats_all['wins']}-{stats_all['losses']}, "
                     f"{'+' if stats_all['pnl'] >= 0 else '-'}${abs(stats_all['pnl']):.2f} "
                     f"({stats_all['roi'] * 100:+.1f}% ROI)")
        if stats_all["brier_model"] is not None and stats_all["brier_market"] is not None:
            better = stats_all["brier_model"] < stats_all["brier_market"]
            lines.append(f"Accuracy (Brier, lower is better): model {stats_all['brier_model']:.3f} "
                         f"vs market {stats_all['brier_market']:.3f}"
                         f" → {'model is beating the market' if better else 'market is beating the model'}")
    else:
        lines.append("No settled alerts yet.")
    lines.append(f"Open alerts: {open_rows} (${stats_all['open_cost']:.2f} if you took them all)")
    if shadow and (shadow["n"] or shadow["open"]):
        if shadow["n"]:
            line = (f"Silent EDGE test: {shadow['wins']}-{shadow['losses']}, "
                    f"{'+' if shadow['pnl'] >= 0 else '-'}${abs(shadow['pnl']):.2f}")
            if shadow["brier_model"] is not None and shadow["brier_market"] is not None:
                line += (" · " + ("model beating market" if shadow["brier_model"] < shadow["brier_market"]
                                  else "market beating model"))
        else:
            line = "Silent EDGE test: nothing settled yet"
        lines.append(line + f" ({shadow['open']} pending)")
    for p in problems[:5]:
        lines.append(f"Problem: {p}")
    return title, "\n".join(lines)
