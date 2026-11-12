"""Paper tests on the 15-min server (Nov 11, 2026). No orders, ever: this module has no order code
and only uses GET requests. Two studies from the overnight research
(/mnt/project-files/analysis/overnight-ideas/RESULTS.md):

1. MakerPaper (idea #3): be a market maker in wide-spread non-crypto markets (Mentions, Science/Tech,
   Financials, politics ...). Each minute, for ~20 markets with spreads of 3c+, we "rest" a 1-contract
   paper bid on each side 1c better than the best bid (so we're first in line). A later trade where
   a taker sells into bids at our price or better fills us. Every fill is marked against the mid
   5 and 30 minutes later (positive = we made money on the trade before fees).

2. RfqPaper (idea #2): watch Kalshi's public quote requests (RFQs) for parlays (combos). For combos
   whose legs are in different events (independent, so fair = product of the legs' prices) we log the
   quote we'd give (selling YES at fair + margin), whether the combo then traded above that price
   (we'd likely have won the request), and the result at settlement.

Casual-flow tags (Nov 11, thread "Find where casual traders lose money"): every maker fill also
carries the share of the market's taker contracts that came from small orders (<= $25), and is
scored again at settlement, split by that share and by the side we bought (history says the edge
sits in casual-heavy markets on the NO side: /mnt/project-files/analysis/casual-flow/RESULTS.md).

3. FieldPaper ("sell the field", Nov 11): in events with many candidates and one winner (Spotify
   Wrapped top artist, TIME Person of the Year, Super Bowl first song, Netflix rankings ...) small
   buyers pile into YES on the candidates. For every such open event we log a paper NO on each
   candidate priced 10-50c, bought at the NO ask (taker, the pessimistic way) and also marked at the
   mid, plus the sum of all YES asks at entry. Scored at settlement after the taker fee.

Status files go to the m15-data branch: m15/paper-mm.json, m15/paper-rfq.json and m15/paper-field.json
(hourly).
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .kalshi import BASE, parse_orderbook

log = logging.getLogger("wxbot")
PACE_S = 0.9
MM_CATS = ("Mentions", "Science and Technology", "Financials", "Politics", "Elections", "Economics",
           "World", "Entertainment", "Companies", "Climate and Weather", "Health", "Transportation",
           "Social")
MM = {"markets": 20, "per_event": 2, "min_spread": 0.03, "min_vol24": 100.0, "mid_lo": 0.10,
      "mid_hi": 0.90, "cycle_s": 60, "select_s": 6 * 3600, "max_inv": 10, "event_pages": 400,
      "small_usd": 25.0, "flow_min_ct": 50.0, "settle_checks": 40, "held_max": 2000}
FIELD_CATS = ("Entertainment", "Mentions", "Social", "Companies", "Science and Technology", "World", "Health")
FIELD = {"min_markets": 5, "yes_lo": 0.10, "yes_hi": 0.50, "min_vol24": 500.0, "per_event": 15,
         "max_open": 400, "settle_checks": 60, "keep_done": 3000}
RFQ = {"cycle_s": 20, "margins": (0.0, 0.05, 0.10, 0.20, 0.30, 0.50, 1.00), "check_s": 600, "keep_days": 4,
       "pages": 5,                  # the open-RFQ list is paged 100 at a time (one poll hit 100)
       # what a small real test would allow: per combo at most this many contracts and this much loss
       "cap_ct": 25, "max_loss": 3.0}


def capped_size(row: dict, q: float) -> float:
    """Contracts we would sell at quote q: the request's size, capped by cap_ct and max_loss."""
    n = row.get("contracts") or 0.0
    if not n and row.get("target_cost") and q > 0:       # size given as a dollar amount instead
        n = row["target_cost"] / q
    room = RFQ["max_loss"] / max(1e-9, 1 - q)            # selling YES at q loses (1 - q) per contract
    return float(max(0, int(min(n, RFQ["cap_ct"], room) + 1e-9)))


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def maker_fee(p: float) -> float:
    """Kalshi's maker fee per contract where a series charges one (0.0175 * p * (1-p))."""
    return 0.0175 * p * (1 - p)


class Lab:
    """Shared plumbing: pacing, logs, state."""

    def __init__(self, out: Path, http, clock=time.time, sleep=time.sleep):
        self.out, self.http, self.clock, self.sleep = out, http, clock, sleep
        self._last = 0.0

    def get(self, path: str, params: dict | None = None, signed=None) -> dict:
        wait = PACE_S - (self.clock() - self._last)
        if wait > 0:
            self.sleep(wait)
        self._last = self.clock()
        if signed is not None:
            return signed._get(path, params)          # read-only signed GET
        return self.http.get_json(BASE + path, params=params)

    def write(self, name: str, row: dict) -> None:
        try:
            with open(self.out / f"{name}-{time.strftime('%Y-%m-%d', time.gmtime(self.clock()))}.jsonl", "a") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            pass


