import json
from datetime import datetime, timezone

from wxbot import quakefast as qf

UTC = timezone.utc
T0 = datetime(2026, 11, 21, 0, 9, 19, tzinfo=UTC).timestamp()     # Peru M6.7, Nov 20 7:09 PM ET

GFZ_RSS = """<rss><channel>
<item><title>M 6.7, Central Peru</title>
<description>2026-11-21 00:09:08  -10.74   -75.95    10 km    C</description>
<guid isPermaLink="false">gfz2026wtzh</guid></item>
<item><title>M 4.9, Reykjanes Ridge</title>
<description>2026-11-21 01:26:20  58.41   -31.69    10 km    A</description>
<guid isPermaLink="false">gfz2026wubv</guid></item></channel></rss>"""

PTWC = """<feed><entry><title>CENTRAL PERU</title><updated>2026-11-21T00:19:13Z</updated>
<geo:lat>-10.98</geo:lat><geo:long>-76.319</geo:long>
<summary><strong>Preliminary Magnitude: </strong>6.9(Mwp)<br/></summary>
<id>urn:uuid:ce02</id><link rel="related" href="https://www.tsunami.gov/events/PHEB/2026/11/21/26325050/1/WEGM42/PHEBCAP.xml"/>
</entry></feed>"""

EMSC_WS = json.dumps({"action": "create", "data": {"type": "Feature", "id": "20261121_0000007",
                      "properties": {"unid": "20261121_0000007", "time": "2026-11-21T00:09:21.4Z",
                                     "lat": -10.9155, "lon": -75.9885, "mag": 6.8, "magtype": "mw",
                                     "flynn_region": "CENTRAL PERU"}}})


def test_parsers():
    g = qf.parse_gfz_rss(GFZ_RSS)
    assert [(d.sid, d.mag) for d in g] == [("gfz2026wtzh", 6.7), ("gfz2026wubv", 4.9)]
    assert abs(g[0].t - (T0 - 11)) < 1 and g[0].lat == -10.74
    p = qf.parse_tsunami_atom(PTWC, "ptwc")
    assert p[0].mag == 6.9 and p[0].t is None and p[0].issued is not None and p[0].magtype == "Mwp"
    e = qf.parse_emsc_ws(EMSC_WS)
    assert e[0].src == "emsc_ws" and e[0].mag == 6.8
    assert qf.parse_emsc_ws("not json") == []
    txt = "#EventID|Time|...\ngfz2026wtzh|2026-11-21T00:09:08.79|-10.74|-75.954|10.0|||GFZ|gfz2026wtzh|Mw|6.66||Central Peru|earthquake\n"
    assert qf.parse_gfz_text(txt)[0].mag == 6.66
    jma = [{"eid": "1", "mag": "6.1", "at": "2026-11-21T06:22:00+09:00", "cod": "+32.4+130.5+0/", "en_anm": "Kumamoto"}]
    j = qf.parse_jma(jma)[0]
    assert j.lat == 32.4 and j.lon == 130.5 and j.t == datetime(2026, 11, 20, 21, 22, tzinfo=UTC).timestamp()
    b = qf.parse_bmkg({"Infogempa": {"gempa": {"DateTime": "2026-11-20T17:50:26+00:00", "Coordinates": "-7.51,106.85",
                                                "Magnitude": "5.9", "Wilayah": "x"}}})
    assert b[0].mag == 5.9 and b[0].lat == -7.51


def test_helpers():
    assert qf.fee1(0.19) == 0.02 and qf.fee1(0.95) == 0.01
    assert qf.ask_depth({"orderbook_fp": {"no_dollars": [["0.70", "5"], ["0.81", "50.00"]]}}) == (0.19, 50.0)
    assert qf.ask_depth({}) == (None, None)
    m = {"ticker": "KXBIGGESTQUAKE-21NOV26-6.6", "floor_strike": 6.6}
    assert qf.covers(m, T0) and not qf.covers({"ticker": "KXBIGGESTQUAKE-20NOV26-6.6"}, T0)
    assert qf.magnitude_market(m) and qf.strike(m) == 6.6


