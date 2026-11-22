from datetime import date, datetime

from wxbot import shiprelease as sr

ET = sr.ET


def ts(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=ET).timestamp()


def pw_rows(end, days=7, totals=None):
    totals = totals or {"chokepoint1": 70, "chokepoint2": 28, "chokepoint4": 25, "chokepoint6": 2}
    rows = []
    for pid, per in totals.items():
        for i in range(days):
            rows.append({"date": str(end - sr.timedelta(days=i)), "portid": pid, "n_total": per})
    return rows


class Resp:
    def __init__(self, js):
        self.js = js

    def raise_for_status(self):
        pass

    def json(self):
        return self.js


class FakeSession:
    """PortWatch + Kalshi stand-in. Each series has strikes around the week total."""

    def __init__(self, end):
        self.end, self.days, self.headers, self.calls = end, 0, {}, []
        self.results = {}
        self.control = {"mode": "off"}
        self.switch = {"mode": "live"}
        self.override = {}          # date -> {portid: n} to fake odd values
        self.rules = None
        self.values = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        if url == sr.CONTROL_URL:
            return Resp(self.control)
        if url == sr.SWITCH_URL:
            return Resp(self.switch)
        if url.endswith("/query"):
            w = params["where"]
            lo = date.fromisoformat(w.split(">= '")[1][:10])
            hi = date.fromisoformat(w.split("<= '")[1][:10])
            cur0 = self.end - sr.timedelta(days=6)
            rows = []
            for i in range((hi - lo).days + 1):
                d = lo + sr.timedelta(days=i)
                if d >= cur0 and (d - cur0).days >= self.days:
                    continue
                for pid, per in {"chokepoint1": 70, "chokepoint2": 28, "chokepoint4": 25, "chokepoint6": 2}.items():
                    rows.append({"date": str(d), "portid": pid, "n_total": self.override.get(str(d), {}).get(pid, per)})
            return Resp({"features": [{"attributes": a} for a in rows]})
        if url == sr.PW_LAYER:
            return Resp({"editingInfo": {"dataLastEditDate": 1795000000000}})
        if url.endswith("/markets"):
            ev = params["event_ticker"]
            s = ev.split("-")[0]
            base = {"KXSUEZWEEKLY": 490, "KXPANAMAWEEKLY": 196, "KXBABELMANDEBWEEKLY": 175,
                    "KXHORMUZWEEKLY": 14}[s]
            ms = []
            for k in (base - 10, base - 1, base + 5):
                t = f"{ev}-T{k}"
                ms.append({"ticker": t, "strike_type": "greater", "floor_strike": k, "status": "active",
                           "exchange_index": 0, "rules_primary": self.rules or
                           f"If the number of transit calls ... reported by the IMF PortWatch is above {k} "
                           f"from Nov 16, 2026 to Nov 22, 2026, then the market resolves to Yes.",
                           "close_time": "2026-11-24T14:00:00Z", "result": self.results.get(t, ""),
                           "expiration_value": self.values.get(s, str(base)) if self.results else ""})
            return Resp({"markets": ms})
        if "/markets/" in url and not url.endswith("/orderbook"):
            t = url.rsplit("/", 1)[1]
            return Resp({"market": {"ticker": t, "result": self.results.get(t, "")}})
        if url.endswith("/orderbook"):
            # YES bids at 0.60 (x50), NO bids at 0.30 (x20): YES ask 0.70, NO ask 0.40
            return Resp({"orderbook_fp": {"yes_dollars": [["0.6000", "50.00"]],
                                          "no_dollars": [["0.3000", "20.00"], ["0.0100", "999"]]}})
        raise AssertionError(url)


