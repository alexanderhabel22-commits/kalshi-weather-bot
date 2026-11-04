from datetime import date, datetime, timezone

import pytest

from helpers import ensemble_json, nws_grid, nyc_observations
from wxbot import kalshi, strategy
from wxbot.model import build_day_model
from wxbot.stations import (cli_window, climate_day_of, series_from_kalshi,
                            station_from_settlement_url)
from wxbot.weather import parse_ensemble, parse_metar, parse_nws_grid, parse_observations

UTC = timezone.utc


# ---------------------------------------------------------------- Kalshi parsing

@pytest.mark.parametrize("m,expected", [
    ({"strike_type": "greater", "floor_strike": 72, "yes_sub_title": "73° or above"}, (73, None)),
    ({"strike_type": "less", "cap_strike": 65, "yes_sub_title": "64° or below"}, (None, 64)),
    ({"strike_type": "between", "floor_strike": 65, "cap_strike": 66, "yes_sub_title": "65° to 66°"}, (65, 66)),
    ({"yes_sub_title": "81° to 82°"}, (81, 82)),
    ({"strike_type": "greater", "floor_strike": 72}, (73, None)),
])
def test_bracket(m, expected):
    assert kalshi.parse_bracket(m) == expected


def test_bracket_mismatch_is_rejected():
    m = {"ticker": "X", "strike_type": "greater", "floor_strike": 72, "yes_sub_title": "72° or above"}
    assert kalshi.parse_bracket(m) is None


def test_event_date():
    assert kalshi.event_date("KXHIGHNY-26NOV03") == date(2026, 11, 3)
    assert kalshi.event_date("KXLOWTCHI-27JAN09") == date(2027, 1, 9)
    assert kalshi.event_date("GOVPARTYNY-26") is None


def test_prices_both_formats():
    assert kalshi.price({"yes_ask_dollars": "0.9400"}, "yes_ask") == pytest.approx(0.94)
    assert kalshi.price({"yes_ask": 94, "response_price_units": "usd_cent"}, "yes_ask") == pytest.approx(0.94)
    assert kalshi.price({}, "yes_ask") is None
    assert not kalshi.tradable(1.0) and not kalshi.tradable(0.0) and kalshi.tradable(0.5)


def test_fees_match_kalshi_table():
    assert kalshi.taker_fee(100, 0.55) == 1.74   # Kalshi's published example
    assert kalshi.taker_fee(100, 0.10) == 0.63
    assert kalshi.taker_fee(1, 0.50) == 0.02
    assert kalshi.taker_fee(1, 0.95) == 0.01


def test_orderbook_and_depth():
    cents = kalshi.parse_orderbook({"orderbook": {"yes": [[5, 100], [4, 200]], "no": [[90, 50]]}})
    dollars = kalshi.parse_orderbook({"orderbook_fp": {"yes_dollars": [["0.05", "100"]], "no_dollars": []}})
    assert cents["yes"][0] == (0.05, 100.0)
    assert dollars["yes"][0] == (0.05, 100.0)
    # buying NO at 95¢ matches YES bids at 5¢ or more
    assert kalshi.depth_to_buy(cents, "no", 0.95) == 100
    assert kalshi.depth_to_buy(cents, "no", 0.96) == 300
    assert kalshi.depth_to_buy(cents, "yes", 0.10) == 50


# ---------------------------------------------------------------- stations

def test_cli_window_uses_standard_time_all_year():
    assert cli_window(date(2026, 11, 3), "America/New_York")[0] == datetime(2026, 11, 3, 5, tzinfo=UTC)
    # July: still 05Z (= 1 AM EDT on the clock)
    assert cli_window(date(2026, 7, 1), "America/New_York")[0] == datetime(2026, 7, 1, 5, tzinfo=UTC)
    assert cli_window(date(2026, 7, 1), "America/Phoenix")[0] == datetime(2026, 7, 1, 7, tzinfo=UTC)
    # 00:30 EDT on July 2 is still July 1's climate day
    assert climate_day_of(datetime(2026, 7, 2, 4, 30, tzinfo=UTC), "America/New_York") == date(2026, 7, 1)


def test_station_discovery():
    url = "https://forecast.weather.gov/product.php?site=LOT&product=CLI&issuedby=MDW"
    assert station_from_settlement_url(url) == "KMDW"
    si = series_from_kalshi({"ticker": "KXHIGHCHI", "frequency": "daily", "title": "Highest temperature in Chicago",
                             "settlement_sources": [{"name": "NWS", "url": url}]}, None)
    assert si and si.station.icao == "KMDW" and si.metric == "high"
    assert series_from_kalshi({"ticker": "KXTEMPNYCH"}, None) is None


# ---------------------------------------------------------------- weather parsing