class FakeResp:
    def __init__(self, js, status=200):
        self._js, self.status_code, self.headers, self.content, self.text = js, status, {}, b"", ""

    def json(self):
        return self._js

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeKalshi:
    """Markets for Nov 21: asks as they stood ~3.5 min after the Peru quake."""
    def __init__(self):
        self.headers = {}
        self.asks = {"5.8": 0.61, "6.0": 0.49, "6.2": 0.33, "6.4": 0.30, "6.6": 0.19, "6.8": 0.20, "7.0": 0.09}
        self.result = {}

    def mk(self, x):
        a = self.asks[x]
        return {"ticker": f"KXBIGGESTQUAKE-21NOV26-{x}", "floor_strike": float(x), "event_ticker": "KXBIGGESTQUAKE-21NOV26",
                "yes_bid_dollars": f"{a - 0.05:.4f}", "yes_ask_dollars": f"{a:.4f}", "last_price_dollars": "0.1"}

    def get(self, url, params=None, timeout=None, headers=None):
        params = params or {}
        if url.endswith("/markets"):
            if params.get("series_ticker") == "KXBIGGESTQUAKE":
                return FakeResp({"markets": [self.mk(x) for x in self.asks]})
            return FakeResp({"markets": []})
        if url.endswith("/orderbook"):
            x = url.split("/")[-2].rsplit("-", 1)[-1]
            return FakeResp({"orderbook_fp": {"no_dollars": [[f"{1 - self.asks[x]:.4f}", "50.00"]]}})
        if "/markets/trades" in url:
            return FakeResp({"trades": []})
        tk = url.rsplit("/", 1)[-1]
        return FakeResp({"market": {"ticker": tk, "result": self.result.get(tk, "")}})


def mk(tmp_path, clock):
    return qf.QuakeFast(tmp_path, feeds=[], clock=lambda: clock[0], session=FakeKalshi(), ws=False)


def test_paper_fills_match_and_settle(tmp_path):
    clock = [T0 - 600]
    w = mk(tmp_path, clock)
    # start-up polls (nothing big) warm both feeds up
    w.on_feed("gfz", clock[0], [], 200, 0.1, None)
    w.on_feed("emsc_ws", clock[0], [], 200, 0, None)
    clock[0] = T0 + 207                                   # GFZ at +3.45 min, M6.66
    d = qf.Det("gfz", "gfz2026wtzh", T0 - 11, -10.74, -75.95, 6.66, "Central Peru")
    w.on_feed("gfz", clock[0], [d], 200, 0.1, None)
    assert len(w.quakes) == 1
    f = {(x["strike"], x["margin"]): x for x in w.fills if x["src"] == "gfz"}
    assert set(f) == {(5.8, 0.0), (6.0, 0.0), (6.2, 0.0), (6.4, 0.0), (6.6, 0.0),
                      (5.8, 0.2), (6.0, 0.2), (6.2, 0.2), (6.4, 0.2)}
    assert f[(6.6, 0.0)]["ask"] == 0.19 and f[(6.6, 0.0)]["ask_size"] == 50.0 and f[(6.6, 0.0)]["rank"] == 1
    assert abs(f[(6.6, 0.0)]["lag_s"] - 218) < 1
    # EMSC says 6.8 a bit later: same quake, its own fills (6.8 strike too), rank 2
    clock[0] = T0 + 240
    w.on_feed("emsc_ws", clock[0], qf.parse_emsc_ws(EMSC_WS), 200, 0, None)
    assert len(w.quakes) == 1 and set(w.quakes[0].srcs) == {"gfz", "emsc_ws"}
    e68 = [x for x in w.fills if x["src"] == "emsc_ws" and x["strike"] == 6.8]
    assert len(e68) == 1 and e68[0]["rank"] == 2 and e68[0]["margin"] == 0.0
    # repeat poll: no duplicate fills
    n = len(w.fills)
    w.on_feed("gfz", clock[0], [d], 200, 0.1, None)
    assert len(w.fills) == n
    # settle: USGS 6.7 -> everything up to 6.6 YES, 6.8 NO
    k = w.s
    for x in k.asks:
        k.result[f"KXBIGGESTQUAKE-21NOV26-{x}"] = "yes" if float(x) <= 6.6 else "no"
    clock[0] = T0 + 3600
    w.settle(clock[0])
    st = w.status(clock[0])
    g66 = next(x for x in w.fills if x["src"] == "gfz" and x["strike"] == 6.6)
    assert g66["status"] == "settled" and abs(g66["pnl"] - (1 - 0.19 - 0.02)) < 1e-9
    lost = [x for x in w.fills if x["strike"] == 6.8]
    assert all(x["pnl"] < 0 for x in lost)
    assert st["paper_only"] and st["by_source"]["gfz|0.0"]["settled"] == 5
    assert st["first_source"]["n"] == 9
    # state survives a restart
    w.save()
    w2 = mk(tmp_path, clock)
    assert len(w2.fills) == len(w.fills) and w2.quakes[0].qid == w.quakes[0].qid