def test_week_helpers():
    assert sr.last_week_end(ts(2026, 11, 24, 5)) == date(2026, 11, 22)     # Tuesday
    assert sr.last_week_end(ts(2026, 11, 23, 13)) == date(2026, 11, 22)    # Monday
    assert sr.last_week_end(ts(2026, 11, 22, 23)) == date(2026, 11, 15)    # Sunday: week not over
    assert sr.event_ticker("KXSUEZWEEKLY", date(2026, 11, 22)) == "KXSUEZWEEKLY-26NOV22"
    assert sr.fast_window(ts(2026, 11, 24, 5), False) and not sr.fast_window(ts(2026, 11, 24, 5), True)
    assert sr.fast_window(ts(2026, 11, 23, 12), False) and not sr.fast_window(ts(2026, 11, 23, 11), False)
    assert not sr.fast_window(ts(2026, 11, 24, 11), False)
    end = date(2026, 11, 22)
    assert sr.week_totals(pw_rows(end, 6), end) is None
    assert sr.week_totals(pw_rows(end), end) == {"KXSUEZWEEKLY": 490, "KXPANAMAWEEKLY": 196,
                                                 "KXBABELMANDEBWEEKLY": 175, "KXHORMUZWEEKLY": 14}
    assert sr.side_for(320, 320) == "no" and sr.side_for(321, 320) == "yes"


def test_asks_and_take():
    book = {"orderbook_fp": {"yes_dollars": [["0.02", "5"], ["0.05", "3"]],
                             "no_dollars": [["0.90", "4"], ["0.95", "6"], ["0.01", "100"]]}}
    assert sr.winning_asks(book, "yes")[:2] == [(0.05, 6.0), (0.1, 4.0)]
    assert sr.winning_asks(book, "no") == [(0.95, 3.0), (0.98, 5.0)]
    t = sr.take(sr.winning_asks(book, "yes"))
    assert t["ct"] == 10 and abs(t["cost"] - (6 * 0.05 + 4 * 0.10)) < 1e-9
    assert sr.take(sr.winning_asks(book, "no"))["ct"] == 3      # the 98c level is above MAX_PRICE
    assert sr.take([])["ct"] == 0


def test_release_flow(tmp_path):
    end = date(2026, 11, 22)
    clock = [ts(2026, 11, 24, 4, 0)]
    fs = FakeSession(end)
    pushed = []
    w = sr.ShipRelease(tmp_path, clock=lambda: clock[0], session=fs, status_up=pushed.append)
    fs.days = 3                                    # partial upload: no release yet
    assert w.step(clock[0]) == sr.FAST_S
    wk = w.weeks["2026-11-22"]
    assert wk["released"] is None and wk["first_partial"]["days"] == 12
    assert isinstance(wk["pre"], dict) and wk["pre"]["markets"][0]["yes_ask"] == 0.7
    fs.days = 7
    clock[0] = ts(2026, 11, 24, 5, 15)
    w.step(clock[0])
    wk = w.weeks["2026-11-22"]
    assert wk["released_et"] == "2026-11-24 05:15:00" and wk["totals"]["KXSUEZWEEKLY"] == 490
    s0 = wk["snaps"]["0"]["markets"]
    m = {r["ticker"]: r for r in s0}
    assert m["KXSUEZWEEKLY-26NOV22-T480"]["side"] == "yes" and m["KXSUEZWEEKLY-26NOV22-T480"]["best"] == 0.7
    assert m["KXSUEZWEEKLY-26NOV22-T495"]["side"] == "no" and m["KXSUEZWEEKLY-26NOV22-T495"]["best"] == 0.4
    skips = {t["ticker"]: t["skip"] for t in wk["takes"]}
    assert skips["KXSUEZWEEKLY-26NOV22-T489"] is True and skips["KXSUEZWEEKLY-26NOV22-T480"] is False
    assert pushed and "shiprelease.json" in pushed[-1]
    # follow-up snapshots at 1 and 5 min, one per step
    clock[0] += 61
    w.step(clock[0])
    assert "60" in wk["snaps"] and "300" not in wk["snaps"]
    # settle after an hour
    for t in wk["takes"]:
        fs.results[t["ticker"]] = t["side"] if t["ticker"] != "KXSUEZWEEKLY-26NOV22-T480" else "no"
    clock[0] = ts(2026, 11, 24, 8, 0)
    for _ in range(8):
        w.step(clock[0])
    assert wk["settled"]
    t480 = [t for t in wk["takes"] if t["ticker"] == "KXSUEZWEEKLY-26NOV22-T480"][0]
    assert t480["pnl"] < 0
    st = w.status(clock[0])
    assert st["paper_only"] and st["weeks"][0]["lags"]["0"]["profit_cap"] > 0
    assert st["totals"]["lost"] == 1
    assert set(wk["snaps"]) == {str(x) for x in sr.LAGS_S}
    # state survives a restart
    w2 = sr.ShipRelease(tmp_path, clock=lambda: clock[0], session=fs)
    assert w2.weeks["2026-11-22"]["settled"]


