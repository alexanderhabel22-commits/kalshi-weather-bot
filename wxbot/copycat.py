"""Copycat, shadow mode (Nov 10, 2026; leaderboard study idea 2): follow proven winners' public trades.

SHADOW ONLY: nothing here places, changes or cancels an order, and it never reads the Kalshi
trading key. It answers one question: when a proven Kalshi winner whose trades are public makes a
trade, does the price keep moving their way afterwards, and do we see the trade soon enough to
follow it?

How it works (one thread, polite polling of Kalshi's public social API):
  - every 12 h, build the watch list: profitable accounts (lifetime pnl >= MIN_PNL) from the
    all-time and category profit boards whose trade history is public;
  - poll each watched account's public trades round-robin (about every 30-60 s per account);
  - each new fill is logged with the sharp's side (yes/no), price, size, maker/taker, the trade's
    own timestamp and when we saw it (detection lag);
  - the market is snapshotted (best bid/ask, last) when we see the fill and at +1, +5, +30 min.
    Settlement results are joined later by the grading script, from the public market data;
  - hourly files copycat-YYYY-MM-DD-HH.jsonl are uploaded to branch copycat-data (gzipped)
    once the hour ends (needs GITHUB_TOKEN; without it files stay on disk).

Research only: sports stay excluded from real money whatever this shows.
Run: python bot.py copycat   (systemd unit deploy/m15/kalshi-copycat.service on the server)
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import requests

from wxbot.srclog import UA, github_put

log = logging.getLogger("wxbot")
ET = ZoneInfo("America/New_York")
SOCIAL = "https://api.elections.kalshi.com/v1/social"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
BRANCH = "copycat-data"
MIN_PNL = 50_000            # dollars of lifetime profit to be watched
MAX_WATCH = 40
SNAP_DELAYS = (0, 60, 300, 1800)
SNAP_GAP_S = 300            # at most one snapshot series per (account, ticker) per 5 min
MAX_PENDING = 4000          # cap on queued snapshots
REQ_GAP_S = 0.4             # spacing between API calls (~150/min; a 3/s test had 0 errors)
WATCH_REFRESH_S = 12 * 3600
CATEGORIES = ("Sports", "Politics", "Crypto", "Economics", "Climate and Weather", "Financials",
              "Entertainment", "Mentions", "Combos", "Commodities", "Elections", "Companies")
SEED = ("BWmarkets", "thebigdon", "BitcoinTradingChallenge", "szg.szg", "brief.theme",
        "CSPTRADINGonX", "tempChecker73", "troubled.owl0488", "lost.buck", "ripjohnpork",
        "balla23", "crow.hcis", "reluctant.fox5394", "spoon.rope", "pentasir", "far.bike")


def _px(v) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return round(v / 100, 4) if v > 1.5 else v


def sharp_side(t: dict, nick: str) -> tuple[str | None, str | None]:
    """(role, side the sharp ended up long: 'yes'/'no') for one public trade row."""
    ts = t.get("taker_side")
    act = t.get("taker_action", "buy")
    if ts not in ("yes", "no"):
        return None, None
    taker_long = ts if act == "buy" else ("no" if ts == "yes" else "yes")
    other = "no" if taker_long == "yes" else "yes"
    if t.get("taker_nickname") == nick:
        return "taker", taker_long
    if t.get("maker_nickname") == nick:
        return "maker", other
    return None, None


class Copycat:
    def __init__(self, out_dir: Path, session: requests.Session | None = None, clock=time.time,
                 uploader: Callable[[str, bytes], None] | None = None, req_gap: float = REQ_GAP_S):
        self.out = out_dir
        self.out.mkdir(parents=True, exist_ok=True)
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": UA})
        self.clock = clock
        self.uploader = uploader
        self.req_gap = req_gap
        self.watch: dict[str, dict] = {}         # nick -> {"pnl":..., "cats":...}
        self.seen: dict[str, set[str]] = {}       # nick -> trade ids already logged
        self.last_snap: dict[tuple[str, str], float] = {}
        self.snaps: list[tuple[float, int, str, str, str]] = []  # (due, delay, nick, ticker, trade_id)
        self.watch_next = 0.0
        self.rr = 0
        self.uploaded: set[str] = set()
        self._lock = threading.Lock()

    # -- files
    def _hour_file(self, ts: float) -> Path:
        d = datetime.fromtimestamp(ts, timezone.utc)
        return self.out / f"copycat-{d:%Y-%m-%d-%H}.jsonl"

    def write(self, row: dict) -> None:
        row.setdefault("ts", round(self.clock(), 3))
        row["et"] = datetime.fromtimestamp(row["ts"], ET).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, open(self._hour_file(row["ts"]), "a") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")

    def upload_done_hours(self, now: float) -> None:
        if not self.uploader:
            return
        cur = self._hour_file(now).name
        for p in sorted(self.out.glob("copycat-*.jsonl")):
            if p.name == cur or p.name in self.uploaded:
                continue
            try:
                self.uploader(f"copycat/{p.name}.gz", gzip.compress(p.read_bytes()))
                self.uploaded.add(p.name)
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat upload %s: %s", p.name, exc)
                return

    # -- api
    def get(self, url: str, **params):
        r = self.s.get(url, params=params, timeout=20)
        if self.req_gap:
            time.sleep(self.req_gap)
        if r.status_code != 200:
            raise RuntimeError(f"{r.status_code} {url}")
        return r.json()

    def build_watch(self) -> None:
        names: dict[str, None] = dict.fromkeys(SEED)
        boards = [dict(metric_name="projected_pnl", since_day_before=0)]
        boards += [dict(metric_name="projected_pnl", since_day_before=0, category=c) for c in CATEGORIES]
        for b in boards:
            try:
                for r in self.get(f"{SOCIAL}/leaderboard", limit=100, **b).get("rank_list") or []:
                    if not r.get("is_anonymous") and r.get("nickname"):
                        names.setdefault(r["nickname"], None)
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat board %s: %s", b, exc)
        keep: dict[str, dict] = {}
        for n in names:
            if len(keep) >= MAX_WATCH:
                break
            try:
                t = self.get(f"{SOCIAL}/trades", nickname=n, limit=1)
                if not t.get("trades"):
                    continue
                m = self.get(f"{SOCIAL}/profile/metrics", nickname=n).get("metrics") or {}
                pnl = (m.get("pnl") or 0) / 1e4
                if pnl < MIN_PNL:
                    continue
                p = (self.get(f"{SOCIAL}/profile", nickname=n).get("social_profile") or {})
                keep[n] = {"pnl": round(pnl), "contracts": m.get("volume"),
                           "markets": m.get("num_markets_traded"),
                           "cats": p.get("top_categories"), "joined": p.get("joined_at")}
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat check %s: %s", n, exc)
        if keep:
            self.watch = keep
        self.write({"kind": "watch", "accounts": self.watch})

    def snapshot(self, ticker: str) -> dict:
        m = self.get(f"{KALSHI}/markets/{ticker}").get("market") or {}
        return {"bid": _px(m.get("yes_bid_dollars", m.get("yes_bid"))),
                "ask": _px(m.get("yes_ask_dollars", m.get("yes_ask"))),
                "last": _px(m.get("last_price_dollars", m.get("last_price"))),
                "status": m.get("status"), "result": m.get("result") or None}

    # -- one account
    def poll(self, nick: str, now: float) -> None:
        trades = self.get(f"{SOCIAL}/trades", nickname=nick, limit=50).get("trades") or []
        first = nick not in self.seen
        seen = self.seen.setdefault(nick, set())
        for t in reversed(trades):
            tid = t.get("trade_id")
            if not tid or tid in seen:
                continue
            seen.add(tid)
            role, side = sharp_side(t, nick)
            row = {"kind": "fill", "nick": nick, "trade_id": tid, "ticker": t.get("ticker"),
                   "role": role, "side": side, "yes_price": _px(t.get("price_dollars")),
                   "count": t.get("count_fp", t.get("count")), "trade_time": t.get("create_date"),
                   "backfill": first}
            try:
                ct = datetime.fromisoformat(str(t.get("create_date")).replace("Z", "+00:00")).timestamp()
                row["lag_s"] = round(now - ct, 1)
            except ValueError:
                pass
            self.write(row)
            key = (nick, t.get("ticker") or "")
            if first or now - self.last_snap.get(key, 0) < SNAP_GAP_S or len(self.snaps) > MAX_PENDING:
                continue
            self.last_snap[key] = now
            for d in SNAP_DELAYS:
                self.snaps.append((now + d, d, nick, key[1], tid))
        if len(seen) > 5000:                       # keep memory bounded
            self.seen[nick] = {t.get("trade_id") for t in trades}

    def run_snaps(self, now: float) -> None:
        due = [x for x in self.snaps if x[0] <= now]
        self.snaps = [x for x in self.snaps if x[0] > now]
        for _, d, nick, ticker, tid in sorted(due):
            try:
                self.write({"kind": "snap", "nick": nick, "ticker": ticker, "trade_id": tid,
                            "delay": d, **self.snapshot(ticker)})
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat snap %s: %s", ticker, exc)

    def step(self) -> None:
        now = self.clock()
        if now >= self.watch_next:
            self.watch_next = now + WATCH_REFRESH_S
            self.build_watch()
        names = list(self.watch)
        if names:
            nick = names[self.rr % len(names)]
            self.rr += 1
            try:
                self.poll(nick, self.clock())
            except Exception as exc:  # noqa: BLE001
                log.warning("copycat poll %s: %s", nick, exc)
        self.run_snaps(self.clock())

    def run(self) -> None:
        last_up = 0.0
        self.write({"kind": "start"})
        while True:
            try:
                self.step()
                if self.clock() - last_up > 300:
                    last_up = self.clock()
                    self.upload_done_hours(last_up)
            except Exception as exc:  # noqa: BLE001  never die; log and keep going
                log.warning("copycat step: %s", exc)
                time.sleep(10)
            if not self.watch:
                time.sleep(60)


def main() -> int:
    out = Path(os.environ.get("COPYCAT_DIR", "/var/lib/kalshi-copycat"))
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    for k in list(os.environ):          # shadow only: drop the trading key and other secrets
        if k.startswith(("KALSHI", "NTFY", "ANTHROPIC", "OANDA")):
            os.environ.pop(k, None)
    Copycat(out, uploader=github_put(token, branch=BRANCH) if token else None).run()
    return 0