def test_startup_and_stale_quakes_never_trade(tmp_path):
    clock = [T0 + 4000]
    w = mk(tmp_path, clock)
    d = qf.Det("gfz", "x", T0, -10.7, -75.9, 6.9, "Peru")
    w.on_feed("gfz", clock[0], [d], 200, 0.1, None)       # first poll after start-up = seed only
    assert w.quakes[0].seeded and w.fills == []
    w2 = mk(tmp_path / "b", clock)
    w2.on_feed("gfz", clock[0], [], 200, 0.1, None)
    w2.on_feed("gfz", clock[0], [d], 200, 0.1, None)      # warm, but 67 min old: too stale
    assert w2.fills == [] and not w2.quakes[0].seeded
    # small quakes never trigger
    w2.on_feed("gfz", clock[0], [qf.Det("gfz", "y", clock[0] - 60, 40.0, 140.0, 5.6)], 200, 0.1, None)
    assert w2.fills == []


def test_main_drops_secrets(monkeypatch, tmp_path):
    seen = {}

    class Stub:
        def __init__(self, out, **kw):
            seen["env"] = {k for k in __import__("os").environ if k.startswith("KALSHI")}
            seen["client"] = kw.get("client")

        def run(self):
            pass
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", "secret")
    monkeypatch.setenv("QUAKEFAST_REAL", "0")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("QUAKEFAST_DIR", str(tmp_path))
    monkeypatch.setattr(qf, "QuakeFast", Stub)
    assert qf.main() == 0 and seen["env"] == set() and seen["client"] is None


class FakeClient:
    def __init__(self, fill=1.0, fail=False):
        self.orders, self.fill, self.fail = [], fill, fail

    def buy_now(self, ticker, side, price, count, coid):
        if self.fail:
            raise RuntimeError("insufficient balance")
        self.orders.append((ticker, side, price, count))
        return {"order_id": f"o{len(self.orders)}", "fill_count_fp": f"{self.fill:.2f}"}


def live(tmp_path, clock, client, **cfg):
    pushes = []
    w = qf.QuakeFast(tmp_path, feeds=[], clock=lambda: clock[0], session=FakeKalshi(), ws=False,
                     client=client, alert=lambda t, b: pushes.append(t))
    w.rcfg = {**qf.REAL_DEFAULTS, "mode": "live", **cfg}
    w.switch = {"mode": "live", "halted": False}
    w.ok_at = clock[0]
    for f in ("gfz", "emsc_ws", "ptwc"):
        w.on_feed(f, clock[0], [], 200, 0.1, None)
    return w, pushes


