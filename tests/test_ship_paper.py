from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from wxbot.strats import ship_paper as sp
from wxbot.util import HttpError

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 17, 0, tzinfo=UTC)          # Fri Nov 20, 12 PM ET
P = dict(sp.DEFAULTS)


def flat(n, last=date(2026, 11, 15), days=30):
    return {last - timedelta(days=i): n for i in range(days)}


def mkt(series="KXSUEZWEEKLY", end="26NOV22", k=320, bid=0.33, ask=0.34, st="greater"):
    return {"ticker": f"{series}-{end}-T{k}", "event_ticker": f"{series}-{end}", "strike_type": st,
            "floor_strike": k, "yes_bid_dollars": f"{bid:.4f}", "yes_ask_dollars": f"{ask:.4f}",
            "close_time": "2026-11-24T14:00:00Z", "volume_fp": "1000"}


def test_nb_matches_scipy_values():
    # values from scipy.stats.nbinom(r, r/(r+mu)).sf(need), as used in the replay
    assert abs(sp.nb_sf(14, 38.913, 12.0) - 0.2504748098) < 1e-9
    assert abs(sp.nb_sf(300, 1849.688, 310.0) - 0.6881265230) < 1e-9
    assert abs(sp.nb_sf(5, 2.0, 3.0) - 0.1586304) < 1e-9
    assert sp.nb_sf(-1, 5, 3) == 1.0 and sp.nb_sf(3, 5, 0) == 0.0
    assert sp.fee1(0.34) == 0.02 and sp.fee1(0.97) == 0.01


def test_forecast_uses_last_14_published_days():
    daily = flat(40)                                     # 40/day -> forecast 280 for the week
    p = sp.p_over("KXSUEZWEEKLY", 320, daily, date(2026, 11, 22))
    assert abs(p - sp.nb_sf(320, sp.R["KXSUEZWEEKLY"], 280.0)) < 1e-12
    assert p < 0.05
    # days of the week already published count as known
    end = date(2026, 11, 15)
    assert sp.p_over("KXSUEZWEEKLY", 250, daily, end) == 1.0          # 7 x 40 = 280 > 250, all known
    assert sp.p_over("KXSUEZWEEKLY", 290, daily, end) == 0.0


def test_buys_no_when_yes_overpriced_and_yes_when_cheap():
    c = {"KXSUEZWEEKLY": flat(40)}                       # forecast 280 -> "> 320" very unlikely
    out = sp.candidates([mkt(k=320, bid=0.33, ask=0.34)], NOW, c, P)
    assert len(out) == 1 and out[0].side == "no" and out[0].price == 0.67 and out[0].taker
    assert out[0].count == 1 and out[0].strategy == "ship_paper"
    c = {"KXSUEZWEEKLY": flat(50)}                       # forecast 350 -> "> 320" very likely
    out = sp.candidates([mkt(k=320, bid=0.33, ask=0.34)], NOW, c, P)
    assert len(out) == 1 and out[0].side == "yes" and out[0].price == 0.34


def test_skips_fair_band_other_types_after_week_and_held():
    c = {"KXSUEZWEEKLY": flat(320 / 7)}
    p = sp.p_over("KXSUEZWEEKLY", 320, c["KXSUEZWEEKLY"], date(2026, 11, 22))
    a = round(p, 2)
    assert sp.candidates([mkt(bid=a - 0.01, ask=a + 0.01)], NOW, c, P) == []      # fair price: no edge
    c = {"KXSUEZWEEKLY": flat(40)}
    assert sp.candidates([mkt(st="between")], NOW, c, P) == []
    assert sp.candidates([mkt(bid=0.01, ask=0.02)], NOW, c, P) == []             # NO 99c outside band
    late = datetime(2026, 11, 23, 15, 0, tzinfo=UTC)                              # Monday after the week
    assert sp.candidates([mkt(bid=0.33, ask=0.34)], late, c, P) == []
    held = {"KXSUEZWEEKLY-26NOV22": {"KXSUEZWEEKLY-26NOV22-T320"}}
    assert sp.candidates([mkt(bid=0.33, ask=0.34)], NOW, c, P, held) == []
    assert sp.candidates([mkt(series="KXOTHER")], NOW, c, P) == []


class FakeHttp:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    def get_json(self, url, params=None):
        self.calls += 1
        if self.fail:
            raise HttpError("down")
        feats = [{"attributes": {"date": (date(2026, 11, 15) - timedelta(days=i)).isoformat(), "n_total": 40}}
                 for i in range(20)]
        return {"features": feats}


def test_portwatch_cache_and_staleness():
    st = {}
    ctx = SimpleNamespace(now=NOW, state=st, http=FakeHttp())
    c = sp.counts(ctx, P)
    assert c["KXSUEZWEEKLY"][date(2026, 11, 15)] == 40 and ctx.http.calls == 4
    sp.counts(ctx, P)
    assert ctx.http.calls == 4                            # cached for refresh_hours
    # PortWatch down later: the cache still works while fresh enough
    later = SimpleNamespace(now=NOW + timedelta(hours=5), state=st, http=FakeHttp(fail=True))
    assert sp.counts(later, P) is not None
    # too old (newest day > 16 days back): no orders
    old = SimpleNamespace(now=NOW + timedelta(days=20), state=st, http=FakeHttp(fail=True))
    assert sp.counts(old, P) is None
    assert sp.counts(SimpleNamespace(now=NOW, state={}, http=FakeHttp(fail=True)), P) is None
