"""Liquidity-reward test (Alex OK'd Nov 11, 2026 8:23 AM ET: "$20 for 3 days").

Kalshi pays daily rewards to resting orders in ~5,000 markets (incentive_programs, type
"liquidity", until Jan 1, 2027). This bot rests cheap bids (1-8c, the longshot side) in a handful
of quiet, long-dated reward markets and records what Kalshi pays us (GET
/portfolio/credit_history), so 2-3 days of real payouts answer the research question.
Research: /mnt/project-files/analysis/overnight-ideas/RESULTS.md (idea #1).

Rails, all checked every cycle:
  - lip_control.json on main says mode "live", the global kill switch (data/trader_state.json:
    TRADING_MODE not off/paper, not halted), both read within the last 30 minutes, and a Kalshi
    key present. Anything else: every one of our orders is cancelled and nothing new is placed.
  - money tied up (resting bids + anything filled) <= budget ($20), and never more than the cash
    on exchange shard 0 minus `reserve` (the main bot's room);
  - bids only at 1c..max_px (8c), post-only (never takes), only on the cheap side, sports excluded;
    order cost <= $6 each (kalshi_api.LIP_MAX_*);
  - fill stop: once filled contracts cost `fill_stop` ($10) in total, everything is cancelled and
    the bot halts (we came to be paid for resting, not to collect longshots).
  - fill recovery (Alex, Nov 13): contracts a bid bought by accident are offered straight back
    at the price we paid (reduce-only, post-only sell; sold at once if a bid already sits at that
    price). Break-even if someone takes them, otherwise they are held to settlement as before.
    These exit offers stay up through the fill stop and the end of the test window, and are
    cancelled only by the kill switches (they can only shrink a position, never add risk).
  - about one Kalshi call per second at most (the key is shared with the live bots).
Client order ids start "lip-" (the tax ledger tags these fills "rewards bot").
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .kalshi import BASE, parse_orderbook
from .kalshi_api import fill_qty
from . import lip_rank

log = logging.getLogger("wxbot")
REPO = "alexanderhabel22-commits/kalshi-weather-bot"
CONTROL_URL = f"https://raw.githubusercontent.com/{REPO}/main/lip_control.json"
SWITCH_URL = f"https://raw.githubusercontent.com/{REPO}/main/data/trader_state.json"
DEFAULTS = {"mode": "off", "budget": 20.0, "reserve": 6.0, "markets": 8, "min_px": 0.01,
            "max_px": 0.08, "max_ct": 300, "min_days_to_close": 21, "min_days_left": 1.0,
            "fill_stop": 10.0, "requote_s": 1800, "max_requotes_day": 6, "cycle_s": 300,
            "select_s": 21600, "upload_s": 900, "min_est_day": 0.03, "candidates": 60,
            "exclude_categories": ["Sports"], "exclude_series": [], "only": [], "until": None,
            "price_mode": "ref", "ref_requote_s": 600, "reselect": None,
            "max_vol24": 2000.0, "move_guard": 0.03, "move_pause_s": 6 * 3600,
            # daily risk-adjusted re-pick, SHADOW only (logs; moves no orders): wxbot/lip_rank.py
            "shadow": True, "shadow_hour_utc": 10, "shadow_programs": 400, "shadow_books": 80,
            "shadow_max_vol24": 20000.0, "adverse": 0.6,
            "recover": True}
CONTROL_STALE_S = 1800
PACE_S = 1.0                     # seconds between Kalshi calls


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def side_stats(levels: list, target: float, disc: float, mine: float = 0.0, my_px: float | None = None):
    """Kalshi's scoring for one side (as documented): walk bids from the best down until the
    cumulative size reaches `target`; the reference price is where it first reaches target/5;
    size at/above it counts fully, lower levels discount^ticks. Returns (others' qualifying score,
    best price, side reaches target, reference price). `mine` (at my_px) is removed first."""
    lv = {}
    for p, q in levels:
        lv[round(p, 4)] = lv.get(round(p, 4), 0.0) + q
    if mine and my_px is not None and round(my_px, 4) in lv:
        lv[round(my_px, 4)] = max(0.0, lv[round(my_px, 4)] - mine)
    lv = sorted(((p, q) for p, q in lv.items() if q > 0), reverse=True)
    if not lv:
        return 0.0, None, False, None
    cum, ref = 0.0, None
    for p, q in lv:
        cum += q
        if ref is None and cum >= target / 5:
            ref = p
    reaches = cum + mine >= target
    ref = ref if ref is not None else lv[-1][0]
    score, cum = 0.0, 0.0
    for p, q in lv:
        if cum >= target:
            break
        take = min(q, target - cum)
        score += take * disc ** max(0, round((ref - p) * 100))
        cum += take
    return score, lv[0][0], reaches, ref


def plan_market(book: dict, prog: dict, s: dict, per_mkt: float) -> dict | None:
    """Where we'd bid in one reward market: the cheap side, within min_px..max_px, not crossing
    the other side. None if it doesn't qualify.
    price_mode "ref" (default since Nov 11, Alex's question): AT the reference price, the lowest
    price that still earns full credit (Kalshi scores pro-rata by size, so being first in line
    earns nothing extra). Cheaper per contract = more contracts per dollar = a bigger share, and
    other bids sit ahead of ours when someone sells. "front": 1c above the best bid."""
    tgt = _f(prog.get("target_size_fp"), 1000.0) or 1000.0
    disc = (_f(prog.get("discount_factor_bps"), 5000.0) or 5000.0) / 10000
    st = {sd: side_stats(book.get(sd) or [], tgt, disc) for sd in ("yes", "no")}
    if not (st["yes"][2] or st["no"][2]):
        return None
    sides = sorted(("yes", "no"), key=lambda sd: st[sd][1] if st[sd][1] is not None else 0.0)
    side = sides[0]
    other = "no" if side == "yes" else "yes"
    best = st[side][1] or 0.0
    if s.get("price_mode", "ref") == "ref":
        ref = st[side][3] if st[side][3] is not None else best
        px = round(max(s["min_px"], math.ceil(ref * 100 - 1e-6) / 100), 2)
    else:
        px = round(max(s["min_px"], math.floor(best * 100 + 1e-6) / 100 + 0.01), 2)
    other_best = st[other][1] or 0.0
    if px > s["max_px"] + 1e-9 or px + other_best > 0.99 + 1e-9:
        return None
    n = int(min(s["max_ct"], math.floor(per_mkt / px + 1e-9)))
    if n < 10:
        return None
    pool = (_f(prog.get("period_reward"), 0.0) or 0.0) / 10000      # centi-cents -> $
    days = max(1.0, ((_ts(prog.get("end_date")) or 0) - (_ts(prog.get("start_date")) or 0)) / 86400)
    others = st[side][0]
    share = n / (others + n)
    # both sides must reach target for a snapshot to count (our size helps our side)
    both = st[other][2] and (st[side][2] or others + n >= tgt)
    est_day = share * pool / 2 / days if both else 0.0
    mid = round((st["yes"][1] + 1 - st["no"][1]) / 2, 4) if st["yes"][1] is not None and st["no"][1] is not None else None
    return {"side": side, "px": px, "n": n, "cost": round(px * n, 2), "share": round(share, 4), "mid": mid,
            "pool": pool, "days": round(days, 2), "est_day": round(est_day, 4),
            "per_dollar": est_day / (px * n), "ref": st[side][3], "best": best}


class LipBot:
    def __init__(self, out_dir: Path, client, http, fetch_json: Callable[[str], dict] | None = None,
                 clock=time.time, sleep=time.sleep, alert: Callable[[str, str], None] | None = None,
                 upload: Callable[[dict], None] | None = None):
        self.out, self.client, self.http = out_dir, client, http
        self.fetch_json = fetch_json or (lambda u: http.get_json(u))
        self.clock, self.sleep = clock, sleep
        self.alert = alert or (lambda t, b: None)
        self.upload = upload
        self.cfg = dict(DEFAULTS)
        self.switch = {"mode": None, "halted": False}
        self.ok_at = 0.0
        self._last_call = 0.0
        try:
            self.state = json.loads((out_dir / "state.json").read_text())
        except (OSError, ValueError):
            self.state = {}
        for k, v in (("orders", {}), ("positions", {}), ("chosen", {}), ("credits", {}),
                     ("bad", {}), ("requotes", {}), ("est", {}), ("halted", None),
                     ("selected_at", 0.0), ("uploaded_at", 0.0), ("events", []), ("shadow", {}), ("exits", {})):
            self.state.setdefault(k, v)

    # -- plumbing
    def pace(self) -> None:
        wait = PACE_S - (self.clock() - self._last_call)
        if wait > 0:
            self.sleep(wait)
        self._last_call = self.clock()

    def save(self) -> None:
        (self.out / "state.json").write_text(json.dumps(self.state, indent=1))

    def event(self, kind: str, **kw) -> None:
        row = {"t": round(self.clock()), "kind": kind, **kw}
        log.info("lip %s", row)
        self.state["events"] = (self.state["events"] + [row])[-400:]
        try:
            with open(self.out / f"log-{time.strftime('%Y-%m-%d', time.gmtime(self.clock()))}.jsonl", "a") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            pass

    def refresh(self) -> None:
        now = self.clock()
        try:
            c = self.fetch_json(CONTROL_URL) or {}
            self.cfg = {**DEFAULTS, **{k: v for k, v in c.items() if k in DEFAULTS}}
            self.cfg["mode"] = str(self.cfg.get("mode") or "off").lower()
            sw = self.fetch_json(SWITCH_URL) or {}
            halted = sw.get("halted") or {}
            self.switch = {"mode": (sw.get("mode") or "").lower() or None,
                           "halted": bool(isinstance(halted, dict) and halted.get("live"))}
            self.ok_at = now
        except Exception as exc:  # noqa: BLE001
            log.warning("lip control read: %s", exc)

    def killed(self) -> bool:
        """An explicit kill (control off, global switch off/paper/halted, stale switches): cancel
        everything, exit offers included. The fill stop and the test end are not kills."""
        if self.cfg.get("mode") != "live" or self.client is None:
            return True
        if self.switch["mode"] in ("off", "paper") or self.switch["halted"]:
            return True
        return self.clock() - self.ok_at > CONTROL_STALE_S

    def mode(self) -> str:
        if self.cfg.get("mode") != "live" or self.client is None:
            return "off"
        if self.switch["mode"] in ("off", "paper") or self.switch["halted"] or self.state.get("halted"):
            return "off"
        if self.clock() - self.ok_at > CONTROL_STALE_S:
            return "off"
        if self.cfg.get("until") and self.clock() > (_ts(self.cfg["until"]) or 0):
            return "off"                    # the test window is over
        return "live"

    # -- money
    def resting_cost(self) -> float:
        return sum(o["px"] * max(0.0, o["n"] - o.get("filled", 0.0)) for o in self.state["orders"].values())

    def filled_cost(self) -> float:
        return sum(p["cost"] for p in self.state["positions"].values())

    def filled_total(self) -> float:
        """Everything our bids ever bought, including fills since sold back (for the fill stop)."""
        return sum(p["cost"] + p.get("recovered", 0.0) for p in self.state["positions"].values())

    def room(self, cash0: float | None) -> float:
        """Dollars we may still put into new bids: under the budget, and leaving `reserve` of
        free cash on shard 0 (cash there already excludes our resting bids)."""
        s = self.cfg
        cap = s["budget"] - self.filled_cost() - self.resting_cost()
        if cash0 is not None:
            cap = min(cap, cash0 - s["reserve"])
        return max(0.0, cap)

    # -- Kalshi reads
    def programs(self) -> list[dict]:
        out, cur = [], None
        for _ in range(20):
            p = {"status": "active", "type": "liquidity", "limit": 10000}
            if cur:
                p["cursor"] = cur
            self.pace()
            js = self.http.get_json(f"{BASE}/incentive_programs", params=p)
            out += js.get("incentive_programs") or []
            cur = js.get("next_cursor")
            if not cur:
                break
        return out

    def markets(self, tickers: list[str]) -> dict:
        out = {}
        for i in range(0, len(tickers), 100):
            self.pace()
            js = self.http.get_json(f"{BASE}/markets", params={"tickers": ",".join(tickers[i:i + 100]),
                                                               "limit": 100})
            for m in js.get("markets") or []:
                if m.get("ticker") in tickers:
                    out[m["ticker"]] = m
        if tickers and not out:          # batch filter not honoured: one market at a time
            for tk in tickers[:60]:
                self.pace()
                try:
                    out[tk] = self.http.get_json(f"{BASE}/markets/{tk}").get("market") or {}
                except Exception:  # noqa: BLE001
                    continue
        return out

    def category(self, event_ticker: str) -> str:
        cats = self.state.setdefault("cats", {})
        if event_ticker not in cats:
            self.pace()
            try:
                js = self.http.get_json(f"{BASE}/events/{event_ticker}")
                cats[event_ticker] = (js.get("event") or {}).get("category") or "unknown"
            except Exception:  # noqa: BLE001
                return "unknown"
        return cats[event_ticker]

    def book(self, ticker: str) -> dict | None:
        self.pace()
        try:
            return parse_orderbook(self.http.get_json(f"{BASE}/markets/{ticker}/orderbook"))
        except Exception as exc:  # noqa: BLE001
            log.info("book %s: %s", ticker, exc)
            return None

    # -- choosing markets
    def select(self) -> None:
        s, now = self.cfg, self.clock()
        progs = {}
        for r in self.programs():
            tk = r.get("market_ticker")
            end, start = _ts(r.get("end_date")), _ts(r.get("start_date"))
            if not tk or not end or not start or start > now or end - now < s["min_days_left"] * 86400:
                continue
            if s["only"] and tk not in s["only"]:
                continue
            if any(tk.startswith(x) for x in s["exclude_series"]) or tk.split("-")[0].endswith("15M"):
                continue
            if self.state["bad"].get(tk, 0) > now:
                continue
            progs[tk] = r
        # rough pre-rank by pool per day, then check the details of the best ones
        rank = sorted(progs, key=lambda t: -(_f(progs[t].get("period_reward"), 0) or 0) /
                      max(1.0, ((_ts(progs[t]["end_date"]) or 0) - (_ts(progs[t]["start_date"]) or 0)) / 86400))
        mk = self.markets(rank[:s["candidates"] * 3])
        per_mkt = s["budget"] / max(1, s["markets"])
        plans = []
        for tk in rank[:s["candidates"] * 3]:
            m = mk.get(tk)
            if not m or m.get("status") not in ("active", "open"):
                continue
            close = _ts(m.get("close_time")) or 0
            if close - now < s["min_days_to_close"] * 86400:
                continue
            if int(_f(m.get("exchange_index"), 0) or 0) != 0:
                continue
            # busy markets fill our bids more often: keep to quiet ones
            if (_f(m.get("volume_24h_fp"), 0.0) or 0.0) > s["max_vol24"]:
                continue
            if len(plans) >= s["candidates"]:
                break
            if self.category(m.get("event_ticker") or "") in s["exclude_categories"] + ["unknown"]:
                continue
            b = self.book(tk)
            pl = plan_market(b, progs[tk], s, per_mkt) if b else None
            if pl and pl["est_day"] >= s["min_est_day"]:
                plans.append(dict(pl, ticker=tk, close=m.get("close_time"), end=progs[tk]["end_date"]))
        plans.sort(key=lambda p: -p["per_dollar"])
        keep = {t: c for t, c in self.state["chosen"].items() if t in progs}
        chosen = {p["ticker"]: p for p in plans[:s["markets"]]}
        # markets we already rest in stay unless they dropped out of the program
        for t in list(keep):
            if t not in chosen and len(chosen) < s["markets"] + 2 and t in self.state["orders"]:
                chosen[t] = keep[t]
        self.state["chosen"] = chosen
        self.state["selected_at"] = now
        self.event("select", programs=len(progs), checked=len(plans), chosen=sorted(chosen),
                   est_day=round(sum(p["est_day"] for p in chosen.values()), 2))

    # -- daily shadow re-pick (Nov 13; logs only, see wxbot/lip_rank.py)
    def trades(self, tk: str, since: float) -> list:
        out, cur = [], None
        for _ in range(3):
            p = {"ticker": tk, "limit": 1000, "min_ts": int(since)}
            if cur:
                p["cursor"] = cur
            self.pace()
            js = self.http.get_json(f"{BASE}/markets/trades", params=p)
            out += js.get("trades") or []
            cur = js.get("cursor")
            if not cur:
                break
        return out

    def tripped(self, tk: str, now: float) -> bool:
        return any(e.get("ticker") == tk and e.get("kind") == "cancel" and str(e.get("why", "")).startswith("price moved")
                   and now - e.get("t", 0) < 3 * 86400 for e in self.state["events"])

    def shadow_due(self) -> bool:
        if not self.cfg.get("shadow"):
            return False
        now = self.clock()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        last = self.state["shadow"].get("day")
        if now - self.state.get("shadow_try", 0) < 3600:
            return False                 # a failed run (e.g. rate limit) retries an hour later
        return last != day and (last is None or time.gmtime(now).tm_hour >= self.cfg["shadow_hour_utc"])

    def shadow(self) -> None:
        """Rank reward markets by expected reward minus expected fill loss and log the set we WOULD
        hold next to the one we hold. Public reads only; never places or cancels anything."""
        s, now = self.cfg, self.clock()
        since = now - lip_rank.FLOW_DAYS * 86400
        progs = {}
        for r in self.programs():
            tk = r.get("market_ticker")
            end, start = _ts(r.get("end_date")), _ts(r.get("start_date"))
            if not tk or not end or not start or start > now or end - now < s["min_days_left"] * 86400:
                continue
            if any(tk.startswith(x) for x in s["exclude_series"]) or tk.split("-")[0].endswith("15M"):
                continue
            progs[tk] = r

        def pool_day(t):
            r = progs[t]
            return (_f(r.get("period_reward"), 0) or 0) / max(1.0, ((_ts(r["end_date"]) or 0) - (_ts(r["start_date"]) or 0)) / 86400)
        current = set(self.state["chosen"])
        look = sorted(progs, key=lambda t: -pool_day(t))[:s["shadow_programs"]]
        look += [t for t in current if t in progs and t not in look]
        mk = self.markets(look)
        per_mkt = s["budget"] / max(1, s["markets"])
        rows, books = [], 0
        for tk in look:
            m = mk.get(tk)
            if not m or m.get("status") not in ("active", "open"):
                continue
            if (_ts(m.get("close_time")) or 0) - now < s["min_days_to_close"] * 86400:
                continue
            if int(_f(m.get("exchange_index"), 0) or 0) != 0:
                continue
            if (_f(m.get("volume_24h_fp"), 0.0) or 0.0) > s["shadow_max_vol24"]:
                continue
            if tk not in current and books >= s["shadow_books"]:
                continue
            if self.category(m.get("event_ticker") or "") in s["exclude_categories"] + ["unknown"]:
                continue
            b = self.book(tk)
            books += 1
            if not b:
                continue
            o = self.state["orders"].get(tk)
            if o:            # judge the book without our own resting order
                rest = o["n"] - o.get("filled", 0)
                b = {sd: [(p, q - (rest if sd == o["side"] and abs(p - o["px"]) < 1e-9 else 0))
                          for p, q in (b.get(sd) or [])] for sd in ("yes", "no")}
            pl = plan_market(b, progs[tk], s, per_mkt)
            if not pl or pl["est_day"] <= 0:
                continue
            try:
                flow = lip_rank.fill_flow(self.trades(tk, since), pl["side"], pl["px"], since,
                                          lambda t: _ts(t.get("created_time")))
            except Exception as exc:  # noqa: BLE001
                log.info("lip shadow trades %s: %s", tk, exc)
                flow = (_f(m.get("volume_24h_fp"), 0.0) or 0.0) / 2
            last, prev = _f(m.get("last_price_dollars")), _f(m.get("previous_price_dollars"))
            move = (last - prev) if last is not None and prev is not None and prev > 0 else None
            sc = lip_rank.score(pl, [(p, q) for p, q in (b.get(pl["side"]) or []) if q > 0], flow, move,
                                self.tripped(tk, now) or self.state["bad"].get(tk, 0) > now,
                                s["move_guard"], s["adverse"])
            rows.append({"ticker": tk, "event": m.get("event_ticker"), "side": pl["side"], "px": pl["px"],
                         "n": pl["n"], "share": pl["share"], "vol24": _f(m.get("volume_24h_fp"), 0.0),
                         "held": tk in current, **sc})
        k = s["markets"]
        picks = lip_rank.pick(rows, k, current)
        picks_cons = lip_rank.pick(rows, k, current, key="net_cons_day")
        cur_rows = [r for r in rows if r["held"]]

        def tot(rs, key):
            return round(sum(r[key] for r in rs), 2)
        summary = {"day": time.strftime("%Y-%m-%d", time.gmtime(now)), "at": round(now), "programs": len(progs),
                   "scored": len(rows), "current": sorted(current), "picks": [r["ticker"] for r in picks],
                   "picks_conservative": [r["ticker"] for r in picks_cons],
                   "add": sorted({r["ticker"] for r in picks} - current), "drop": sorted(current - {r["ticker"] for r in picks}),
                   "current_net_day": tot(cur_rows, "net_day"), "picks_net_day": tot(picks, "net_day"),
                   "current_net_cons_day": tot(cur_rows, "net_cons_day"), "picks_net_cons_day": tot(picks_cons, "net_cons_day"),
                   "current_fill_loss_day": tot(cur_rows, "fill_loss_day"), "picks_fill_loss_day": tot(picks, "fill_loss_day"),
                   "top": sorted(rows, key=lambda r: -r["net_day"])[:25], "held_rows": cur_rows}
        self.state["shadow"] = summary
        try:
            (self.out / f"shadow-{summary['day']}.json").write_text(json.dumps(summary, indent=1))
        except OSError:
            pass
        self.event("shadow", scored=len(rows), picks=summary["picks"], add=summary["add"], drop=summary["drop"],
                   current_net_day=summary["current_net_day"], picks_net_day=summary["picks_net_day"])

    # -- orders
    def cancel(self, tk: str, why: str) -> None:
        o = self.state["orders"].get(tk)
        if not o:
            return
        self.pace()
        try:
            self.client.cancel(o["id"], tk)
            self.pace()
            self.book_fill(tk, o, fill_qty(self.client.get_order(o["id"]), o["n"]))
        except Exception as exc:  # noqa: BLE001
            log.warning("lip cancel %s: %s", tk, exc)
            return
        self.state["orders"].pop(tk, None)
        self.event("cancel", ticker=tk, why=why)

    def cancel_all(self, why: str) -> None:
        for tk in list(self.state["orders"]):
            self.cancel(tk, why)

    def book_fill(self, tk: str, o: dict, got: float) -> None:
        new = round(got - o.get("filled", 0.0), 2)
        if new <= 0:
            return
        o["filled"] = got
        p = self.state["positions"].setdefault(tk, {"side": o["side"], "n": 0.0, "cost": 0.0})
        p["n"] = round(p["n"] + new, 2)
        p["cost"] = round(p["cost"] + new * o["px"], 4)
        self.event("fill", ticker=tk, side=o["side"], n=new, px=o["px"])
        self.alert("Rewards test: a bid filled",
                   f"{tk}: bought {new:g} {o['side'].upper()} at {round(o['px'] * 100)}c "
                   f"(offered back at our price; held to settlement if nobody takes it). Filled so far ${self.filled_total():.2f} of the "
                   f"${self.cfg['fill_stop']:.0f} fill stop.")

    def place(self, tk: str, pl: dict, room: float) -> float:
        n = int(min(pl["n"], math.floor(room / pl["px"] + 1e-9)))
        if n < 10:
            return 0.0
        cid = f"lip-{uuid.uuid4().hex[:16]}"
        self.pace()
        try:
            o = self.client.place_lip_bid(tk, pl["side"], pl["px"], n, cid)
        except Exception as exc:  # noqa: BLE001
            self.event("reject", ticker=tk, side=pl["side"], px=pl["px"], n=n, error=str(exc)[:200])
            self.state["bad"][tk] = self.clock() + 6 * 3600
            return 0.0
        oid = o.get("order_id") or o.get("id")
        self.state["orders"][tk] = {"id": oid, "cid": cid, "side": pl["side"], "px": pl["px"], "n": n,
                                    "filled": 0.0, "at": self.clock(), "mid0": pl.get("mid")}
        self.event("place", ticker=tk, side=pl["side"], px=pl["px"], n=n, est_day=pl.get("est_day"))
        return n * pl["px"]

    def sync(self) -> None:
        """Fills and vanished orders, from one listing of our resting orders; cancel strays."""
        self.pace()
        resting = self.client.resting_orders()
        mine = {o["id"] for o in self.state["orders"].values()} | {x["id"] for x in self.state["exits"].values()}
        for oid, ko in resting.items():
            if str(ko.get("client_order_id") or "").startswith("lip-") and oid not in mine:
                self.pace()
                try:
                    self.client.cancel(oid, ko.get("ticker", ""))
                    self.event("stray_cancel", ticker=ko.get("ticker"), id=oid)
                except Exception as exc:  # noqa: BLE001
                    log.warning("lip stray %s: %s", oid, exc)
        for tk, o in list(self.state["orders"].items()):
            ko = resting.get(o["id"])
            if ko is None:
                self.pace()
                try:
                    ko = self.client.get_order(o["id"])
                except Exception as exc:  # noqa: BLE001
                    log.warning("lip order %s: %s", o["id"], exc)
                    continue
                self.book_fill(tk, o, fill_qty(ko, o["n"]))
                self.state["orders"].pop(tk, None)
                self.event("gone", ticker=tk, status=ko.get("status"))
            else:
                self.book_fill(tk, o, fill_qty(ko, o["n"]))
        self.sync_exits(resting)

    def credits(self) -> None:
        """Kalshi credits (where reward payouts should show up), newest first; keep them all."""
        if not hasattr(self.client, "credit_history"):
            return
        self.pace()
        try:
            rows = self.client.credit_history()
        except Exception as exc:  # noqa: BLE001
            log.info("credit history: %s", exc)
            return
        for r in rows:
            cid = r.get("credit_id") or json.dumps(r, sort_keys=True)
            if cid not in self.state["credits"]:
                self.state["credits"][cid] = r
                self.event("credit", **{k: r.get(k) for k in ("type", "status", "amount_dollars",
                                                              "reason", "created_time")})

    def manage(self) -> None:
        s, now = self.cfg, self.clock()
        cash0 = None
        self.pace()
        try:
            cash0 = self.client.cash_on(0)
        except Exception as exc:  # noqa: BLE001
            log.warning("lip cash: %s", exc)
            return
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        rq = self.state["requotes"]
        # spread what we can actually use over the chosen markets (Kalshi holds the cost of
        # resting bids out of the cash balance, so cash0 + our resting = our usable pool)
        usable = min(s["budget"] - self.filled_cost(), cash0 + self.resting_cost() - s["reserve"])
        per_mkt = max(0.0, usable) / max(1, s["markets"])
        self.state["usable"] = round(usable, 2)
        for tk in list(self.state["orders"]):
            if tk not in self.state["chosen"]:
                self.cancel(tk, "no longer chosen")
        for tk, pl in self.state["chosen"].items():
            if tk in self.state["positions"]:
                continue        # after any fill in a market: no new or moved bids there
            if self.state["bad"].get(tk, 0) > now:
                continue                # paused (price jump / rejected)
            b = self.book(tk)
            if not b:
                continue
            o = self.state["orders"].get(tk)
            # news guard: the price moved a lot since we bid -> pull and sit out for a while
            yb = max((p for p, _ in b.get("yes") or []), default=None)
            nb = max((p for p, _ in b.get("no") or []), default=None)
            mid = (yb + 1 - nb) / 2 if yb is not None and nb is not None else None
            if o and mid is not None and o.get("mid0") is not None and abs(mid - o["mid0"]) >= s["move_guard"] - 1e-9:
                self.cancel(tk, f"price moved {round((mid - o['mid0']) * 100, 1)}c since we bid")
                self.state["bad"][tk] = now + s["move_pause_s"]
                continue
            prog = {"target_size_fp": "1000", "discount_factor_bps": 5000,
                    "period_reward": pl["pool"] * 10000, "start_date": None, "end_date": None}
            if o:
                lv = b.get(o["side"]) or []
                others = [(p, q) for p, q in lv if p > o["px"] + 1e-9]
                tgt = 1000.0
                sc, _, _, ref = side_stats(lv, tgt, 0.5, mine=o["n"] - o.get("filled", 0), my_px=o["px"])
                self.state["est"].setdefault(day, {})[tk] = {
                    "ahead": round(sum(q for _, q in others), 2), "others": round(sc, 1),
                    "share": round((o["n"] - o["filled"]) / (sc + o["n"] - o["filled"] + 1e-9), 4)}
                # where would we bid now, judged on the book WITHOUT our own order?
                rest = round(o["n"] - o.get("filled", 0), 2)
                bx = {sd: [(p, q - (rest if sd == o["side"] and abs(p - o["px"]) < 1e-9 else 0))
                           for p, q in (b.get(sd) or [])] for sd in ("yes", "no")}
                np_ = plan_market(bx, prog, s, max(per_mkt, o["px"] * o["n"]))
                moved = np_ and np_["side"] == o["side"] and abs(np_["px"] - o["px"]) > 1e-9
                below = moved and np_["px"] > o["px"]          # we'd lose credit: move up soon
                wait = s["ref_requote_s"] if (below or s.get("price_mode", "ref") == "ref") else s["requote_s"]
                if s.get("price_mode", "ref") != "ref":
                    moved = moved and bool(others) and below
                if moved and now - o["at"] > wait and rq.get(f"{day}|{tk}", 0) < s["max_requotes_day"]:
                    if True:
                        self.cancel(tk, f"{'up' if below else 'down'} to {round(np_['px'] * 100)}c (reference price)")
                        if tk not in self.state["orders"] and tk not in self.state["positions"]:
                            rq[f"{day}|{tk}"] = rq.get(f"{day}|{tk}", 0) + 1
                            self.place(tk, dict(np_, est_day=pl.get("est_day")), self.room(cash0 + o["px"] * o["n"]))
                continue
            np_ = plan_market(b, prog, s, per_mkt)
            if not np_:
                continue
            spent = self.place(tk, dict(np_, est_day=pl.get("est_day")), self.room(cash0))
            if spent and not self.state.get("cash_check"):
                # learn once whether Kalshi holds resting bids out of the cash balance
                self.pace()
                try:
                    after = self.client.cash_on(0)
                    self.state["cash_check"] = {"before": cash0, "after": after, "bid_cost": spent}
                    self.event("cash_check", before=cash0, after=after, bid_cost=spent)
                except Exception:  # noqa: BLE001
                    pass
            cash0 -= spent

    # -- fill recovery (Nov 13): offer accidental fills back at our cost
    def book_exit(self, tk: str, x: dict, sold: float) -> None:
        new = round(sold - x.get("sold", 0.0), 2)
        if new <= 0:
            return
        x["sold"] = sold
        p = self.state["positions"].get(tk)
        if p:
            avg = p["cost"] / p["n"] if p["n"] else x["px"]
            p["n"] = round(max(0.0, p["n"] - new), 2)
            p["cost"] = round(max(0.0, p["cost"] - new * avg), 4)
            p["recovered"] = round(p.get("recovered", 0.0) + new * x["px"], 4)
            p["recovered_n"] = round(p.get("recovered_n", 0.0) + new, 2)
        self.event("exit_fill", ticker=tk, side=x["side"], n=new, px=x["px"])
        self.alert("Rewards test: accidental fill sold back",
                   f"{tk}: sold {new:g} {x['side'].upper()} back at {round(x['px'] * 100)}c (what we paid).")

    def cancel_exit(self, tk: str, why: str) -> bool:
        x = self.state["exits"].get(tk)
        if not x:
            return True
        self.pace()
        try:
            self.client.cancel(x["id"], tk)
            self.pace()
            self.book_exit(tk, x, fill_qty(self.client.get_order(x["id"]), x["n"]))
        except Exception as exc:  # noqa: BLE001
            log.warning("lip exit cancel %s: %s", tk, exc)
            return False
        self.state["exits"].pop(tk, None)
        self.event("exit_cancel", ticker=tk, why=why)
        return True

    def sync_exits(self, resting: dict) -> None:
        for tk, x in list(self.state["exits"].items()):
            ko = resting.get(x["id"])
            if ko is not None:
                self.book_exit(tk, x, fill_qty(ko, x["n"]))
                continue
            self.pace()
            try:
                ko = self.client.get_order(x["id"])
            except Exception as exc:  # noqa: BLE001
                log.warning("lip exit order %s: %s", x["id"], exc)
                continue
            self.book_exit(tk, x, fill_qty(ko, x["n"]))
            self.state["exits"].pop(tk, None)
            self.event("exit_gone", ticker=tk, status=ko.get("status"))

    def recover(self) -> None:
        """For every held position: one exit offer for all of it, at the average price paid."""
        if not self.cfg.get("recover"):
            return
        for tk, p in list(self.state["positions"].items()):
            if self.state["bad"].get(f"exit|{tk}", 0) > self.clock():
                continue
            if self.state["orders"].get(tk):
                # a fill usually means news: pull the rest of that bid (it would also trade
                # against our own exit offer at the same price)
                self.cancel(tk, "bid filled: pull the rest, offer the fill back")
                if self.state["orders"].get(tk):
                    continue
            want = int(math.floor(p["n"] + 1e-6))
            x = self.state["exits"].get(tk)
            if x and round(x["n"] - x.get("sold", 0.0), 2) >= want:
                continue
            if x and not self.cancel_exit(tk, "more filled: re-offer all"):
                continue
            want = int(math.floor(self.state["positions"].get(tk, {}).get("n", 0) + 1e-6))
            if want < 1:
                continue
            px = round(math.ceil(p["cost"] / p["n"] * 100 - 1e-6) / 100, 2) if p["n"] else None
            if not px:
                continue
            b = self.book(tk) or {}
            best = max((q for q, _ in b.get(p["side"]) or []), default=0.0)
            now_ = best >= px - 1e-9        # someone already bids our price: sell at once
            cid = f"lip-x-{uuid.uuid4().hex[:14]}"
            self.pace()
            try:
                o = self.client.place_lip_exit(tk, p["side"], px, want, cid, now=now_)
            except Exception as exc:  # noqa: BLE001
                self.event("exit_reject", ticker=tk, side=p["side"], px=px, n=want, error=str(exc)[:200])
                self.state["bad"][f"exit|{tk}"] = self.clock() + 3600
                continue
            oid = o.get("order_id") or o.get("id")
            x = {"id": oid, "cid": cid, "side": p["side"], "px": px, "n": want, "sold": 0.0, "at": self.clock()}
            self.event("exit_place", ticker=tk, side=p["side"], px=px, n=want, now=now_)
            if now_:
                self.book_exit(tk, x, fill_qty(o, want))
            else:
                self.state["exits"][tk] = x

    def check_stop(self) -> None:
        if self.filled_total() >= self.cfg["fill_stop"] and not self.state.get("halted"):
            self.state["halted"] = f"fill stop: ${self.filled_total():.2f} of bids filled"
            self.cancel_all("fill stop")
            self.alert("Rewards test halted", f"Our cheap bids filled ${self.filled_total():.2f} worth "
                       f"(stop ${self.cfg['fill_stop']:.0f}). All bids cancelled; filled contracts "
                       "stay offered back at what we paid (else held to settlement).")

    def status(self) -> dict:
        return {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mode": self.mode(),
                "control": self.cfg.get("mode"), "kill_switch": self.switch, "halted": self.state.get("halted"),
                "budget": self.cfg["budget"], "resting_cost": round(self.resting_cost(), 2),
                "filled_cost": round(self.filled_cost(), 2), "orders": self.state["orders"],
                "positions": self.state["positions"], "exits": self.state["exits"], "chosen": self.state["chosen"],
                "est": dict(list(self.state["est"].items())[-4:]), "shadow": self.state.get("shadow") or {},
                "credits": sorted(self.state["credits"].values(), key=lambda r: str(r.get("created_time")))[-60:],
                "events": self.state["events"][-80:]}

    def cycle(self) -> None:
        self.refresh()
        now = self.clock()
        if self.mode() != "live":
            if self.state["orders"] and self.client is not None:
                self.cancel_all(f"mode {self.mode()} (control {self.cfg.get('mode')}, switch {self.switch})")
            if self.client is not None and (self.state["exits"] or self.state["positions"]):
                try:
                    if self.killed():
                        for tk in list(self.state["exits"]):
                            self.cancel_exit(tk, "kill switch")
                    else:           # fill stop / test over: keep offering fills back
                        self.pace()
                        self.sync_exits(self.client.resting_orders())
                        self.recover()
                except Exception as exc:  # noqa: BLE001
                    log.warning("lip recover: %s", exc)
                    self.event("error", error=f"recover: {str(exc)[:280]}")
        else:
            try:
                self.sync()
                self.check_stop()
                if self.mode() == "live":
                    fresh = self.cfg.get("reselect") and self.cfg["reselect"] != self.state.get("reselect_seen")
                    if fresh or now - self.state["selected_at"] > self.cfg["select_s"] or not self.state["chosen"]:
                        self.state["reselect_seen"] = self.cfg.get("reselect")
                        self.select()
                    self.manage()
                self.recover()
            except Exception as exc:  # noqa: BLE001
                log.warning("lip cycle: %s", exc)
                self.event("error", error=str(exc)[:300])
        if self.shadow_due():
            # once a day, even when the test is off: reads only, places nothing
            self.state["shadow_try"] = now
            try:
                self.shadow()
            except Exception as exc:  # noqa: BLE001
                log.warning("lip shadow: %s", exc)
                self.event("error", error=f"shadow: {str(exc)[:280]}")
        if self.client is not None and now - self.state.get("credits_at", 0) > 3600:
            self.state["credits_at"] = now
            self.credits()
        self.save()
        if self.upload and now - self.state["uploaded_at"] > self.cfg["upload_s"]:
            try:
                self.upload({"lip-status.json": json.dumps(self.status(), indent=1)})
                self.state["uploaded_at"] = now
            except Exception as exc:  # noqa: BLE001
                log.warning("lip upload: %s", exc)

    def run(self) -> None:
        while True:
            self.cycle()
            self.sleep(self.cfg["cycle_s"])


def main() -> int:
    from . import notify
    from .m15 import client_from_env, github_uploader
    from .util import Http, load_config

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out = Path(os.environ.get("LIP_DIR", "/var/lib/kalshi-lip"))
    out.mkdir(parents=True, exist_ok=True)
    os.environ.pop("OANDA_TOKEN", None)
    os.environ.pop("ANTHROPIC_API_KEY", None)
    cfg = load_config()
    http = Http("kalshi-lip/1", min_interval=0.2, retries=3)
    token = os.environ.get("GITHUB_TOKEN", "").strip()

    def alert(title: str, body: str) -> None:
        try:
            notify.send(http, cfg, title, body, priority=3, tags=["moneybag"])
        except Exception as exc:  # noqa: BLE001
            log.warning("lip alert: %s", exc)

    up = github_uploader(Http("kalshi-lip/1"), token) if token else None
    bot = LipBot(out, client_from_env(http), http, alert=alert, upload=up)
    bot.run()
    return 0