def test_metar_groups():
    raw = "KNYC 031951Z 27008KT 10SM CLR 16/03 A3012 RMK AO2 SLP199 T01610028 10167 20089 53012"
    g = parse_metar(raw)
    assert g["t_c"] == pytest.approx(16.1)
    assert g["max6_c"] == pytest.approx(16.7)
    assert g["min6_c"] == pytest.approx(8.9)
    neg = parse_metar("KMSP 031951Z 00000KT M05/M10 RMK AO2 T10501100 11033 21061")
    assert neg["t_c"] == pytest.approx(-5.0) and neg["max6_c"] == pytest.approx(-3.3)


def test_observations_parse_and_qc():
    now = datetime(2026, 11, 3, 21, 30, tzinfo=UTC)
    js = nyc_observations(now)
    js["features"].append({"properties": {"timestamp": "2026-11-03T03:00:00+00:00",
                                          "temperature": {"value": 40, "qualityControl": "X"}}})
    obs = parse_observations(js)
    assert obs.temps[0].time < obs.temps[-1].time            # sorted oldest first
    assert max(o.temp_f for o in obs.temps) == pytest.approx(61.2, abs=0.1)
    assert all(o.precise for o in obs.temps)
    assert len(obs.max6) == 1 and obs.max6[0].temp_f == pytest.approx(59.6, abs=0.1)


def test_ensemble_and_grid_parse():
    ens = parse_ensemble(ensemble_json(), "x")
    assert len(ens.members) == 31 and len(ens.times) == 96
    grid = parse_nws_grid(nws_grid(), "America/New_York")
    assert grid["2026-11-03"]["high"] == pytest.approx(61, abs=0.01)
    assert grid["2026-11-04"]["low"] == pytest.approx(49, abs=0.01)


# ---------------------------------------------------------------- model

def _nyc_model(metric, day=date(2026, 11, 3), now=datetime(2026, 11, 3, 21, 30, tzinfo=UTC), nws=None):
    ws, we = cli_window(day, "America/New_York")
    obs = parse_observations(nyc_observations(now))
    ens = parse_ensemble(ensemble_json(), "x")
    return build_day_model(metric, ws, we, now, obs, ens, nws, {"samples": 20000}, seed=1)


def test_model_afternoon_high_lock():
    dm = _nyc_model("high")
    assert sum(dm.counts.values()) == dm.n
    assert dm.obs_extreme == pytest.approx(61.2, abs=0.05)
    assert dm.guaranteed == 61
    assert dm.lock_side(59, 60) == "no"
    assert dm.lock_side(None, 56) == "no"
    assert dm.lock_side(61, 62) is None
    assert dm.lock_side(65, None) is None
    assert dm.prob(61, 62) > 0.85
    assert dm.prob(None, 60) == 0.0
    assert dm.bias < 0.5   # models ran a bit warm vs this afternoon's readings


def test_model_low_lock():
    dm = _nyc_model("low")
    assert dm.obs_extreme == pytest.approx(48.0, abs=0.05)
    assert dm.guaranteed == 48
    assert dm.lock_side(None, 48) == "yes"      # "48° or below"
    assert dm.lock_side(49, 50) == "no"
    assert dm.lock_side(51, None) == "no"
    assert dm.lock_side(None, 46) is None


def test_model_day_ahead_is_wider_and_anchored():
    now = datetime(2026, 11, 3, 21, 30, tzinfo=UTC)
    no_anchor = _nyc_model("high", day=date(2026, 11, 4), now=now)
    anchored = _nyc_model("high", day=date(2026, 11, 4), now=now, nws=55.0)
    assert no_anchor.guaranteed is None and no_anchor.obs_extreme is None
    assert anchored.median < no_anchor.median           # pulled toward NWS 55
    assert no_anchor.quantile(0.9) - no_anchor.quantile(0.1) >= 4
    assert no_anchor.lock_side(None, 50) is None


def test_noisy_five_minute_readings_are_discounted():
    from helpers import metar_feature
    ws, we = cli_window(date(2026, 11, 3), "America/New_York")
    now = datetime(2026, 11, 3, 21, 30, tzinfo=UTC)
    ens = parse_ensemble(ensemble_json(), "x")

    def model_with(extra_f):
        js = nyc_observations(now)
        js["features"].append(metar_feature(datetime(2026, 11, 3, 20, 5, tzinfo=UTC), extra_f,
                                            with_tgroup=False))
        js["features"][-1]["properties"]["rawMessage"] = ""
        return build_day_model("high", ws, we, now, parse_observations(js), ens, None,
                               {"samples": 5000}, seed=1)

    small = model_with(62.6)     # 17 °C reading: within noise of the 61.2 METAR max
    assert small.guaranteed == 61 and small.obs_extreme == pytest.approx(61.2, abs=0.05)
    big = model_with(64.4)       # 18 °C reading: clearly higher, counted after a haircut
    assert big.guaranteed == 62 and big.obs_extreme == pytest.approx(63.4, abs=0.05)


