from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from wxbot.strats import rain_evening as re_

UTC = timezone.utc
# Nov 12, 2026 in NYC (EST = UTC-5): local midnight = 05:00Z, 8:30 PM local = 01:30Z Nov 13
SINCE = datetime(2026, 11, 12, 5, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 13, 1, 30, tzinfo=UTC)


def obs(hours=range(5, 25), rain_at=None, pgroup_at=None, skip=()):
    out = []
    for h in hours:
        if h in skip:
            continue
        t = SINCE.replace(hour=0) + timedelta(hours=h, minutes=51)
        raw = f"METAR KNYC {t:%d%H%M}Z 27008KT 10SM CLR 10/03 A3012 RMK AO2 SLP199 T01000030"
        if h == rain_at:
            raw += " P0002"
        out.append({"obsTime": t.timestamp(), "metarType": "METAR", "rawOb": raw + " $"})
    if pgroup_at:
        out.append({"obsTime": pgroup_at.timestamp(), "metarType": "SPECI",
                    "rawOb": "SPECI KNYC 122215Z 27008KT 3SM RA RMK P0001"})
    return out


def test_dry_day_with_full_reports():
    rained, why = re_.rain_so_far(obs(), SINCE, NOW)
    assert rained is False, why


def test_measurable_rain_or_p_group_counts():
    assert re_.rain_so_far(obs(rain_at=14), SINCE, NOW)[0] is True
    assert re_.rain_so_far(obs(pgroup_at=datetime(2026, 11, 12, 22, 15, tzinfo=UTC)), SINCE, NOW)[0] is True


def test_trace_is_not_rain():
    js = obs()
    js[3]["rawOb"] = js[3]["rawOb"].replace(" $", " P0000 $")
    assert re_.rain_so_far(js, SINCE, NOW)[0] is False


def test_gaps_mean_unknown():
    assert re_.rain_so_far(obs(skip={8, 9, 10, 11}), SINCE, NOW)[0] is None
    assert re_.rain_so_far(obs(skip={8, 9}), SINCE, NOW)[0] is False   # 2 missing is allowed


def test_report_before_midnight_ignored():
    js = obs()
    js.append({"obsTime": datetime(2026, 11, 12, 4, 51, tzinfo=UTC).timestamp(), "metarType": "METAR",
               "rawOb": "METAR KNYC 120451Z RMK AO2 P0010"})
    assert re_.rain_so_far(js, SINCE, NOW)[0] is False


def ctx(now=NOW, orders=()):
    return SimpleNamespace(now=now, orders=list(orders), mode="live")


def mkt(city="NYC", bid=0.12, day="26NOV12"):
    return {"ticker": f"KXRAIN-{day}-{city}", "event_ticker": f"KXRAIN-{day}", "yes_bid_dollars": str(bid),
            "yes_ask_dollars": str(bid + 0.03), "close_time": "2026-11-13T05:00:00Z", "volume_fp": "5000"}


def test_candidate_buys_no_at_ask_in_window():
    out = re_.candidates(ctx(), [mkt()], dict(re_.DEFAULTS), lambda *a: obs())
    assert len(out) == 1
    i = out[0]
    assert i.side == "no" and i.price == 0.88 and i.count == 1 and i.taker and i.strategy == "rain_evening"


def test_no_candidate_outside_band_window_or_wet():
    p = dict(re_.DEFAULTS)
    assert re_.candidates(ctx(), [mkt(bid=0.30)], p, lambda *a: obs()) == []      # NO ask 70c
    assert re_.candidates(ctx(), [mkt(bid=0.02)], p, lambda *a: obs()) == []      # NO ask 98c
    early = datetime(2026, 11, 12, 23, 30, tzinfo=UTC)                             # 6:30 PM local
    assert re_.candidates(ctx(now=early), [mkt()], p, lambda *a: obs(hours=range(5, 23))) == []
    assert re_.candidates(ctx(), [mkt()], p, lambda *a: obs(rain_at=12)) == []
    assert re_.candidates(ctx(), [mkt(city="HOU")], p, lambda *a: obs()) == []    # city left out
    assert re_.candidates(ctx(), [mkt(day="26NOV11")], p, lambda *a: obs()) == []  # not today


def test_already_held_market_skipped():
    held = [{"strategy": "rain_evening", "status": "filled", "mode": "live",
             "event": "KXRAIN-26NOV12", "ticker": "KXRAIN-26NOV12-NYC"}]
    assert re_.candidates(ctx(orders=held), [mkt()], dict(re_.DEFAULTS), lambda *a: obs()) == []


def test_every_city_has_station_and_zone():
    from zoneinfo import ZoneInfo
    for city, (icao, tz) in re_.CITIES.items():
        assert icao.startswith("K") and len(icao) == 4
        ZoneInfo(tz)
    assert re_.CITIES["CHI"][0] == "KORD" and "HOU" not in re_.CITIES
