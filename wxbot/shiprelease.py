"""Shipping release-window watcher: paper log + small REAL test (Nov 22, 2026).

REAL MONEY from Tue Nov 24 (Alex, Nov 22 1:08 AM ET: "Let's go live with porteatch for this
Tuesday"), switched by shiprelease_control.json on main (mode live | off, read every 5 min; also obeys
the global kill switch data/trader_state.json mode off/paper or halted, and goes off by itself when
either file can't be read for 30 min). Rails: 1 contract per bracket, only brackets the posted count
clears by more than `margin` (2) ships, price + taker fee <= `max_cost` (95c), immediate-or-cancel
(never rests, never chases), one real order per market ever, exchange shard 0 only, HARD $5 cap:
cost of open real bets + settled real losses + the new order <= $5 (profits never add room).
Anomaly stop (real orders stop until the control file's `epoch` is raised): a second PortWatch read
disagrees with the first, PortWatch history can't be read, a market's rules don't name PortWatch and
this week, Kalshi refuses an order, Kalshi settles on a different count than PortWatch showed, or a
real bet loses. A total far from the last 4 weeks (50% and 10+ ships) skips that chokepoint's real orders.
The paper twin below keeps logging every Tuesday whatever the real switch says. Review at 10 real fills.

Kalshi's weekly ship-count markets (KXHORMUZWEEKLY, KXSUEZWEEKLY, KXPANAMAWEEKLY,
KXBABELMANDEBWEEKLY) settle on the IMF PortWatch Mon-Sun sum of daily transit calls, and they stay
open until Tuesday 9:00 AM ET. PortWatch posts the finished week early Tuesday (Nov 17: layer edited
5:15 AM ET), and the trade tape showed stale resting orders on the winning side for hours after it
(research /mnt/project-files/analysis/ship-live-data/RESULTS.md). This service measures that window:

  - polls the PortWatch ArcGIS API every 20 s from Monday noon ET until the week is out (or Tuesday
    11 AM ET), every 10 min otherwise, and logs the exact time the full week first appears;
  - at release: computes each chokepoint's week total, reads every order book of the ending events and
    paper-takes the winning side's asks at <= 97c (10 contracts per market at most), skipping
    brackets within 2 ships of the total (PortWatch has revised a week by 1 ship after settlement);
  - re-reads the books 1, 5, 15, 30, 60 and 120 min later to show what a slower bot would still get;
  - after settlement, scores the paper takes against Kalshi's result and checks Kalshi's settled value
    against our total.

Status: m15/shiprelease.json on m15-data. Paper verdict after 4 Tuesdays (~Dec 15).
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import requests

from .kalshi_api import OrderRejected, fill_qty

log = logging.getLogger("wxbot")
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
PW_LAYER = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
            "Daily_Chokepoints_Data/FeatureServer/0")
UA = "kalshi-shiprelease/1 (paper research logger)"
SERIES = {"KXSUEZWEEKLY": "chokepoint1", "KXPANAMAWEEKLY": "chokepoint2",
          "KXBABELMANDEBWEEKLY": "chokepoint4", "KXHORMUZWEEKLY": "chokepoint6"}
SHORT = {"KXSUEZWEEKLY": "Suez", "KXPANAMAWEEKLY": "Panama", "KXBABELMANDEBWEEKLY": "Bab el-Mandeb",
         "KXHORMUZWEEKLY": "Hormuz"}
MAX_PRICE = 0.97          # never "buy" above this
CAP_CT = 10               # paper contracts per market at most
MARGIN = 2                # skip brackets with |total - strike| <= MARGIN ships
FAST_S, SLOW_S = 20, 600
LAGS_S = (0, 60, 300, 900, 1800, 3600, 7200)
KEEP_WEEKS = 12

REPO = "alexanderhabel22-commits/kalshi-weather-bot"
CONTROL_URL = f"https://raw.githubusercontent.com/{REPO}/main/shiprelease_control.json"
SWITCH_URL = f"https://raw.githubusercontent.com/{REPO}/main/data/trader_state.json"
CONTROL_STALE_S = 1800
REAL_DEFAULTS = {"mode": "off", "contracts": 1, "max_loss_usd": 5.0, "max_cost": 0.95, "margin": 2,
                 "slack": 0.03, "epoch": 0}
REAL_MAX_CONTRACTS = 1     # code ceiling whatever the control file says
REAL_MAX_LOSS = 5.0        # code ceiling for the cap
HIST_DEV = 0.5             # anomaly: total more than 50% (and 10+ ships) away from the last 4 weeks' mean


def fee1(q: float) -> float:
    """Kalshi taker fee on one contract at price q, rounded up to the cent."""
    return math.ceil(0.07 * q * (1 - q) * 100 - 1e-9) / 100


def et_str(ts: float) -> str:
    return datetime.fromtimestamp(ts, ET).strftime("%Y-%m-%d %H:%M:%S")


def last_week_end(now: float) -> date:
    """The Sunday that ends the most recent fully finished Mon-Sun week (ET)."""
    d = datetime.fromtimestamp(now, ET).date()
    return d - timedelta(days=(d.weekday() + 1) % 7 or 7)


def event_ticker(series: str, end: date) -> str:
    return f"{series}-{end.strftime('%y%b%d').upper()}"


def fast_window(now: float, released: bool) -> bool:
    """20-s polling from Monday 12:00 PM ET to Tuesday 11:00 AM ET while the week isn't out."""
    if released:
        return False
    t = datetime.fromtimestamp(now, ET)
    return (t.weekday() == 0 and t.hour >= 12) or (t.weekday() == 1 and t.hour < 11)


