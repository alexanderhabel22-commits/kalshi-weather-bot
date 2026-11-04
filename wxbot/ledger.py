"""Paper-trade ledger: every alert is logged as if you took it, then graded
against Kalshi's settlement. This is the bot's honest track record."""
from __future__ import annotations

import csv
import logging
import math
from datetime import datetime

from . import kalshi
from .util import DATA_DIR, Http, parse_iso, utcnow

log = logging.getLogger("wxbot")

LEDGER = DATA_DIR / "alerts.csv"
FIELDS = ["id", "alerted_at", "ticker", "event", "series", "city", "metric", "date",
          "bracket", "side", "kind", "price", "model_p", "fair_p", "market_p", "edge",
          "contracts", "cost", "fee", "pred_median", "obs_extreme", "status",
          "settled_at", "cli_value", "pnl"]


def load() -> list[dict]:
    if not LEDGER.exists():
        return []
    with open(LEDGER, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save(rows: list[dict]) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(LEDGER)


def record(rows: list[dict], opp, dm, now: datetime) -> dict:
    row = {
        "id": f"{opp.ticker}|{opp.side}|{now.strftime('%Y%m%dT%H%M')}",
        "alerted_at": now.isoformat(timespec="seconds"),
        "ticker": opp.ticker, "event": opp.event, "series": opp.series, "city": opp.city,
        "metric": opp.metric, "date": opp.date, "bracket": opp.label, "side": opp.side,
        "kind": opp.kind, "price": f"{opp.price:.4f}", "model_p": f"{opp.model_p:.4f}",
        "fair_p": f"{opp.fair_p:.4f}",
        "market_p": "" if math.isnan(opp.market_p) else f"{opp.market_p:.4f}",
        "edge": f"{opp.edge:.4f}", "contracts": opp.contracts, "cost": f"{opp.cost:.2f}",
        "fee": f"{opp.fee:.2f}", "pred_median": dm.median if dm else "",
        "obs_extreme": "" if not dm or dm.obs_extreme is None else f"{dm.obs_extreme:.1f}",
        "status": "open", "settled_at": "", "cli_value": "", "pnl": "",
    }
    rows.append(row)
    return row


def open_exposure(rows: list[dict], event: str | None = None) -> float:
    tot = 0.0
    for r in rows:
        if r.get("status") == "open" and (event is None or r.get("event") == event):
            tot += float(r.get("cost") or 0)
    return tot


def settle(http: Http, rows: list[dict], now: datetime | None = None) -> list[dict]:
    """Grade open rows whose markets have resolved. Returns newly settled rows."""
    now = now or utcnow()
    newly = []
    cache: dict[str, dict | None] = {}
    today = now.date().isoformat()
    for r in rows:
        if r.get("status") != "open":
            continue
        if r.get("date") and r["date"] >= today:
            continue   # can't have settled yet
        t = r["ticker"]
        if t not in cache:
            cache[t] = kalshi.get_market(http, t)
        m = cache[t]
        if not m:
            continue
        result = (m.get("result") or "").lower()
        status = (m.get("status") or "").lower()
        if result not in ("yes", "no"):
            if status in ("settled", "finalized") and result:
                r["status"] = "void"
                r["settled_at"] = now.isoformat(timespec="seconds")
                newly.append(r)
            continue
        won = result == r["side"]
        n = int(r.get("contracts") or 0)
        cost, fee = float(r.get("cost") or 0), float(r.get("fee") or 0)
        r["status"] = "won" if won else "lost"
        r["pnl"] = f"{(n if won else 0) - cost - fee:.2f}"
        r["cli_value"] = m.get("expiration_value") or ""
        r["settled_at"] = now.isoformat(timespec="seconds")
        newly.append(r)
    return newly


def stats(rows: list[dict], kind: str | None = None, since: datetime | None = None) -> dict:
    done = [r for r in rows if r.get("status") in ("won", "lost")
            and (kind is None or r.get("kind") == kind)
            and (since is None or (parse_iso(r.get("settled_at")) or since) >= since)]
    wins = sum(r["status"] == "won" for r in done)
    pnl = sum(float(r.get("pnl") or 0) for r in done)
    staked = sum(float(r.get("cost") or 0) + float(r.get("fee") or 0) for r in done)
    # Brier score: model vs market, on our side's probability
    bm = [(float(r["model_p"]) - (r["status"] == "won")) ** 2 for r in done if r.get("model_p")]
    bk = [(float(r["market_p"]) - (r["status"] == "won")) ** 2 for r in done if r.get("market_p")]
    return {
        "n": len(done), "wins": wins, "losses": len(done) - wins, "pnl": round(pnl, 2),
        "staked": round(staked, 2), "roi": (pnl / staked) if staked else 0.0,
        "brier_model": sum(bm) / len(bm) if bm else None,
        "brier_market": sum(bk) / len(bk) if bk else None,
        "open": sum(r.get("status") == "open" for r in rows),
        "open_cost": round(open_exposure(rows), 2),
    }


def record_line(rows: list[dict]) -> str:
    s = stats(rows)
    if not s["n"]:
        return f"Paper record: no settled alerts yet ({s['open']} open)"
    sign = "+" if s["pnl"] >= 0 else "-"
    return (f"Paper record: {s['wins']}-{s['losses']}, {sign}${abs(s['pnl']):.2f} "
            f"({s['roi'] * 100:+.1f}% on ${s['staked']:.2f})")