def taker_fee(p: float) -> float:
    """Kalshi's taker fee per contract (0.07 * p * (1-p)), before any rounding."""
    return 0.07 * p * (1 - p)


class FieldPaper:
    """Paper "sell the field": NO on every 10-50c candidate of a many-candidate, one-winner event."""

    def __init__(self, lab: Lab):
        self.lab = lab
        try:
            self.st = json.loads((lab.out / "field_state.json").read_text())
        except (OSError, ValueError):
            self.st = {}
        for k, v in (("open", []), ("done", []), ("events", {}), ("seen", {})):
            self.st.setdefault(k, v)

    def count(self, why: str) -> None:
        self.st["seen"][why] = self.st["seen"].get(why, 0) + 1

    def save(self):
        (self.lab.out / "field_state.json").write_text(json.dumps(self.st))

    def consider(self, e: dict, now: float) -> None:
        """Called for every open event the maker's scan pages through. Enters each event once."""
        ev = e.get("event_ticker") or ""
        if not ev or ev in self.st["events"] or (e.get("category") or "") not in FIELD_CATS:
            return
        if not e.get("mutually_exclusive"):
            return self.count("not_one_winner")
        if len(self.st["open"]) >= FIELD["max_open"]:
            return self.count("book_full")
        quoted = []
        for m in e.get("markets") or []:
            b, a = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
            if m.get("status") not in ("active", "open") or a is None or not a:
                continue
            quoted.append((m.get("ticker") or "", b or 0.0, a, _f(m.get("volume_24h_fp"), 0.0) or 0.0))
        if len(quoted) < FIELD["min_markets"]:
            return self.count("under_5_candidates")
        if sum(q[3] for q in quoted) < FIELD["min_vol24"]:
            return self.count("too_quiet")
        ask_sum, bid_sum = round(sum(q[2] for q in quoted), 3), round(sum(q[1] for q in quoted), 3)
        picks = [q for q in quoted if q[1] > 0 and FIELD["yes_lo"] <= (q[1] + q[2]) / 2 <= FIELD["yes_hi"]]
        picks = sorted(picks, key=lambda q: -q[3])[:FIELD["per_event"]]
        if not picks:
            return self.count("no_candidate_10_50c")
        self.count("entered")
        self.st["events"][ev] = {"t": round(now), "cat": e.get("category"), "markets": len(quoted),
                                 "ask_sum": ask_sum, "bid_sum": bid_sum, "picks": len(picks)}
        for tk, b, a, v in picks:
            row = {"tk": tk, "event": ev, "cat": e.get("category"), "t": round(now),
                   "no_take": round(1 - b, 4),               # NO bought at the NO ask (= 1 - YES bid)
                   "no_mid": round(1 - (a + b) / 2, 4), "ask_sum": ask_sum, "n": len(quoted)}
            self.st["open"].append(row)
            self.lab.write("field", dict(row, kind="enter"))

    def settle(self) -> None:
        waiting, looked, checked = [], [], 0
        for r in self.st["open"]:
            if checked >= FIELD["settle_checks"]:
                waiting.append(r)                     # not looked at this round: first in line next time
                continue
            checked += 1
            try:
                m = self.lab.get(f"/markets/{r['tk']}").get("market") or {}
                res = str(m.get("result") or "").lower()
            except Exception as exc:  # noqa: BLE001
                log.info("paper field %s: %s", r["tk"], exc)
                res = ""
            if res not in ("yes", "no"):
                looked.append(r)
                continue
            won = 1.0 if res == "no" else 0.0
            r["result"] = res
            r["pnl_take"] = round((won - r["no_take"] - taker_fee(r["no_take"])) * 100, 2)
            r["pnl_mid"] = round((won - r["no_mid"]) * 100, 2)
            self.st["done"].append(r)
            self.lab.write("field", dict(r, kind="settled"))
        self.st["open"] = waiting + looked
        self.st["done"] = self.st["done"][-FIELD["keep_done"]:]
        live = {r["event"] for r in self.st["open"]} | {r["event"] for r in self.st["done"]}
        self.st["events"] = {k: v for k, v in self.st["events"].items() if k in live}

    def status(self) -> dict:
        def summ(rows):
            if not rows:
                return {"n": 0}
            by_ev: dict = {}
            for r in rows:
                by_ev.setdefault(r["event"], []).append(r["pnl_take"])
            ev_avg = [sum(v) / len(v) for v in by_ev.values()]
            return {"n": len(rows), "events": len(by_ev),
                    "taker_c": round(sum(r["pnl_take"] for r in rows) / len(rows), 2),
                    "mid_c": round(sum(r["pnl_mid"] for r in rows) / len(rows), 2),
                    "taker_c_event_avg": round(sum(ev_avg) / len(ev_avg), 2),
                    "events_positive": sum(1 for x in ev_avg if x > 0),
                    "no_won": sum(1 for r in rows if r["result"] == "no")}
        done = self.st["done"]
        cats = sorted({r["cat"] for r in done})
        return {"what": "paper NO on every 10-50c candidate in many-candidate, one-winner events",
                "scan_counts": self.st["seen"], "open": len(self.st["open"]), "open_events": len({r["event"] for r in self.st["open"]}),
                "all": summ(done), "by_category": {c: summ([r for r in done if r["cat"] == c]) for c in cats},
                "events": dict(sorted(self.st["events"].items(), key=lambda kv: -kv[1]["t"])[:60]),
                "recent": done[-40:]}