def week_totals(rows: list[dict], end: date) -> dict | None:
    """{series: total} when every day Mon..Sun is present for all four chokepoints, else None."""
    days = {str(end - timedelta(days=i)) for i in range(7)}
    got: dict[str, dict[str, int]] = {}
    for a in rows:
        d = str(a.get("date") or "")[:10]
        if d in days and a.get("n_total") is not None:
            got.setdefault(a.get("portid"), {})[d] = int(a["n_total"])
    out = {}
    for s, pid in SERIES.items():
        if len(got.get(pid, {})) != 7:
            return None
        out[s] = sum(got[pid].values())
    return out


def days_present(rows: list[dict], end: date) -> int:
    days = {str(end - timedelta(days=i)) for i in range(7)}
    return len({(a.get("portid"), str(a.get("date"))[:10]) for a in rows
                if str(a.get("date") or "")[:10] in days})


def winning_asks(book: dict, side: str) -> list[tuple[float, float]]:
    """Asks on `side` (price, size), cheapest first. A YES ask is 1 - a NO bid and vice versa."""
    ob = (book or {}).get("orderbook_fp") or (book or {}).get("orderbook") or {}
    other = "no" if side == "yes" else "yes"
    lv = ob.get(f"{other}_dollars") or ob.get(other) or []
    out = []
    for p, q in lv:
        try:
            p, q = float(p), float(q)
        except (TypeError, ValueError):
            continue
        p = p / 100 if p > 1.5 else p
        if q > 0:
            out.append((round(1 - p, 4), q))
    return sorted(out)


def take(asks: list[tuple[float, float]], cap: float | None = CAP_CT) -> dict:
    """Walk the asks up to MAX_PRICE (and `cap` contracts). Profit if the side wins, after fees."""
    n = cost = prof = 0.0
    for p, q in asks:
        if p > MAX_PRICE:
            break
        k = q if cap is None else min(q, cap - n)
        if k <= 0:
            break
        n += k
        cost += k * p
        prof += k * (1 - p - fee1(p))
    return {"ct": round(n, 2), "cost": round(cost, 2), "profit": round(prof, 2),
            "avg": round(cost / n, 4) if n else None}