def gfz_peru(w, clock, mag=6.66):
    clock[0] = T0 + 207
    w.on_feed("gfz", clock[0], [qf.Det("gfz", "g", T0 - 11, -10.74, -75.95, mag, "Central Peru")], 200, 0.1, None)


def test_real_orders_follow_the_rails(tmp_path):
    clock = [T0 - 600]
    c = FakeClient()
    w, _ = live(tmp_path, clock, c)
    gfz_peru(w, clock)
    # M6.66: +0.2 clears 5.8/6.0/6.2/6.4; asks 61/49/33/30 -> 5.8 (61c) is over the 60c limit
    assert sorted(o[0].rsplit("-", 1)[-1] for o in c.orders) == ["6.0", "6.2", "6.4"]
    assert all(o[1] == "yes" and o[3] == 1 for o in c.orders)
    assert {o[2] for o in c.orders} == {0.49, 0.33, 0.30}
    assert abs(w.cap_used() - (0.49 + 0.02 + 0.33 + 0.02 + 0.30 + 0.02)) < 1e-9
    # EMSC sees the same quake: no second order on markets we already bought
    n = len(c.orders)
    clock[0] = T0 + 240
    w.on_feed("emsc_ws", clock[0], qf.parse_emsc_ws(EMSC_WS), 200, 0, None)
    assert [o[0] for o in c.orders[n:]] == ["KXBIGGESTQUAKE-21NOV26-6.6"]   # 6.8 >= 6.6 + 0.2, ask 19c
    # PTWC is not a real-money source
    assert all(r["src"] in ("gfz", "emsc_ws") for r in w.real)


def test_real_cap_counts_open_bets_and_pushes_once(tmp_path):
    clock = [T0 - 600]
    c = FakeClient()
    w, pushes = live(tmp_path, clock, c, max_loss_usd=0.87)
    gfz_peru(w, clock)
    # 6.0 at 49c+2c fee fits; 6.2 (35c) makes 0.86, fits; 6.4 (32c) -> 1.18 > 0.87 skipped
    assert len(c.orders) == 2 and w.cap_used() <= 0.87
    # both lose -> settled loss 0.86 leaves no room: one push, nothing more
    for r in w.real:
        w.s.result[r["ticker"]] = "no"
    clock[0] = T0 + 3600
    w.settle_real(clock[0])
    w.settle_real(clock[0] + 400)
    assert pushes == ["Quake bot: $0.87 loss cap used up"] and w.cap_alerted
    st = w.status(clock[0])
    assert st["real"]["settled"] == 2 and st["real"]["pnl"] == -0.86


def test_real_off_switches_and_misses(tmp_path):
    clock = [T0 - 600]
    c = FakeClient()
    w, _ = live(tmp_path, clock, c)
    w.switch = {"mode": "off", "halted": False}            # TRADING_MODE=off
    gfz_peru(w, clock)
    assert c.orders == [] and w.real == []
    w2, _ = live(tmp_path / "b", [T0 - 600], c)
    w2.ok_at = -1e18                                       # control unread for 30+ min
    gfz_peru(w2, [T0])
    assert c.orders == []
    c0 = FakeClient(fill=0.0)                              # IOC got nothing: no chase, no cost
    w3, _ = live(tmp_path / "c", [T0 - 600], c0)
    gfz_peru(w3, [T0])
    assert c0.orders and all(r["status"] == "missed" for r in w3.real) and w3.cap_used() == 0
    cf = FakeClient(fail=True)
    w4, p4 = live(tmp_path / "d", [T0 - 600], cf)
    gfz_peru(w4, [T0])
    assert all(r["status"] == "error" for r in w4.real) and p4 == ["Quake bot: Kalshi refused an order"]
    w5, _ = live(tmp_path / "e", [T0 - 600], None)         # no key on the server: paper only
    gfz_peru(w5, [T0])
    assert w5.real == [] and w5.real_mode() == "off"