def test_late_release_after_close(tmp_path):
    end = date(2026, 11, 22)
    fs = FakeSession(end)
    fs.days = 7
    clock = [ts(2026, 11, 24, 8, 30)]
    w = sr.ShipRelease(tmp_path, clock=lambda: clock[0], session=fs)
    w.step(clock[0])
    clock[0] = ts(2026, 11, 24, 9, 45)
    for _ in range(6):
        w.step(clock[0])
    wk = w.weeks["2026-11-22"]
    assert wk["snaps"]["3600"]["closed"] is True and "7200" not in wk["snaps"]


def test_portwatch_error_counts(tmp_path):
    class Bad(FakeSession):
        def get(self, url, params=None, timeout=None):
            if url.endswith("/query"):
                return Resp({"error": {"code": 400}})
            return super().get(url, params, timeout)
    w = sr.ShipRelease(tmp_path, clock=lambda: ts(2026, 11, 24, 5), session=Bad(date(2026, 11, 22)))
    w.step(ts(2026, 11, 24, 5))
    assert w.polls["err"] == 1 and w.weeks["2026-11-22"]["released"] is None


class FakeClient:
    def __init__(self, fill=1, refuse=False):
        self.orders, self.fill, self.refuse = [], fill, refuse

    def buy_now(self, ticker, side, price, count, coid):
        self.orders.append((ticker, side, price, count))
        if self.refuse:
            raise sr.OrderRejected("insufficient balance")
        return {"order_id": f"o{len(self.orders)}", "fill_count_fp": str(self.fill),
                "taker_fill_cost_dollars": f"{(price - 0.02) * self.fill:.2f}" if self.fill else "0"}


def live(tmp_path, client, **ctl):
    end = date(2026, 11, 22)
    fs = FakeSession(end)
    fs.control = {"mode": "live", "max_loss_usd": 5, **ctl}
    clock = [ts(2026, 11, 24, 4, 0)]
    alerts = []
    w = sr.ShipRelease(tmp_path, clock=lambda: clock[0], session=fs, client=client,
                       alert=lambda t, b: alerts.append((t, b)))
    return w, fs, clock, alerts


def release(w, fs, clock):
    fs.days = 3
    w.step(clock[0])
    fs.days = 7
    clock[0] = ts(2026, 11, 24, 5, 15)
    w.step(clock[0])
    return w.weeks["2026-11-22"]


def test_real_orders_rails(tmp_path):
    c = FakeClient()
    w, fs, clock, alerts = live(tmp_path, c)
    assert w.real_mode() == "off"            # control not read yet
    wk = release(w, fs, clock)
    assert wk["real_mode"] == "live" and wk["checks"]["second_read"] is True
    # books: YES ask 0.70, NO ask 0.40; brackets at dist -1 (T491/T197...) are skipped
    tk = [o[0] for o in c.orders]
    assert all(not t.endswith(("-T489", "-T195", "-T174", "-T13")) for t in tk)
    assert all(o[3] == 1 for o in c.orders)
    # cheapest winning side first (NO at 0.40), limit = ask + 3c
    assert c.orders[0][1] == "no" and c.orders[0][2] == 0.43
    # $5 cap incl. open bets: never exceeded at the worst-case limit
    assert w.cap_used() <= 5.0 + 1e-9
    worst = sum(o[2] + sr.fee1(o[2]) for o in c.orders)
    assert worst <= 5.0 + 1e-9
    assert len(c.orders) == 8               # 4 NO legs at 0.43+fee, then YES legs at 0.73+fee until the cap
    assert alerts and "real bets placed" in alerts[-1][0]
    # one real order per market: a second snapshot-0 never re-buys
    n = len(c.orders)
    w.real_orders(wk, wk["snaps"]["0"])
    assert len(c.orders) == n
    st = w.status(clock[0])
    assert st["paper_only"] is False and st["real"]["orders"] == n


