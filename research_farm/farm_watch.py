"""Farm daily-series listing watcher (added Nov 20, 2026). No orders, no Kalshi key.

Kalshi created daily/weekly series for corn, soybeans, wheat, live cattle, cocoa and coffee
(KXCORND ... KXCOFFEED) but, as of Nov 20 2026 2:05 AM ET, none had a market listed. Once they
list, the first test to run is the spx_range "frozen window" bracket test (grain and cattle
futures are shut part of the day, like the S&P overnight).

Each run: for every watched series, count open markets (one public call each). The first time
a series has open markets, push ONE phone note (one combined push per run, only on a hit).
Also picks up any other Commodities-category series whose ticker mentions a farm product, so
renamed or extra series are caught too. State lives in <state_dir>/farm_watch.json.

Usage: python research_farm/farm_watch.py <state_dir>
Env: NTFY_TOPIC (optional; without it the hit is only printed), DRY_RUN=1 (never push).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://api.elections.kalshi.com/trade-api/v2"
WATCH = [
    "KXCORND", "KXSOYBEAND", "KXWHEATD", "KXLCATTLED", "KXCOCOAD", "KXCOFFEED",
    "KXCORNW", "KXSOYBEANW", "KXWHEATW", "KXLCATTLEW", "KXCOCOAW", "KXCOFFEEW", "KXCOFFEEW2",
    "KXSUGARW", "KXCOFFEEH", "KXCOFFEE15M",
]
FARM_RE = re.compile(r"CORN|SOYBEAN|WHEAT|CATTLE|HOG|COCOA|COFFEE|SUGAR|COTTON|OATS")
ET = ZoneInfo("America/New_York")


def get(path: str, tries: int = 5):
    for i in range(tries):
        try:
            req = urllib.request.Request(BASE + path, headers={"User-Agent": "wxbot-farm-watch"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
        except Exception:
            pass
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"Kalshi API failed: {path}")


def extra_series(series: list[dict]) -> list[str]:
    """Other recurring Commodities series about farm products (not monthly/annual one-offs)."""
    out = []
    for s in series:
        t = s.get("ticker") or ""
        if (s.get("category") == "Commodities" and FARM_RE.search(t)
                and s.get("frequency") in ("daily", "weekly", "hourly", "fifteen_min")
                and t not in WATCH):
            out.append(t)
    return sorted(out)


def summarize(ms: list[dict]) -> dict:
    spreads, vol = [], 0.0
    for m in ms:
        try:
            b, a = float(m.get("yes_bid_dollars") or 0), float(m.get("yes_ask_dollars") or 0)
            if 0 < a <= 1 and a >= b:
                spreads.append(round(a - b, 4))
            vol += float(m.get("volume_fp") or 0)
        except (TypeError, ValueError):
            pass
    spreads.sort()
    return {"n_open": len(ms), "volume": round(vol, 1),
            "median_spread": spreads[len(spreads) // 2] if spreads else None,
            "example": ms[0].get("ticker") if ms else None,
            "close_time": ms[0].get("close_time") if ms else None}


def push_text(hits: list[dict]) -> tuple[str, str]:
    names = ", ".join(h["series"] for h in hits)
    title = f"Farm markets listed on Kalshi: {names}"[:120]
    lines = []
    for h in hits:
        sp = h.get("median_spread")
        lines.append(f"- {h['series']}: {h['n_open']} markets open"
                     + (f", median spread {sp * 100:.0f}c" if sp is not None else ""))
    lines.append("Info only, no orders. Claude runs the frozen-window bracket test next; "
                 "nothing goes to real money without your OK.")
    return title, "\n".join(lines)


def send(title: str, msg: str, click: str) -> bool:
    topic = os.environ.get("NTFY_TOPIC")
    print(f"=== {title} ===\n{msg}")
    if not topic or os.environ.get("DRY_RUN") == "1":
        return False
    body = json.dumps({"topic": topic, "title": title, "message": msg, "priority": 3,
                       "tags": ["corn"], "click": click}).encode()
    req = urllib.request.Request("https://ntfy.sh", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except Exception as e:  # noqa: BLE001
        print("ntfy failed:", e)
        return False


def run(state_dir: Path, now: datetime, fetch=get, sender=send) -> dict:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "farm_watch.json"
    st = json.loads(path.read_text()) if path.exists() else {}
    listed = st.setdefault("listed", {})
    allser = (fetch("/series") or {}).get("series") or []
    watch = WATCH + [t for t in extra_series(allser) if t not in WATCH]
    hits, counts = [], {}
    for t in watch:
        js = fetch(f"/markets?series_ticker={t}&status=open&limit=200") or {}
        ms = js.get("markets") or []
        counts[t] = len(ms)
        if ms and t not in listed:
            info = summarize(ms)
            info.update(series=t, first_seen=now.isoformat(timespec="seconds"))
            listed[t] = info
            hits.append(info)
    if hits:
        ex = hits[0].get("example") or ""
        click = f"https://kalshi.com/markets/{hits[0]['series'].lower()}" if ex else "https://kalshi.com/"
        title, msg = push_text(hits)
        st.setdefault("pushes", []).append({"at": now.isoformat(timespec="seconds"),
                                            "series": [h["series"] for h in hits],
                                            "sent": sender(title, msg, click)})
    st["last_check"] = now.isoformat(timespec="seconds")
    st["last_check_et"] = now.astimezone(ET).strftime("%b %d %I:%M %p ET")
    st["open_counts"] = counts
    st.setdefault("log", []).append({"at": st["last_check"], "open": sum(counts.values()),
                                     "hits": [h["series"] for h in hits]})
    st["log"] = st["log"][-400:]
    path.write_text(json.dumps(st, indent=1, sort_keys=True))
    return st


if __name__ == "__main__":
    out = run(Path(sys.argv[1] if len(sys.argv) > 1 else "farm_state"), datetime.now(timezone.utc))
    print("checked", len(out["open_counts"]), "series; open markets:",
          {k: v for k, v in out["open_counts"].items() if v} or "none")
