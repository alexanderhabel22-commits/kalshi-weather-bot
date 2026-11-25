from datetime import date, datetime, timezone

from wxbot.strats import gas_fav_paper as g

UTC = timezone.utc
P = dict(g.DEFAULTS)


def mkt(st="NC", day="26NOV26", b="LD", ask=0.85, status="active"):
    return {"ticker": f"KXAAAGASCD{st}-{day}-{b}", "event_ticker": f"KXAAAGASCD{st}-{day}",
            "status": status, "yes_ask_dollars": f"{ask:.4f}", "yes_bid_dollars": f"{ask - 0.01:.4f}",
            "close_time": "2026-12-03T15:00:00Z", "volume_fp": "300"}


def test_window_and_target_day():
    assert g.target_day(datetime(2026, 11, 26, 4, 30, tzinfo=UTC), P) == date(2026, 11, 26)   # 11:30 PM ET Nov 25
    assert g.target_day(datetime(2026, 11, 26, 7, 0, tzinfo=UTC), P) == date(2026, 11, 26)    # 2 AM ET Nov 26
    assert g.target_day(datetime(2026, 11, 26, 9, 0, tzinfo=UTC), P) is None                  # 4 AM ET
    assert g.target_day(datetime(2026, 11, 25, 20, 0, tzinfo=UTC), P) is None                 # 3 PM ET


def test_tickers_cover_all_states():
    t = g.tickers_for(date(2026, 11, 26))
    assert len(t) == 250 and "KXAAAGASCDNC-26NOV26-LD" in t and len(set(t)) == 250


def test_buys_favorite_in_band_only():
    now = datetime(2026, 11, 26, 5, 0, tzinfo=UTC)                                           # midnight ET
    ms = [mkt(ask=0.85), mkt(b="SD", ask=0.12), mkt(st="TX", ask=0.98), mkt(st="NY", ask=0.70),
          mkt(st="CA", day="26NOV25", ask=0.85), mkt(st="FL", ask=0.90, status="closed")]
    out = g.candidates(ms, now, P)
    assert [i.ticker for i in out] == ["KXAAAGASCDNC-26NOV26-LD", "KXAAAGASCDNY-26NOV26-LD"]
    assert all(i.side == "yes" and i.taker and i.count == 1 and i.strategy == "gas_fav_paper" for i in out)
    assert out[0].price == 0.85


def test_one_entry_per_market_and_no_orders_outside_window():
    now = datetime(2026, 11, 26, 5, 0, tzinfo=UTC)
    held = {"KXAAAGASCDNC-26NOV26": {"KXAAAGASCDNC-26NOV26-LD"}}
    assert g.candidates([mkt()], now, P, held) == []
    assert g.candidates([mkt()], datetime(2026, 11, 26, 15, 0, tzinfo=UTC), P) == []


def test_config_is_paper():
    import tomllib
    c = tomllib.load(open("config.toml", "rb"))["trading"]["gas_fav_paper"]
    assert c["live"] is False and c["enabled"] is True
