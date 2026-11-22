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

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        if url.endswith("/query"):
            return Resp({"features": [{"attributes": a} for a in pw_rows(self.end, self.days)]})
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
                           "close_time": "2026-11-24T14:00:00Z", "result": self.results.get(t, ""),
                           "expiration_value": "490" if self.results else ""})
            return Resp({"markets": ms})
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