class MakerPaper:
    def __init__(self, lab: Lab, field: FieldPaper | None = None):
        self.lab = lab
        self.field = field
        p = lab.out / "mm_state.json"
        try:
            self.st = json.loads(p.read_text())
        except (OSError, ValueError):
            self.st = {}
        for k, v in (("markets", {}), ("orders", {}), ("pending", []), ("inv", {}), ("seen", {}),
                     ("selected_at", 0.0), ("stats", {}), ("hours", {}), ("last", 0.0),
                     ("flow", {}), ("held", []), ("flow_stats", {})):
            self.st.setdefault(k, v)

    def save(self):
        (self.lab.out / "mm_state.json").write_text(json.dumps(self.st))

    def select(self) -> None:
        now = self.lab.clock()
        cands, cur = [], None
        info = {"t": round(now), "pages": 0, "events": 0, "error": None, "done": False}
        for _ in range(MM["event_pages"]):
            p = {"status": "open", "with_nested_markets": "true", "limit": 200}
            if cur:
                p["cursor"] = cur
            try:
                js = self.lab.get("/events", p)
            except Exception as exc:  # noqa: BLE001
                log.info("paper mm events: %s", exc)
                info["error"] = str(exc)[:200]
                break
            info["pages"] += 1
            info["events"] += len(js.get("events") or [])
            for e in js.get("events") or []:
                cat = e.get("category") or ""
                if self.field is not None:
                    try:
                        self.field.consider(e, now)
                    except Exception as exc:  # noqa: BLE001
                        log.info("paper field consider: %s", exc)
                if cat not in MM_CATS:
                    continue
                for m in e.get("markets") or []:
                    tk = m.get("ticker") or ""
                    if tk.split("-")[0].endswith("15M") or m.get("status") not in ("active", "open"):
                        continue
                    b, a = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
                    v = _f(m.get("volume_24h_fp"), 0.0) or 0.0
                    if b is None or a is None or not b or a >= 1:
                        continue
                    mid = (a + b) / 2
                    if a - b < MM["min_spread"] - 1e-9 or not (MM["mid_lo"] <= mid <= MM["mid_hi"]) \
                            or v < MM["min_vol24"]:
                        continue
                    cands.append((v, tk, e.get("event_ticker"), cat))
            cur = js.get("cursor")
            if not cur:
                info["done"] = True
                break
        info["candidates"] = len(cands)
        self.st["select_info"] = info
        cands.sort(reverse=True)
        # Mentions first (the lead from the overnight replay: ~6c spreads), up to 8 of the slots
        ment = [c for c in cands if c[3] == "Mentions"][:8]
        cands = ment + [c for c in cands if c not in ment]
        chosen, per = {}, {}
        for v, tk, ev, cat in cands:
            if per.get(ev, 0) >= MM["per_event"]:
                continue
            per[ev] = per.get(ev, 0) + 1
            chosen[tk] = {"cat": cat, "event": ev, "vol24": v}
            if len(chosen) >= MM["markets"]:
                break
        if chosen:
            self.st["markets"] = chosen
            self.st["orders"] = {k: v for k, v in self.st["orders"].items() if k.split("|")[0] in chosen}
        # a scan that failed before reading anything is tried again in 10 minutes, not in 6 hours
        self.st["selected_at"] = now if info["events"] else now - MM["select_s"] + 600
        self.lab.write("mm", {"t": round(now), "kind": "select", "info": info, "candidates": len(cands),
                              "chosen": {k: v["cat"] for k, v in chosen.items()}})

    @staticmethod
    def mid(book: dict) -> float | None:
        y, n = book.get("yes") or [], book.get("no") or []
        if not y or not n:
            return None
        return (max(p for p, _ in y) + 1 - max(p for p, _ in n)) / 2

    def fills_from(self, tk: str, trades: list, now: float, mid: float | None) -> None:
        seen = self.st["seen"].setdefault(tk, [])
        self.tally_flow(tk, [t for t in trades if t.get("trade_id") not in seen])
        for t in sorted(trades, key=lambda t: t.get("created_time") or ""):
            tid = t.get("trade_id")
            if tid in seen:
                continue
            seen.append(tid)
            ts, yp, tks = _ts(t.get("created_time")), _f(t.get("yes_price_dollars")), t.get("taker_side")
            if ts is None or yp is None:
                continue
            for side in ("yes", "no"):
                o = self.st["orders"].get(f"{tk}|{side}")
                if not o or ts < o["at"]:
                    continue
                hit = (side == "yes" and tks == "no" and yp <= o["px"] + 1e-9) or \
                      (side == "no" and tks == "yes" and 1 - yp <= o["px"] + 1e-9)
                if not hit:
                    continue
                inv = self.st["inv"].get(tk, 0) + (1 if side == "yes" else -1)
                self.st["inv"][tk] = inv
                self.st["orders"].pop(f"{tk}|{side}")
                val0 = (mid if side == "yes" else 1 - mid) if mid is not None else None
                f = {"tk": tk, "cat": self.st["markets"].get(tk, {}).get("cat", "?"), "side": side,
                     "px": o["px"], "t": ts, "spread": o["spread"],
                     "edge0": round((val0 - o["px"]) * 100, 2) if val0 is not None else None,
                     "fee": round(maker_fee(o["px"]) * 100, 2), "casual": self.casual_share(tk)}
                self.st["pending"].append(f)
                self.st["held"].append({k: f[k] for k in ("tk", "side", "px", "t", "casual")})
                self.st["held"] = self.st["held"][-MM["held_max"]:]
                self.lab.write("mm", dict(f, kind="fill"))
        self.st["seen"][tk] = seen[-300:]

    def tally_flow(self, tk: str, new: list) -> None:
        """Adds new trades to the market's running count of taker contracts, and of those that came
        from small orders. One order that sweeps several price levels prints several trades with the
        same time and side, so those are summed into one order first."""
        orders: dict = {}
        for t in new:
            yp, n = _f(t.get("yes_price_dollars")), _f(t.get("count_fp", t.get("count")), 0.0) or 0.0
            if yp is None or n <= 0:
                continue
            side = t.get("taker_side")
            o = orders.setdefault((t.get("created_time"), side), [0.0, 0.0])
            o[0] += n
            o[1] += n * (yp if side == "yes" else 1 - yp)
        if not orders:
            return
        fl = self.st["flow"].setdefault(tk, {"all": 0.0, "small": 0.0})
        for n, cost in orders.values():
            fl["all"] = round(fl["all"] + n, 2)
            if cost <= MM["small_usd"]:
                fl["small"] = round(fl["small"] + n, 2)

    def casual_share(self, tk: str) -> float | None:
        """Share of the market's taker contracts (seen so far) that came from orders of $25 or less."""
        fl = self.st["flow"].get(tk)
        if not fl or fl["all"] < MM["flow_min_ct"]:
            return None
        return round(fl["small"] / fl["all"], 3)

    @staticmethod
    def flow_key(casual: float | None, side: str) -> str:
        b = "unknown" if casual is None else "under20" if casual < 0.2 else "20to50" if casual < 0.5 else "50plus"
        return f"{b}|{side}"

    def flow_stat(self, f: dict) -> dict:
        return self.st["flow_stats"].setdefault(self.flow_key(f.get("casual"), f["side"]), {
            "fills": 0, "mk30": 0.0, "n30": 0, "settled": 0, "settle": 0.0, "fee": 0.0})

    def settle(self) -> None:
        """Scores held paper fills whose market has settled: result minus the price paid (cents)."""
        waiting, looked, checked, res = [], [], 0, {}
        for h in self.st["held"]:
            tk = h["tk"]
            if tk not in res:
                if checked >= MM["settle_checks"]:
                    waiting.append(h)                 # not looked at this round: first in line next time
                    continue
                checked += 1
                try:
                    m = self.lab.get(f"/markets/{tk}").get("market") or {}
                    res[tk] = str(m.get("result") or "").lower()
                except Exception as exc:  # noqa: BLE001
                    log.info("paper mm settle %s: %s", tk, exc)
                    res[tk] = ""
            if res[tk] not in ("yes", "no"):
                looked.append(h)
                continue
            pnl = round(((1.0 if res[tk] == h["side"] else 0.0) - h["px"]) * 100, 2)
            s = self.flow_stat(h)
            s["settled"] += 1
            s["settle"] = round(s["settle"] + pnl, 2)
            self.lab.write("mm", dict(h, kind="settled", result=res[tk], pnl=pnl))
        self.st["held"] = waiting + looked
        held = {h["tk"] for h in self.st["held"]}
        self.st["flow"] = {k: v for k, v in self.st["flow"].items()
                           if k in self.st["markets"] or k in held}

    def markouts(self, mids: dict, now: float) -> None:
        keep = []
        for f in self.st["pending"]:
            m = mids.get(f["tk"])
            for k, dt in (("mk5", 300), ("mk30", 1800)):
                if k not in f and now >= f["t"] + dt and m is not None:
                    f[k] = round(((m if f["side"] == "yes" else 1 - m) - f["px"]) * 100, 2)
            if "mk30" in f or now > f["t"] + 6 * 3600:
                s = self.st["stats"].setdefault(f["cat"], {"fills": 0, "edge0": 0.0, "mk5": 0.0, "mk30": 0.0,
                                                           "fee": 0.0, "n5": 0, "n30": 0})
                s["fills"] += 1
                s["edge0"] += f.get("edge0") or 0.0
                s["fee"] += f["fee"]
                if f.get("mk5") is not None:
                    s["mk5"] += f["mk5"]
                    s["n5"] += 1
                if f.get("mk30") is not None:
                    s["mk30"] += f["mk30"]
                    s["n30"] += 1
                fs = self.flow_stat(f)
                fs["fills"] += 1
                fs["fee"] = round(fs["fee"] + f["fee"], 2)
                if f.get("mk30") is not None:
                    fs["mk30"] = round(fs["mk30"] + f["mk30"], 2)
                    fs["n30"] += 1
                self.lab.write("mm", dict(f, kind="markout"))
            else:
                keep.append(f)
        self.st["pending"] = keep

    def cycle(self) -> None:
        now = self.lab.clock()
        if now - self.st["selected_at"] > MM["select_s"] or not self.st["markets"] \
                or "select_info" not in self.st:              # first run of this version: scan now
            self.select()
            try:
                self.settle()
            except Exception as exc:  # noqa: BLE001
                log.info("paper mm settle: %s", exc)
            if self.field is not None:
                try:
                    self.field.settle()
                    self.field.save()
                except Exception as exc:  # noqa: BLE001
                    log.info("paper field settle: %s", exc)
        mids = {}
        dt = now - self.st["last"] if self.st["last"] else 0
        self.st["last"] = now
        for tk, info in self.st["markets"].items():
            try:
                book = parse_orderbook(self.lab.get(f"/markets/{tk}/orderbook")) or {}
                trades = self.lab.get("/markets/trades", {"ticker": tk, "limit": 100,
                                                          "min_ts": int(now - 600)}).get("trades") or []
            except Exception as exc:  # noqa: BLE001
                log.info("paper mm %s: %s", tk, exc)
                continue
            m = self.mid(book)
            mids[tk] = m
            self.fills_from(tk, trades, now, m)
            if dt and dt < 600:
                self.st["hours"][info["cat"]] = round(self.st["hours"].get(info["cat"], 0.0) + dt / 3600, 4)
            # re-quote: 1c better than the best bid on each side, if the spread is still wide
            y, n = book.get("yes") or [], book.get("no") or []
            if not y or not n:
                continue
            yb, nb = max(p for p, _ in y), max(p for p, _ in n)
            spread = round(1 - yb - nb, 4)
            inv = self.st["inv"].get(tk, 0)
            for side, best in (("yes", yb), ("no", nb)):
                key = f"{tk}|{side}"
                ok = spread >= MM["min_spread"] - 1e-9 and \
                    (inv < MM["max_inv"] if side == "yes" else inv > -MM["max_inv"])
                if ok:
                    px = round(best + 0.01, 2)
                    o = self.st["orders"].get(key)
                    if not o or o["px"] != px:
                        self.st["orders"][key] = {"px": px, "at": now, "spread": spread}
                else:
                    self.st["orders"].pop(key, None)
        self.markouts(mids, now)
        self.save()

    def status(self) -> dict:
        out = {}
        for cat, s in self.st["stats"].items():
            h = self.st["hours"].get(cat, 0.0)
            out[cat] = {"fills": s["fills"], "market_hours": round(h, 1),
                        "fills_per_market_hour": round(s["fills"] / h, 2) if h else None,
                        "edge_at_fill_c": round(s["edge0"] / s["fills"], 2) if s["fills"] else None,
                        "markout_5m_c": round(s["mk5"] / s["n5"], 2) if s["n5"] else None,
                        "markout_30m_c": round(s["mk30"] / s["n30"], 2) if s["n30"] else None,
                        "maker_fee_c": round(s["fee"] / s["fills"], 2) if s["fills"] else None}
        flow = {}
        for k, s in sorted(self.st["flow_stats"].items()):
            flow[k] = {"fills": s["fills"], "settled": s["settled"],
                       "markout_30m_c": round(s["mk30"] / s["n30"], 2) if s["n30"] else None,
                       "settled_c": round(s["settle"] / s["settled"], 2) if s["settled"] else None,
                       "maker_fee_c": round(s["fee"] / s["fills"], 2) if s["fills"] else None}
        for tk, info in self.st["markets"].items():
            info["casual"] = self.casual_share(tk)
        return {"markets": self.st["markets"], "by_category": out, "by_flow": flow,
                "last_scan": self.st.get("select_info"),
                "pending": len(self.st["pending"]), "held": len(self.st["held"]), "inventory": self.st["inv"]}