def test_model_with_no_observations_never_locks():
    ws, we = cli_window(date(2026, 11, 3), "America/New_York")
    now = datetime(2026, 11, 3, 21, 30, tzinfo=UTC)
    ens = parse_ensemble(ensemble_json(), "x")
    dm = build_day_model("high", ws, we, now, None, ens, None, {"samples": 5000}, seed=1)
    assert dm.guaranteed is None and "no station observations yet" in dm.notes


# ---------------------------------------------------------------- strategy

def test_evaluate_finds_lock_and_model_edge():
    from helpers import nyc_high_markets
    dm = _nyc_model("high")
    ms = [m for m in nyc_high_markets() if m["event_ticker"] == "KXHIGHNY-26NOV03"]
    opps, rows = strategy.evaluate_event(ms, dm, series="KXHIGHNY", city="NYC", metric="high",
                                         date="2026-11-03", day_ahead=False)
    assert len(rows) == 6
    assert opps[0].kind == "lock" and opps[0].ticker.endswith("B59.5") and opps[0].side == "no"
    assert opps[0].edge == pytest.approx(0.985 - 0.95 - 0.0033, abs=0.012)
    model = [o for o in opps if o.kind == "model"]
    assert model and model[0].ticker.endswith("B61.5") and model[0].side == "yes"
    # never bet against a lock, never buy at 99¢
    assert not any(o.ticker.endswith("T57") for o in opps)


def test_kelly_and_caps():
    assert strategy.kelly_contracts(0.5, 0.6, 100, 0.25, 0.05) == 0
    n = strategy.kelly_contracts(0.995, 0.95, 35, 0.25, 0.10)
    assert n * 0.95 <= 3.5 + 1e-9 and n >= 1
    from wxbot.strategy import Opportunity
    o = Opportunity("T", "E", "S", "NYC", "high", "2026-11-03", 59, 60, "no", "lock",
                    0.95, 0.995, 0.995, 0.935, 0.04)
    strategy.size(o, 35, open_total=13.5, open_event=0, depth=None)   # 40% cap = $14
    assert o.contracts == 0
    o2 = Opportunity("T", "E", "S", "NYC", "high", "2026-11-03", 59, 60, "no", "lock",
                     0.95, 0.995, 0.995, 0.935, 0.04)
    strategy.size(o2, 35, 0, 0, depth=2)
    assert o2.contracts == 2 and o2.fee == 0.01 and o2.cost == 1.90


def _flat_model(counts, remaining_hours, metric="high"):
    from wxbot.model import DayModel
    n = sum(counts.values())
    return DayModel(metric=metric, counts=counts, n=n, remaining_hours=remaining_hours)


def test_near_lock_only_late_in_the_day_and_never_day_ahead():
    from helpers import mk
    e = "KXHIGHNY-26NOV03"
    ms = [mk(f"{e}-T64", e, "greater", floor=64, label="65° or above",
             yb=.07, ya=.09, nb=.91, na=.92)]
    dm = _flat_model({62: 980, 65: 20}, remaining_hours=6)     # 98% it stays under 65
    opps, _ = strategy.evaluate_event(ms, dm, series="KXHIGHNY", city="NYC", metric="high",
                                      date="2026-11-03", day_ahead=False)
    near = [o for o in opps if o.kind == "near"]
    assert len(near) == 1 and near[0].side == "no" and near[0].price == 0.92
    assert near[0].fair_p == pytest.approx(0.75 * 0.98 + 0.25 * 0.92, abs=1e-6)
    # too early in the day, or tomorrow's market: not a near-lock
    for hours, ahead in ((12, False), (6, True)):
        dm = _flat_model({62: 980, 65: 20}, remaining_hours=hours)
        opps, _ = strategy.evaluate_event(ms, dm, series="KXHIGHNY", city="NYC", metric="high",
                                          date="2026-11-03", day_ahead=ahead)
        assert not any(o.kind == "near" for o in opps)
    # incomplete data: locks only
    dm = _flat_model({62: 980, 65: 20}, remaining_hours=6)
    opps, _ = strategy.evaluate_event(ms, dm, series="KXHIGHNY", city="NYC", metric="high",
                                      date="2026-11-03", day_ahead=False, locks_only=True)
    assert opps == []


def test_report_keeps_near_locks_apart_from_edge():
    from wxbot import report
    base = {"mode": "shadow", "status": "won", "pnl": "0.05", "cost": "0.92", "fee": "0.01",
            "price": "0.92", "market_p": "0.91", "model_p": "0.98"}
    rows = [dict(base, kind="near"), dict(base, kind="model", status="lost", pnl="-0.50",
                                          model_p="0.60", market_p="0.40")]
    edge = "\n".join(report._shadow_section(rows, "model"))
    near = "\n".join(report._shadow_section(rows, "near"))
    assert "Record: 0-1" in edge and "Record: 1-0" in near
    assert "near-lock alerts silent" in near