def rules_ok(m: dict, end: date) -> bool:
    """The market's own rules must name IMF PortWatch and this Mon-Sun week."""
    txt = m.get("rules_primary") or ""
    start = end - timedelta(days=6)
    return ("IMF PortWatch" in txt and f"{start:%b} {start.day}, {start.year}" in txt
            and f"{end:%b} {end.day}, {end.year}" in txt)


def side_for(total: int, strike: float) -> str:
    return "yes" if total > strike else "no"


class ShipRelease:
    def __init__(self, out_dir: Path, clock: Callable[[], float] = time.time,
                 status_up: Callable[[dict], None] | None = None, session=None, client=None,
                 alert: Callable[[str, str], None] | None = None):
        self.out = out_dir
        self.out.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.status_up = status_up
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": UA})
        self.weeks: dict[str, dict] = {}
        self.polls = {"ok": 0, "err": 0, "last_ok": None, "last_err": None, "edit": None}
        self.client, self.alert = client, alert
        self.rcfg = dict(REAL_DEFAULTS)
        self.switch = {"mode": None, "halted": False}
        self.ok_at = -1e12
        self.real: list[dict] = []
        self.stopped: dict | None = None
        self.load()

    # -- persistence
    def state_file(self) -> Path:
        return self.out / "state.json"

    def load(self) -> None:
        try:
            d = json.loads(self.state_file().read_text())
            self.weeks, self.polls = d.get("weeks", {}), d.get("polls", self.polls)
            self.real, self.stopped = d.get("real", []), d.get("stopped")
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        tmp = self.state_file().with_suffix(".tmp")
        tmp.write_text(json.dumps({"weeks": self.weeks, "polls": self.polls, "real": self.real,
                                   "stopped": self.stopped}))
        tmp.replace(self.state_file())

    def write(self, row: dict) -> None:
        row = {"ts": round(self.clock(), 1), **row}
        with open(self.out / "log.jsonl", "a") as f:
            f.write(json.dumps(row) + "\n")

    # -- network (GET only)
    def get_json(self, url: str, params: dict | None = None) -> dict:
        r = self.s.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    def portwatch(self, end: date, days: int = 7) -> tuple[list[dict], float | None]:
        start = end - timedelta(days=days - 1)
        ids = ",".join(f"'{p}'" for p in SERIES.values())
        d = self.get_json(f"{PW_LAYER}/query", {
            "where": f"portid IN ({ids}) AND date >= '{start}' AND date <= '{end}'",
            "outFields": "date,portid,n_total",
            "resultRecordCount": 2000, "f": "json"})
        if "error" in d:
            raise RuntimeError(str(d["error"])[:200])
        rows = [f.get("attributes") or {} for f in d.get("features") or []]
        edit = None
        try:
            info = self.get_json(PW_LAYER, {"f": "json"})
            e = (info.get("editingInfo") or {}).get("dataLastEditDate")
            edit = e / 1000 if e else None
        except Exception:  # noqa: BLE001  the edit time is a nice-to-have
            pass
        return rows, edit

    def markets(self, series: str, end: date) -> list[dict]:
        d = self.get_json(f"{KALSHI}/markets", {"event_ticker": event_ticker(series, end), "limit": 200})
        return d.get("markets") or []

    def book(self, ticker: str) -> dict:
        return self.get_json(f"{KALSHI}/markets/{ticker}/orderbook")

    # -- the work
    def week(self, end: date) -> dict:
        return self.weeks.setdefault(str(end), {"end": str(end), "released": None, "totals": None,
                                                "first_partial": None, "snaps": {}, "takes": [],
                                                "pre": None, "settled": False})

    def poll(self, now: float) -> None:
        end = last_week_end(now)
        w = self.week(end)
        if w["released"]:
            return
        try:
            rows, edit = self.portwatch(end)
        except Exception as exc:  # noqa: BLE001
            self.polls.update(err=self.polls["err"] + 1, last_err=f"{et_str(now)} {str(exc)[:120]}")
            return
        self.polls.update(ok=self.polls["ok"] + 1, last_ok=et_str(now),
                          edit=et_str(edit) if edit else self.polls.get("edit"))
        n = days_present(rows, end)
        if n and not w["first_partial"]:
            w["first_partial"] = {"et": et_str(now), "days": n}
            self.write({"kind": "partial", "end": str(end), "days": n})
        tot = week_totals(rows, end)
        if tot is None:
            if w["pre"] is None and fast_window(now, False):
                w["pre"] = "pending"           # one pre-release book snapshot per week
                self.snapshot(w, end, now, "pre")
            return
        w.update(released=now, released_et=et_str(now), totals=tot,
                 layer_edit_et=et_str(edit) if edit else None)
        self.write({"kind": "release", "end": str(end), "totals": tot, "edit": edit})
        log.info("shiprelease: week %s out at %s ET: %s", end, et_str(now), tot)
        w["checks"] = self.release_checks(end, tot)
        self.snapshot(w, end, now, "0")
        self.push(now)

    def snapshot(self, w: dict, end: date, now: float, label: str) -> None:
        tot = w.get("totals")
        snap = {"et": et_str(now), "after_s": round(now - w["released"]) if w.get("released") else None,
                "markets": []}
        for series in SERIES:
            try:
                ms = self.markets(series, end)
            except Exception as exc:  # noqa: BLE001
                snap["markets"].append({"series": series, "err": str(exc)[:100]})
                continue
            for m in ms:
                k = m.get("floor_strike")
                if m.get("strike_type") != "greater" or k is None:
                    continue
                row = {"series": series, "ticker": m["ticker"], "strike": k, "status": m.get("status"),
                       "close": m.get("close_time"), "shard": m.get("exchange_index"),
                       "rules_ok": rules_ok(m, end)}
                if (m.get("status") or "") not in ("active", "open"):
                    snap["markets"].append(row)
                    continue
                try:
                    b = self.book(m["ticker"])
                except Exception as exc:  # noqa: BLE001
                    row["err"] = str(exc)[:100]
                    snap["markets"].append(row)
                    continue
                row["book_et"] = et_str(self.clock())
                if tot:
                    side = side_for(tot[series], float(k))
                    asks = winning_asks(b, side)
                    row.update(side=side, dist=tot[series] - float(k), best=asks[0][0] if asks else None,
                               cap=take(asks), all=take(asks, cap=None))
                else:
                    ya, na = winning_asks(b, "yes"), winning_asks(b, "no")
                    row.update(yes_ask=ya[0][0] if ya else None, no_ask=na[0][0] if na else None)
                snap["markets"].append(row)
        if label == "pre":
            w["pre"] = snap
        else:
            w["snaps"][label] = snap
            self.write({"kind": "snap", "end": w["end"], "label": label, "snap": snap})
        if label == "0":
            for r in snap["markets"]:
                if r.get("cap") and r["cap"]["ct"] > 0:
                    w["takes"].append({"ticker": r["ticker"], "series": r["series"], "side": r["side"],
                                       "dist": r["dist"], "skip": abs(r["dist"]) <= MARGIN, **r["cap"],
                                       "result": None, "pnl": None})
            self.real_orders(w, snap)

    def follow_up(self, now: float) -> None:
        for w in self.weeks.values():
            if not w.get("released"):
                continue
            end = date.fromisoformat(w["end"])
            for lag in LAGS_S[1:]:
                if str(lag) not in w["snaps"] and now >= w["released"] + lag:
                    closes = [r.get("close") for s in w["snaps"].values() for r in s["markets"] if r.get("close")]
                    close = min((datetime.fromisoformat(c.replace("Z", "+00:00")).timestamp() for c in closes),
                                default=None)
                    if close is not None and now > close:
                        w["snaps"][str(lag)] = {"et": et_str(now), "after_s": lag, "closed": True, "markets": []}
                        continue
                    self.snapshot(w, end, now, str(lag))
                    self.push(now)
                    return              # one snapshot per loop step

    def settle(self, now: float) -> None:
        for w in self.weeks.values():
            if not w.get("released") or w.get("settled") or now < w["released"] + 3600:
                continue
            end = date.fromisoformat(w["end"])
            values, done = {}, True
            for series in SERIES:
                try:
                    ms = {m["ticker"]: m for m in self.markets(series, end)}
                except Exception:  # noqa: BLE001
                    return
                for m in ms.values():
                    if m.get("expiration_value") not in (None, ""):
                        values[series] = m.get("expiration_value")
                for t in w["takes"]:
                    if t["series"] != series or t["result"]:
                        continue
                    res = (ms.get(t["ticker"], {}).get("result") or "").lower()
                    if res in ("yes", "no"):
                        t["result"] = res
                        t["pnl"] = t["profit"] if res == t["side"] else round(-t["cost"] - t["ct"] * fee1(t["avg"] or 0), 2)
                    else:
                        done = False
            w["kalshi_values"] = values
            for sr_, v in values.items():
                tv = (w.get("totals") or {}).get(sr_)
                if tv is not None and str(v).strip() != str(tv) and not w.get("value_flag", {}).get(sr_):
                    w.setdefault("value_flag", {})[sr_] = str(v)
                    self.anomaly(f"Kalshi settled {SHORT[sr_]} week {w['end']} on {v}, PortWatch showed {tv} at release")
            if done:
                w["settled"] = True
                self.write({"kind": "settled", "end": w["end"], "values": values})

    # -- real money
    def fetch_json(self, url: str):
        r = self.s.get(url, params={"t": int(self.clock())}, timeout=10)
        r.raise_for_status()
        return r.json()

    def read_control(self, now: float) -> None:
        try:
            c = self.fetch_json(CONTROL_URL) or {}
            sw = self.fetch_json(SWITCH_URL) or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("shiprelease control read: %s", exc)
            return
        self.rcfg = {**REAL_DEFAULTS, **{k: v for k, v in c.items() if k in REAL_DEFAULTS}}
        self.rcfg["mode"] = str(self.rcfg.get("mode") or "off").lower()
        halted = sw.get("halted") or {}
        self.switch = {"mode": (sw.get("mode") or "").lower() or None,
                       "halted": bool(isinstance(halted, dict) and halted.get("live"))}
        self.ok_at = now
        if self.stopped and int(self.rcfg.get("epoch") or 0) > int(self.stopped.get("epoch") or 0):
            self.write({"kind": "resume", "was": self.stopped})
            self.stopped = None

    def real_mode(self) -> str:
        if self.client is None or self.rcfg.get("mode") != "live":
            return "off"
        if self.switch["mode"] in ("off", "paper") or self.switch["halted"]:
            return "off"
        if self.clock() - self.ok_at > CONTROL_STALE_S:
            return "off"
        if self.stopped:
            return "stopped"
        return "live"

    def cap(self) -> float:
        return min(float(self.rcfg.get("max_loss_usd") or 0), REAL_MAX_LOSS)

    def cap_used(self) -> float:
        """Open real bets at cost + settled real losses. Profits never add room."""
        used = 0.0
        for r in self.real:
            if r.get("status") == "open":
                used += r["cost"]
            elif r.get("status") == "settled" and r["pnl"] < 0:
                used -= r["pnl"]
        return round(used, 4)

    def anomaly(self, why: str) -> None:
        """Stop real orders until the control file's epoch is raised; one push."""
        self.write({"kind": "anomaly", "why": why})
        log.warning("shiprelease anomaly: %s", why)
        if self.stopped:
            return
        self.stopped = {"why": why, "et": et_str(self.clock()), "epoch": int(self.rcfg.get("epoch") or 0)}
        if self.alert and self.client is not None:
            self.alert("Ship release bot: real orders stopped",
                       f"{why}. The paper log keeps running; I'll look at it before any more real orders.")

    def release_checks(self, end: date, tot: dict) -> dict:
        """Second read must match (else anomaly stop). A total far from the last 4 weeks: that
        chokepoint gets no real orders this week. History unreadable: anomaly stop."""
        out = {"second_read": None, "history": {}, "skip_series": []}
        try:
            rows, _ = self.portwatch(end)
            again = week_totals(rows, end)
        except Exception as exc:  # noqa: BLE001
            again, out["second_read_err"] = None, str(exc)[:100]
        out["second_read"] = again == tot
        if again != tot:
            self.anomaly(f"PortWatch second read for week {end} gave {again}, first {tot}")
        try:
            rows, _ = self.portwatch(end - timedelta(days=7), days=28)
            for s_, pid in SERIES.items():
                vals = [int(a["n_total"]) for a in rows if a.get("portid") == pid and a.get("n_total") is not None]
                if len(vals) < 21:
                    continue
                mean = sum(vals) / len(vals) * 7
                out["history"][s_] = round(mean, 1)
                if abs(tot[s_] - mean) > max(10, HIST_DEV * mean):
                    out["skip_series"].append(s_)     # no real orders on this chokepoint this week
                    self.write({"kind": "real_skip_series", "series": s_, "total": tot[s_], "mean4": mean})
        except Exception as exc:  # noqa: BLE001
            out["history_err"] = str(exc)[:100]
            self.anomaly(f"PortWatch history read failed ({str(exc)[:60]})")
        return out

    def real_orders(self, w: dict, snap: dict) -> None:
        """At release: 1-ct IOC buys on the clearest, cheapest winning sides, inside the cap."""
        mode = self.real_mode()
        w["real_mode"] = mode
        if mode != "live":
            return
        c = self.rcfg
        margin, max_cost, slack = float(c["margin"]), float(c["max_cost"]), float(c["slack"])
        n = max(1, min(int(c["contracts"]), REAL_MAX_CONTRACTS))
        cands = []
        for r in snap["markets"]:
            if r.get("best") is None or abs(r["dist"]) <= margin:
                continue
            if r["series"] in ((w.get("checks") or {}).get("skip_series") or []):
                continue
            if not r.get("rules_ok"):
                self.anomaly(f"{r['ticker']} rules don't name IMF PortWatch and week {w['end']}")
                return
            if int(float(r.get("shard") or 0)) != 0 or any(x["ticker"] == r["ticker"] for x in self.real):
                continue
            if r["best"] + fee1(r["best"]) > max_cost + 1e-9:
                continue
            cands.append(r)
        cands.sort(key=lambda r: r["best"])        # biggest edge first
        for r in cands:
            # limit = best ask + slack, but never past max_cost after the fee
            lim = round(min(r["best"] + slack, 0.99), 2)
            while lim > r["best"] and lim + fee1(lim) > max_cost + 1e-9:
                lim = round(lim - 0.01, 2)
            worst = round(n * (lim + fee1(lim)), 4)
            if self.cap_used() + worst > self.cap() + 1e-9:
                self.write({"kind": "real_skip", "ticker": r["ticker"], "why": "cap",
                            "used": self.cap_used(), "cap": self.cap()})
                continue
            rec = {"week": w["end"], "ticker": r["ticker"], "series": r["series"], "side": r["side"],
                   "dist": r["dist"], "seen_ask": r["best"], "limit": lim, "placed": n,
                   "ts": round(self.clock(), 1), "et": et_str(self.clock()),
                   "after_s": round(self.clock() - w["released"], 1)}
            try:
                o = self.client.buy_now(r["ticker"], r["side"], lim, n,
                                        f"sr-{r['ticker']}-{int(self.clock())}")
                got = fill_qty(o, n)
                paid = o.get("taker_fill_cost_dollars")
                paid = float(paid) if paid not in (None, "") else None
                rec.update(order_id=o.get("order_id"), filled=got)
            except (OrderRejected, Exception) as exc:  # noqa: BLE001
                rec.update(status="error", filled=0, cost=0.0, err=str(exc)[:200])
                self.real.append(rec)
                self.write({"kind": "real", **rec})
                self.anomaly(f"Kalshi refused {r['ticker']} {r['side'].upper()} at {lim:.2f}: {str(exc)[:100]}")
                return
            if got <= 0:
                rec.update(status="missed", cost=0.0)
            else:
                avg = (paid / got) if paid else lim
                rec.update(status="open", avg=round(avg, 4), cost=round(got * avg + got * fee1(round(avg, 2)), 4))
            self.real.append(rec)
            self.write({"kind": "real", **rec})
        week_real = [x for x in self.real if x["week"] == w["end"]]
        filled = [x for x in week_real if (x.get("filled") or 0) > 0]
        if self.alert and filled:
            self.alert("Ship release bot: real bets placed",
                       f"PortWatch posted week to {w['end']} at {w.get('released_et', '')[11:16]} ET. Bought "
                       f"{len(filled)} brackets (1 ct each), ${sum(x['cost'] for x in filled):.2f} at risk, "
                       f"worth +${sum(x['filled'] - x['cost'] for x in filled):.2f} if PortWatch holds. "
                       f"{len(week_real) - len(filled)} missed.")

    def settle_real(self, now: float) -> None:
        for r in self.real:
            if r.get("status") != "open" or now - r["ts"] < 600 or now - r.get("checked", 0) < 300:
                continue
            r["checked"] = now
            try:
                m = self.get_json(f"{KALSHI}/markets/{r['ticker']}").get("market") or {}
            except Exception as exc:  # noqa: BLE001
                log.info("shiprelease settle %s: %s", r["ticker"], exc)
                continue
            out = (m.get("result") or "").lower()
            if out in ("yes", "no"):
                r["status"], r["result"] = "settled", out
                r["pnl"] = round((r["filled"] if out == r["side"] else 0.0) - r["cost"], 4)
                self.write({"kind": "real_settle", "ticker": r["ticker"], "result": out, "pnl": r["pnl"]})
                if out != r["side"]:
                    self.anomaly(f"real bet {r['ticker']} {r['side'].upper()} lost (Kalshi said {out})")

    def real_status(self) -> dict:
        st = [r for r in self.real if r.get("status") == "settled"]
        rp = sum(r["pnl"] for r in st)
        rct = sum(r["filled"] for r in st)
        return {"mode": self.real_mode(), "control": dict(self.rcfg), "cap": self.cap(),
                "cap_used": self.cap_used(), "stopped": self.stopped, "orders": len(self.real),
                "filled": len([r for r in self.real if (r.get("filled") or 0) > 0]),
                "missed": len([r for r in self.real if r.get("status") == "missed"]),
                "settled": len(st), "won": len([r for r in st if r["pnl"] > 0]), "pnl": round(rp, 2),
                "c_per_ct": round(100 * rp / rct, 1) if rct else None,
                "open_cost": round(sum(r["cost"] for r in self.real if r.get("status") == "open"), 2),
                "recent": self.real[-30:]}

    def prune(self) -> None:
        for k in sorted(self.weeks)[:-KEEP_WEEKS]:
            self.weeks.pop(k, None)

    # -- status for the dashboard
    def status(self, now: float) -> dict:
        weeks = []
        for k in sorted(self.weeks, reverse=True):
            w = self.weeks[k]
            lags = {}
            for lab, s in w.get("snaps", {}).items():
                ms = [r for r in s.get("markets", []) if r.get("cap")]
                keep = [r for r in ms if abs(r["dist"]) > MARGIN]
                lags[lab] = {"et": s.get("et"), "closed": s.get("closed", False),
                             "profit_cap": round(sum(r["cap"]["profit"] for r in keep), 2),
                             "ct_cap": round(sum(r["cap"]["ct"] for r in keep), 1),
                             "profit_all_mkts": round(sum(r["cap"]["profit"] for r in ms), 2),
                             "profit_uncapped": round(sum(r["all"]["profit"] for r in keep), 2)}
            tk = [t for t in w.get("takes", []) if not t["skip"]]
            st = [t for t in tk if t["pnl"] is not None]
            weeks.append({"end": w["end"], "released_et": w.get("released_et"),
                          "layer_edit_et": w.get("layer_edit_et"), "first_partial": w.get("first_partial"),
                          "totals": w.get("totals"), "kalshi_values": w.get("kalshi_values"),
                          "lags": lags, "takes": w.get("takes", []), "settled": w.get("settled", False),
                          "checks": w.get("checks"), "real_mode": w.get("real_mode"),
                          "paper": {"markets": len(tk), "ct": round(sum(t["ct"] for t in tk), 1),
                                    "settled": len(st), "won": len([t for t in st if t["pnl"] > 0]),
                                    "pnl": round(sum(t["pnl"] for t in st), 2)},
                          "pre": w.get("pre") if isinstance(w.get("pre"), dict) else None})
        allst = [t for w in self.weeks.values() for t in w.get("takes", [])
                 if not t["skip"] and t["pnl"] is not None]
        real = self.real_status()
        return {"at": et_str(now), "paper_only": real["mode"] != "live", "real": real,
                "rules": {"max_price": MAX_PRICE, "cap_ct": CAP_CT, "margin": MARGIN},
                "polls": self.polls, "weeks": weeks,
                "totals": {"weeks_released": len([w for w in self.weeks.values() if w.get("released")]),
                           "settled_takes": len(allst), "ct": round(sum(t["ct"] for t in allst), 1),
                           "pnl": round(sum(t["pnl"] for t in allst), 2),
                           "lost": len([t for t in allst if t["pnl"] < 0])}}

    def push(self, now: float) -> None:
        if self.status_up:
            try:
                self.status_up({"shiprelease.json": json.dumps(self.status(now), indent=1)})
            except Exception as exc:  # noqa: BLE001
                log.warning("shiprelease status upload: %s", exc)

    def step(self, now: float) -> float:
        """One loop step; returns seconds to sleep."""
        if now - self.ok_at >= 300:
            self.read_control(now)
        self.poll(now)
        self.follow_up(now)
        self.settle(now)
        self.settle_real(now)
        self.prune()
        self.save()
        w = self.weeks.get(str(last_week_end(now))) or {}
        fast = fast_window(now, bool(w.get("released")))
        pending = any(w2.get("released") and len(w2["snaps"]) < len(LAGS_S) for w2 in self.weeks.values())
        return FAST_S if fast or pending else SLOW_S

    def run(self) -> None:
        self.write({"kind": "start"})
        nup = 0.0
        while True:
            try:
                now = self.clock()
                wait = self.step(now)
                if now >= nup:
                    nup = now + 600
                    self.push(now)
            except Exception as exc:  # noqa: BLE001  never die; log and keep going
                log.warning("shiprelease step: %s", exc)
                wait = 60
            time.sleep(wait)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out = Path(os.environ.get("SHIPREL_DIR", "/var/lib/kalshi-shiprel"))
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    client = alert = None
    if os.environ.get("SHIPREL_REAL", "1") != "0":
        from .m15 import client_from_env
        from .util import Http as _Http
        try:
            client = client_from_env(_Http("kalshi-shiprelease/1", min_interval=0.05, retries=1))
        except Exception as exc:  # noqa: BLE001
            log.warning("shiprelease: no Kalshi client (%s): paper only", exc)
    for k in list(os.environ):          # the client holds the key in memory; drop it from env
        if k.startswith(("KALSHI", "ANTHROPIC", "OANDA")):
            os.environ.pop(k, None)
    if client is not None:
        from . import notify
        from .util import Http as _Http2, load_config
        cfg, nh = load_config(), _Http2("kalshi-shiprelease/1")

        def _alert(title: str, body: str) -> None:
            try:
                notify.send(nh, cfg, title, body, priority=3, tags=["ship"])
            except Exception as exc:  # noqa: BLE001
                log.warning("shiprelease alert: %s", exc)
        alert = _alert
    status_up = None
    if token:
        from .m15 import github_uploader
        from .util import Http
        status_up = github_uploader(Http("kalshi-shiprelease/1"), token)
    ShipRelease(out, status_up=status_up, client=client, alert=alert).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