def test_real_settle_loss_stops(tmp_path):
    c = FakeClient()
    w, fs, clock, alerts = live(tmp_path, c)
    wk = release(w, fs, clock)
    bad = w.real[0]["ticker"]
    for t in wk["takes"]:
        fs.results[t["ticker"]] = t["side"]
    fs.results[bad] = "yes"                  # our NO leg loses
    clock[0] = ts(2026, 11, 24, 9, 30)
    for _ in range(10):
        w.step(clock[0])
    assert w.stopped and "lost" in w.stopped["why"]
    assert w.real_mode() == "stopped"
    assert any("stopped" in a[0] for a in alerts)
    # raising the control epoch resumes
    fs.control["epoch"] = 1
    w.read_control(clock[0])
    assert w.stopped is None and w.real_mode() == "live"


def test_kalshi_value_mismatch_stops(tmp_path):
    c = FakeClient()
    w, fs, clock, _ = live(tmp_path, c)
    wk = release(w, fs, clock)
    for t in wk["takes"]:
        fs.results[t["ticker"]] = t["side"]
    fs.values = {"KXPANAMAWEEKLY": "197"}
    clock[0] = ts(2026, 11, 24, 9, 30)
    for _ in range(10):
        w.step(clock[0])
    assert w.stopped and "Kalshi settled Panama" in w.stopped["why"]


def test_kill_switch_and_control_off(tmp_path):
    c = FakeClient()
    w, fs, clock, _ = live(tmp_path, c)
    fs.switch = {"mode": "off"}
    release(w, fs, clock)
    assert c.orders == []
    c2 = FakeClient()
    w2, fs2, clock2, _ = live(tmp_path / "b", c2, mode="off")
    release(w2, fs2, clock2)
    assert c2.orders == []
    c3 = FakeClient()
    w3, fs3, clock3, _ = live(tmp_path / "c", c3)
    fs3.switch = {"mode": "live", "halted": {"live": True}}
    release(w3, fs3, clock3)
    assert c3.orders == []


def test_rules_mismatch_and_refusal_stop(tmp_path):
    c = FakeClient()
    w, fs, clock, _ = live(tmp_path, c)
    fs.rules = "If the number of transit calls ... from Nov 9, 2026 to Nov 15, 2026"
    release(w, fs, clock)
    assert c.orders == [] and "rules" in w.stopped["why"]
    c2 = FakeClient(refuse=True)
    w2, fs2, clock2, _ = live(tmp_path / "b", c2)
    release(w2, fs2, clock2)
    assert len(c2.orders) == 1 and "refused" in w2.stopped["why"]


def test_history_outlier_skips_series(tmp_path):
    c = FakeClient()
    w, fs, clock, _ = live(tmp_path, c)
    for i in range(7):                       # Suez this week 7 x 10 = 70 vs 490 normal
        fs.override[str(date(2026, 11, 22) - sr.timedelta(days=i))] = {"chokepoint1": 10}
    release(w, fs, clock)
    assert not any(o[0].startswith("KXSUEZ") for o in c.orders) and c.orders
    assert w.weeks["2026-11-22"]["checks"]["skip_series"] == ["KXSUEZWEEKLY"]
    assert w.stopped is None


def test_max_cost_after_fee(tmp_path):
    c = FakeClient()
    w, fs, clock, _ = live(tmp_path, c, max_cost=0.5)
    release(w, fs, clock)
    # only NO legs (ask 0.40); limit 0.43 + 2c fee = 0.45 <= 0.50; YES at 0.70 never
    assert c.orders and all(o[1] == "no" and o[2] + sr.fee1(o[2]) <= 0.5 for o in c.orders)