class RfqPaper:
    def __init__(self, lab: Lab, client):
        self.lab, self.client = lab, client
        p = lab.out / "rfq_state.json"
        try:
            self.st = json.loads(p.read_text())
        except (OSError, ValueError):
            self.st = {}
        for k, v in (("rfqs", {}), ("checked_at", 0.0), ("errors", 0), ("last_error", None), ("polls", 0)):
            self.st.setdefault(k, v)

    def save(self):
        (self.lab.out / "rfq_state.json").write_text(json.dumps(self.st))

    @staticmethod
    def legs(rfq: dict) -> list[dict]:
        out = []
        for lg in rfq.get("mve_selected_legs") or []:
            tk = lg.get("market_ticker") or lg.get("ticker")
            side = str(lg.get("side") or "yes").lower()
            if tk:
                out.append({"tk": tk, "ev": lg.get("event_ticker") or tk.rsplit("-", 1)[0], "side": side})
        return out

    def price_legs(self, legs: list[dict]) -> float | None:
        js = self.lab.get("/markets", {"tickers": ",".join(lg["tk"] for lg in legs), "limit": 100})
        mk = {m.get("ticker"): m for m in js.get("markets") or []}
        fair = 1.0
        for lg in legs:
            m = mk.get(lg["tk"])
            if not m:
                return None
            b, a = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
            if b is None or a is None or a <= 0:
                return None
            mid = (a + b) / 2
            fair *= mid if lg["side"] == "yes" else 1 - mid
        return fair

    def poll(self) -> None:
        now = self.lab.clock()
        self.st["polls"] += 1
        got, cursor = [], None
        for _ in range(RFQ["pages"]):
            params = {"status": "open", "limit": 100, **({"cursor": cursor} if cursor else {})}
            try:
                js = self.lab.get("/communications/rfqs", params, signed=self.client)
            except Exception as exc:  # noqa: BLE001
                self.st["errors"] += 1
                self.st["last_error"] = str(exc)[:300]
                break
            got += js.get("rfqs") or []
            cursor = js.get("cursor")
            if not cursor or len(js.get("rfqs") or []) < 100:
                break
        for r in got:
            rid = r.get("id") or r.get("rfq_id")
            if not rid or rid in self.st["rfqs"]:
                continue
            legs = self.legs(r)
            row = {"id": rid, "t": now, "ticker": r.get("market_ticker"), "n_legs": len(legs),
                   "contracts": _f(r.get("contracts_fp"), _f(r.get("contracts"))),
                   # requests can name a dollar amount instead of contracts (contracts_fp then reads 0)
                   "target_cost": _f(r.get("target_cost_dollars")),
                   "created": r.get("created_ts") or r.get("created_time"),
                   "cross": len(legs) >= 2 and len({lg["ev"] for lg in legs}) == len(legs)}
            if row["cross"]:
                try:
                    fair = self.price_legs(legs)
                except Exception as exc:  # noqa: BLE001
                    fair = None
                    row["price_error"] = str(exc)[:200]
                if fair is not None:
                    row["fair"] = round(fair, 4)
                    row["sports_legs"] = self.sports_legs(legs)
                    row["quotes"] = {str(m): round(min(0.99, fair * (1 + m) + 0.01), 2) for m in RFQ["margins"]}
            self.st["rfqs"][rid] = row
            self.lab.write("rfq", dict(row, kind="rfq", legs=legs))

    def series_category(self, series: str) -> str:
        cache = self.st.setdefault("series_cat", {})
        if series not in cache:
            try:
                cache[series] = str((self.lab.get(f"/series/{series}").get("series") or {}).get("category") or "?")
            except Exception:  # noqa: BLE001
                return "?"
        return cache[series]

    def sports_legs(self, legs: list[dict]) -> int:
        """How many legs are sports (by series category, cached; one call per new series)."""
        return sum(1 for lg in legs if self.series_category(lg["ev"].split("-")[0]) == "Sports")

    def check(self) -> None:
        """Did the combo trade after the request, at what price, and how did it settle?"""
        now = self.lab.clock()
        if now - self.st["checked_at"] < RFQ["check_s"]:
            return
        self.st["checked_at"] = now
        for rid, row in list(self.st["rfqs"].items()):
            if now - row["t"] > RFQ["keep_days"] * 86400:
                self.st["rfqs"].pop(rid)
                continue
            if not row.get("fair") or row.get("result") or not row.get("ticker"):
                continue
            try:
                if "traded" not in row and now - row["t"] > 120:
                    tr = self.lab.get("/markets/trades", {"ticker": row["ticker"], "limit": 100,
                                                          "min_ts": int(row["t"] - 60)}).get("trades") or []
                    yes = [_f(t.get("yes_price_dollars")) for t in tr if t.get("taker_side") == "yes"]
                    row["traded"] = [round(p, 4) for p in yes if p is not None][:10]
                m = self.lab.get(f"/markets/{row['ticker']}").get("market") or {}
                res = str(m.get("result") or "").lower()
                if res in ("yes", "no"):
                    row["result"] = res
                    st = _ts(m.get("settlement_ts") or m.get("close_time"))
                    row["settle_h"] = round((st - row["t"]) / 3600, 2) if st else None
                    win = 1.0 if res == "yes" else 0.0
                    # paper P&L per contract if we had sold YES at each quote
                    row["pnl_c"] = {k: round((q - win) * 100, 1) for k, q in row["quotes"].items()}
                    self.lab.write("rfq", dict(row, kind="settled"))
            except Exception as exc:  # noqa: BLE001
                log.info("rfq check %s: %s", rid, exc)

    def status(self) -> dict:
        rows = list(self.st["rfqs"].values())
        cross = [r for r in rows if r.get("fair")]
        done = [r for r in cross if r.get("result")]
        out = {"polls": self.st["polls"], "errors": self.st["errors"], "last_error": self.st["last_error"],
               "rfqs_seen": len(rows), "cross_priced": len(cross), "settled": len(done), "by_margin": {}}
        for m in map(str, RFQ["margins"]):
            # rows from before a margin was added have no quote for it
            cross_m = [r for r in cross if m in r["quotes"]]
            done_m = [r for r in done if m in r["quotes"] and m in (r.get("pnl_c") or {})]
            won = [r for r in cross_m if r.get("traded") and any(p > r["quotes"][m] for p in r["traded"])]
            pn = [r["pnl_c"][m] for r in done_m]
            # what we would really have traded: only requests a taker filled above our quote
            ww = [r for r in done_m if r.get("traded") and any(p > r["quotes"][m] for p in r["traded"])]
            wpn = [r["pnl_c"][m] for r in ww]
            dol = [capped_size(r, r["quotes"][m]) * r["pnl_c"][m] / 100 for r in ww]
            out["by_margin"][m] = {"would_win": len(won), "settled": len(pn),
                                   "pnl_c_per_ct": round(sum(pn) / len(pn), 2) if pn else None,
                                   "won_settled": len(wpn),
                                   "won_pnl_c_per_ct": round(sum(wpn) / len(wpn), 2) if wpn else None,
                                   "won_wins": sum(1 for x in wpn if x > 0),
                                   "capped_pnl_dollars": round(sum(dol), 2),
                                   "capped_worst_dollars": round(min(dol), 2) if dol else None}
        # by fair price band (buyers overpay most at 10-40c, Nov 11 settled-parlay study)
        out["by_band"] = {}
        for lo, hi in ((0, .02), (.02, .05), (.05, .10), (.10, .20), (.20, .40), (.40, 1.01)):
            band = [r for r in done if lo <= r["fair"] < hi]
            row = {"settled": len(band)}
            for m in map(str, RFQ["margins"]):
                ww = [r for r in band if m in (r.get("pnl_c") or {}) and r.get("traded")
                      and any(p > r["quotes"][m] for p in r["traded"])]
                row[m] = {"won": len(ww), "pnl_c_per_ct": round(sum(r["pnl_c"][m] for r in ww) / len(ww), 2)
                          if ww else None}
            out["by_band"][f"{round(lo * 100)}-{round(min(hi, 1) * 100)}c"] = row
        # niches the 9-week history flagged (Nov 12); judged ONLY on requests we would have won
        niches = {"non_sports_cross": lambda r: r.get("sports_legs") == 0,
                  "settle_under_1h": lambda r: r.get("settle_h") is not None and r["settle_h"] < 1,
                  "all_sports_cross": lambda r: r.get("sports_legs") is not None and r["sports_legs"] == r["n_legs"]}
        out["niches"] = {}
        for name, fn in niches.items():
            sub = [r for r in done if fn(r)]
            row = {"settled": len(sub)}
            for m in map(str, RFQ["margins"]):
                ww = [r for r in sub if m in (r.get("pnl_c") or {}) and r.get("traded")
                      and any(p > r["quotes"][m] for p in r["traded"])]
                row[m] = {"won": len(ww), "pnl_c_per_ct": round(sum(r["pnl_c"][m] for r in ww) / len(ww), 2)
                          if ww else None,
                          "capped_dollars": round(sum(capped_size(r, r["quotes"][m]) * r["pnl_c"][m] / 100
                                                      for r in ww), 2)}
            out["niches"][name] = row
        out["sized"] = sum(1 for r in cross if (r.get("contracts") or 0) > 0 or r.get("target_cost"))
        out["caps"] = {"cap_ct": RFQ["cap_ct"], "max_loss": RFQ["max_loss"]}
        # compact rows for offline grading (settled first, newest last)
        keep = ("id", "t", "n_legs", "contracts", "target_cost", "fair", "quotes", "traded", "result",
                "sports_legs", "settle_h")
        out["settled_rows"] = [{k: r.get(k) for k in keep} for r in sorted(done, key=lambda r: r["t"])][-1500:]
        out["recent"] = sorted(rows, key=lambda r: -r["t"])[:20]
        return out


