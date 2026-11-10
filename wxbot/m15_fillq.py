"""Fill-quality log for the 15-minute bot (shadow only: never changes what the bot trades).

Built Nov 10, 2026 (thread "Measure 15-min fill quality"). For every live fill it records
where the favourite's price went 10 s, 60 s and 5 min after we were filled, plus our place
in Kalshi's queue while the order rested. That answers "are we the last one filled before
the price drops?" (adverse selection) and feeds the replay test of +1c price improvement
and different bid times (research_fillq/).

One row per fill event in data/m15/fills-YYYY-MM-DD.csv (ET day of the fill), written once
every horizon is known and the market has settled:

  move_10s / move_60s / move_300s   favourite-side mid minus our fill price, in cents
                                    (+ = the price went our way). A horizon that falls after
                                    the close uses the settlement (100 or 0) instead and is
                                    listed in `settled_h`.
  queue_first / queue_last          contracts ahead of us in the queue (first sample after
                                    placing, last sample before the fill); queue_last_age_s
                                    says how stale the last one was.

Every call here is wrapped so a failure only loses a log row, never a trade.
"""
from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone

log = logging.getLogger("wxbot")

HORIZONS = (10, 60, 300)
LATE_S = 20            # a mark taken more than this long after its horizon is left blank
KEEP_S = 2 * 86400     # pending rows older than this are dropped (market never graded)
COLS = ["ticker", "series", "mode", "side", "fill_ts", "fill_at", "ttc_s", "rest_s", "nth",
        "contracts", "price", "bid", "ask", "spread", "queue_first", "queue_last",
        "queue_last_age_s", "spot_bps", "move_10s", "move_60s", "move_300s", "settled_h",
        "result", "won", "pnl_ct"]


def fav_book(quote: dict | None, side: str) -> tuple[float | None, float | None]:
    """(bid, ask) of `side` from a YES-book quote dict."""
    if not quote:
        return None, None

    def f(k):
        try:
            v = quote.get(k)
            return None if v in (None, "") else float(v)
        except (TypeError, ValueError):
            return None
    yb, ya = f("yes_bid_dollars"), f("yes_ask_dollars")
    if side == "yes":
        return yb, ya
    return (round(1 - ya, 2) if ya is not None else None,
            round(1 - yb, 2) if yb is not None else None)


def fav_mid(quote: dict | None, side: str) -> float | None:
    b, a = fav_book(quote, side)
    if b is not None and a is not None and a >= b:
        return (a + b) / 2
    return b if b is not None else a


def parse_queue(js) -> dict:
    """{order_id: contracts ahead} from Kalshi's queue position replies (batch or single)."""
    out = {}
    rows = (js or {}).get("queue_positions") or []
    for r in rows if isinstance(rows, list) else []:
        oid = r.get("order_id")
        v = r.get("queue_position_fp", r.get("queue_position"))
        try:
            if oid and v not in (None, ""):
                out[oid] = float(v)
        except (TypeError, ValueError):
            continue
    return out


