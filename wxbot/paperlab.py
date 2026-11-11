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

Status files go to the m15-data branch: m15/paper-mm.json and m15/paper-rfq.json (hourly).
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
      "mid_hi": 0.90, "cycle_s": 60, "select_s": 6 * 3600, "max_inv": 10, "event_pages": 400}
RFQ = {"cycle_s": 20, "margins": (0.0, 0.05, 0.10, 0.20), "check_s": 600, "keep_days": 4}


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


class MakerPaper:
    def __init__(self, lab: Lab):
        self.lab = lab
        p = lab.out / "mm_state.json"
        try:
            self.st = json.loads(p.read_text())
        except (OSError, ValueError):
            self.st = {}
        for k, v in (("markets", {}), ("orders", {}), ("pending", []), ("inv", {}), ("seen", {}),
                     ("selected_at", 0.0), ("stats", {}), ("hours", {}), ("last", 0.0)):
            self.st.setdefault(k, v)

    def save(self):
        (self.lab.out / "mm_state.json").write_text(json.dumps(self.st))

    def select(self) -> None:
        now = self.lab.clock()
        cands, cur = [], None
        for _ in range(MM["event_pages"]):
            p = {"status": "open", "with_nested_markets": "true", "limit": 200}
            if cur:
                p["cursor"] = cur
            try:
                js = self.lab.get("/events", p)
            except Exception as exc:  # noqa: BLE001
                log.info("paper mm events: %s", exc)
                break
            for e in js.get("events") or []:
                cat = e.get("category") or ""
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
                break
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
        self.st["selected_at"] = now
        self.lab.write("mm", {"t": round(now), "kind": "select", "candidates": len(cands),
                              "chosen": {k: v["cat"] for k, v in chosen.items()}})

    @staticmethod
    def mid(book: dict) -> float | None:
        y, n = book.get("yes") or [], book.get("no") or []
        if not y or not n:
            return None
        return (max(p for p, _ in y) + 1 - max(p for p, _ in n)) / 2

    def fills_from(self, tk: str, trades: list, now: float, mid: float | None) -> None:
        seen = self.st["seen"].setdefault(tk, [])
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
                     "fee": round(maker_fee(o["px"]) * 100, 2)}
                self.st["pending"].append(f)
                self.lab.write("mm", dict(f, kind="fill"))
        self.st["seen"][tk] = seen[-300:]

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
                self.lab.write("mm", dict(f, kind="markout"))
            else:
                keep.append(f)
        self.st["pending"] = keep

    def cycle(self) -> None:
        now = self.lab.clock()
        if now - self.st["selected_at"] > MM["select_s"] or not self.st["markets"]:
            self.select()
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
        return {"markets": self.st["markets"], "by_category": out, "pending": len(self.st["pending"]),
                "inventory": self.st["inv"]}


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
        try:
            js = self.lab.get("/communications/rfqs", {"status": "open", "limit": 100}, signed=self.client)
        except Exception as exc:  # noqa: BLE001
            self.st["errors"] += 1
            self.st["last_error"] = str(exc)[:300]
            return
        for r in js.get("rfqs") or []:
            rid = r.get("id") or r.get("rfq_id")
            if not rid or rid in self.st["rfqs"]:
                continue
            legs = self.legs(r)
            row = {"id": rid, "t": now, "ticker": r.get("market_ticker"), "n_legs": len(legs),
                   "contracts": _f(r.get("contracts_fp"), _f(r.get("contracts"))),
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
                    row["quotes"] = {str(m): round(min(0.99, fair * (1 + m) + 0.01), 2) for m in RFQ["margins"]}
            self.st["rfqs"][rid] = row
            self.lab.write("rfq", dict(row, kind="rfq", legs=legs))

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
            won = [r for r in cross if r.get("traded") and any(p > r["quotes"][m] for p in r["traded"])]
            pn = [r["pnl_c"][m] for r in done]
            out["by_margin"][m] = {"would_win": len(won), "settled": len(pn),
                                   "pnl_c_per_ct": round(sum(pn) / len(pn), 2) if pn else None}
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
    mm = MakerPaper(lab)
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
                    "paper-rfq.json": json.dumps(dict(rfq.status() if rfq else {"error": "no key"}, at=at),
                                                 indent=1)})
            except Exception as exc:  # noqa: BLE001
                log.warning("paper upload: %s", exc)
        time.sleep(RFQ["cycle_s"])