class ReadOnly:
    """Wraps the signed client so only GET requests can ever be made."""

    def __init__(self, client):
        self._c = client

    def _get(self, path, params=None):
        return self._c._get(path, params)


def main() -> int:
    from .m15 import client_from_env, github_uploader
    from .util import Http

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out = Path(os.environ.get("PAPERLAB_DIR", "/var/lib/kalshi-paper"))
    out.mkdir(parents=True, exist_ok=True)
    for k in ("OANDA_TOKEN", "ANTHROPIC_API_KEY", "NTFY_TOPIC"):
        os.environ.pop(k, None)
    http = Http("kalshi-paperlab/1", min_interval=0.2, retries=3)
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    client = client_from_env(http)
    lab = Lab(out, http)
    field = FieldPaper(lab)
    mm = MakerPaper(lab, field)
    rfq = RfqPaper(lab, ReadOnly(client)) if client is not None else None
    up = github_uploader(Http("kalshi-paperlab/1"), token) if token else None
    last_mm = last_up = 0.0
    while True:
        now = time.time()
        if now - last_mm >= MM["cycle_s"]:
            last_mm = now
            try:
                mm.cycle()
            except Exception as exc:  # noqa: BLE001
                log.warning("paper mm: %s", exc)
        if rfq is not None:
            try:
                rfq.poll()
                rfq.check()
                rfq.save()
            except Exception as exc:  # noqa: BLE001
                log.warning("paper rfq: %s", exc)
        if up and now - last_up > 3600:
            last_up = now
            at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            try:
                up({"paper-mm.json": json.dumps(dict(mm.status(), at=at), indent=1),
                    "paper-field.json": json.dumps(dict(field.status(), at=at), indent=1),
                    "paper-rfq.json": json.dumps(dict(rfq.status() if rfq else {"error": "no key"}, at=at),
                                                 indent=1)})
            except Exception as exc:  # noqa: BLE001
                log.warning("paper upload: %s", exc)
        time.sleep(RFQ["cycle_s"])