class FillQuality:
    """Mixed into m15.Bot (uses self.state, self.clock, self.quote, self.client, self.s)."""

    # -------------------------------------------------------------- queue

    def queue_check(self, now: float) -> None:
        if not self.s.get("fillq", True) or self.client is None:
            return
        if now - self.last.get("queue", 0) < float(self.s.get("queue_s", 5)):
            return
        mine = [m for m in self.state["markets"].values()
                if m.get("mode") == "live" and m.get("order") and m["order"].get("id")]
        if not mine:
            return
        self.last["queue"] = now
        try:
            got = parse_queue(self.client.queue_positions(sorted({m["ticker"] for m in mine})))
        except Exception as exc:          # endpoint missing / renamed: just no queue data
            if not getattr(self, "_queue_warned", False):
                log.warning("queue positions: %s", exc)
                self._queue_warned = True
            return
        for m in mine:
            o = m["order"]
            v = got.get(o["id"])
            if v is None:
                continue
            o.setdefault("queue_first", v)
            o["queue_last"] = v
            o["queue_at"] = now

    # -------------------------------------------------------------- fills

    def fillq_open(self, m: dict, o: dict, new: float, ts: float | None) -> None:
        if not self.s.get("fillq", True) or m.get("mode") != "live" or new <= 0:
            return
        try:
            now = self.clock()
            t = float(ts or now)
            side = o.get("side") or m.get("fav_side") or ""
            b, a = fav_book(self.quote(m["ticker"], now), side)
            qa = o.get("queue_at")
            mv = m.get("spot_bps", "") if not m.get("fillq_n") else ""
            m["fillq_n"] = int(m.get("fillq_n", 0)) + 1
            self.state.setdefault("fillq", []).append({
                "ticker": m["ticker"], "series": m["series"], "mode": m["mode"], "side": side,
                "t": round(t, 1), "close": m["close"],
                "rest_s": round(t - o["t0"], 1) if o.get("t0") else "",
                "nth": m["fillq_n"], "n": new, "q": o["q"], "bid": b, "ask": a,
                "queue_first": o.get("queue_first", ""), "queue_last": o.get("queue_last", ""),
                "queue_age": round(t - qa, 1) if qa else "", "spot_bps": mv, "marks": {}})
        except Exception as exc:
            log.warning("fill log %s: %s", m.get("ticker"), exc)

    def fillq_step(self, now: float) -> None:
        pend = self.state.get("fillq") or []
        if not pend:
            return
        keep = []
        for p in pend:
            try:
                if self._fillq_mark(p, now):
                    continue                     # written
            except Exception as exc:
                log.warning("fill log %s: %s", p.get("ticker"), exc)
            if now - p["t"] < KEEP_S:
                keep.append(p)
        self.state["fillq"] = keep

    def _fillq_mark(self, p: dict, now: float) -> bool:
        m = self.state["markets"].get(p["ticker"]) or {}
        for h in HORIZONS:
            k = str(h)
            if k in p["marks"]:
                continue
            at = p["t"] + h
            if at >= p["close"] - 1:
                continue                         # filled in from the settlement below
            if now < at:
                continue
            mid = fav_mid(self.quote(p["ticker"], now), p["side"]) if now - at <= LATE_S else None
            p["marks"][k] = "" if mid is None else round((mid - p["q"]) * 100, 1)
        if not m.get("graded"):
            return False
        settled = []
        res = m.get("result")
        won = res == p["side"] if res in ("yes", "no") else None
        for h in HORIZONS:
            k = str(h)
            if k not in p["marks"]:
                if p["t"] + h < p["close"] - 1 and now < p["t"] + h:
                    return False                 # graded early? wait for the horizon anyway
                p["marks"][k] = "" if won is None else round(((1.0 if won else 0.0) - p["q"]) * 100, 1)
                settled.append(k)
        self._fillq_write(p, res, won, settled)
        return True

    def _fillq_write(self, p: dict, res, won, settled) -> None:
        path = self.fillq_dir() / f"fills-{self.day(p['t'])}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        b, a = p.get("bid"), p.get("ask")
        row = {"ticker": p["ticker"], "series": p["series"], "mode": p["mode"], "side": p["side"],
               "fill_ts": int(p["t"]),
               "fill_at": datetime.fromtimestamp(p["t"], timezone.utc).isoformat(timespec="seconds"),
               "ttc_s": int(p["close"] - p["t"]), "rest_s": p.get("rest_s", ""), "nth": p["nth"],
               "contracts": p["n"], "price": p["q"],
               "bid": "" if b is None else b, "ask": "" if a is None else a,
               "spread": "" if (a is None or b is None) else round((a - b) * 100),
               "queue_first": p.get("queue_first", ""), "queue_last": p.get("queue_last", ""),
               "queue_last_age_s": p.get("queue_age", ""), "spot_bps": p.get("spot_bps", ""),
               "move_10s": p["marks"].get("10", ""), "move_60s": p["marks"].get("60", ""),
               "move_300s": p["marks"].get("300", ""), "settled_h": ";".join(settled),
               "result": res or "", "won": "" if won is None else int(won),
               "pnl_ct": "" if won is None else round(((1.0 if won else 0.0) - p["q"]) * 100, 1)}
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLS)
            if new:
                w.writeheader()
            w.writerow(row)
