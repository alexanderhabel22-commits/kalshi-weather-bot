from datetime import datetime, timedelta, timezone

from wxbot import allocator
from wxbot.util import load_config

UTC = timezone.utc
NOW = datetime(2026, 11, 9, 14, 0, tzinfo=UTC)
P = allocator.DEFAULTS


def row(strategy, event, price, filled, won, mode="live", ticker=None):
    pnl = filled * ((1.0 if won else 0.0) - price)
    return {"strategy": strategy, "mode": mode, "event": event, "ticker": ticker or event,
            "price": f"{price:.2f}", "filled": filled, "status": "settled", "pnl": f"{pnl:.2f}",
            "fee": "", "settled_at": NOW.isoformat()}


def test_no_record_means_normal_size():
    g = allocator.grade([], P)
    assert g["grade"] == P["hurdle"] and allocator.target(g["grade"], P) == 1.0


def test_grades_on_profit_per_dollar_not_hit_rate():
    # favorites: 90c, win 95% -> +5.6c per $1 ; longshots: 9c, win 10% -> +11c per $1
    fav = [row("favorites", f"F{i}", 0.90, 1, i % 20 != 0) for i in range(200)]
    wx = [row("weather", f"W{i}", 0.09, 1, i % 10 == 0) for i in range(200)]
    r = allocator.results(fav + wx)
    gf, gw = allocator.grade(r["favorites"], P), allocator.grade(r["weather"], P)
    assert gf["roi"] > 0.05 and gw["roi"] > 0.10
    # same count, but the longshot record is far noisier per dollar, so it is trusted less
    assert gw["trust"] < gf["trust"] / 3
    assert gf["trust"] > 0.5


def test_one_event_counts_once():
    rows = [row("mentions", "SPEECH1", 0.80, 1, False, ticker=f"SPEECH1-{w}") for w in "ABCDEFGHIJ"]
    units = allocator.results(rows)["mentions"]
    assert len(units) == 1 and abs(units[0]["pnl"] + 8.0) < 1e-9
    # correlated: variance of ten markets on one event = (10 * sd)^2
    assert abs(units[0]["var"] - 100 * 0.8 * 0.2) < 1e-9


def test_paper_ignored_by_default_and_counts_less_when_on():
    paper_rows = [row("m", f"E{i}", .8, 1, True, "paper") for i in range(30)]
    off = allocator.grade(allocator.results(paper_rows)["m"], P)
    assert off["events_paper"] == 0 and off["grade"] == P["hurdle"]      # real money only
    half = {**P, "paper_weight": 0.5}
    live = allocator.grade(allocator.results([row("m", f"E{i}", .8, 1, True) for i in range(30)])["m"], half)
    paper = allocator.grade(allocator.results(paper_rows)["m"], half)
    assert paper["trust"] < live["trust"]


def test_floor_ceiling_and_weekly_speed_limit():
    cfg = load_config()
    cfg.raw["allocator"] = {"apply": False}
    losers = [row("macro_fade", f"L{i}", 0.85, 2, i % 3 == 0) for i in range(300)]   # big losses
    store = {}
    a = allocator.update(cfg, losers, [], ["macro_fade", "weather"], NOW, store)
    assert a["grades"]["macro_fade"]["target"] == P["floor"]          # never switched off
    assert a["multipliers"]["macro_fade"] == 0.75                     # one step of 0.25
    assert a["multipliers"]["weather"] == 1.0                         # no record: unchanged
    # a day later: not due, nothing moves
    a = allocator.update(cfg, losers, [], ["macro_fade", "weather"], NOW + timedelta(days=1), store)
    assert a["multipliers"]["macro_fade"] == 0.75
    for wk in range(1, 5):
        a = allocator.update(cfg, losers, [], ["macro_fade", "weather"], NOW + timedelta(days=7 * wk), store)
    assert a["multipliers"]["macro_fade"] == P["floor"]
    assert len(store["history"]) == 5


def test_apply_scales_sizes_only_when_on():
    specs = {"weather": {"contracts": 1, "max_open_dollars": 7.5, "max_event_dollars": 7.5},
             "mentions": {"contracts": 3, "word_pct": 0.05, "max_open_dollars": 25.0}}
    alloc = {"multipliers": {"weather": 0.25, "mentions": 2.0}}
    assert allocator.apply_to(specs, alloc, {"apply": False}) is specs
    out = allocator.apply_to(specs, alloc, {"apply": True})
    assert out["weather"]["contracts"] == 1                            # never below 1 contract
    assert out["weather"]["max_open_dollars"] == 1.88
    assert out["weather"]["max_event_dollars"] == 7.5                  # event cap untouched
    assert out["mentions"]["contracts"] == 6 and abs(out["mentions"]["word_pct"] - 0.10) < 1e-9
    assert allocator.order(["weather", "mentions"], alloc, {"apply": True}) == ["mentions", "weather"]
    assert allocator.order(["weather", "mentions"], alloc, {"apply": False}) == ["weather", "mentions"]


def test_report_section_renders():
    cfg = load_config()
    a = allocator.update(cfg, [row("favorites", "F1", .9, 1, True)], [], ["favorites"], NOW, {})
    text = "\n".join(allocator.report_section(a))
    assert "shadow" in text and "favorites" in text


def m15_row(series, ticker, cost, won, mode="live", filled=1, close="2026-11-08T04:00:00+00:00"):
    pnl = filled * (1.0 if won else 0.0) - cost - 0.01
    return {"ticker": ticker, "series": series, "mode": mode, "close": close, "filled": filled,
            "cost": f"{cost:.2f}", "fee": "0.01", "won": "" if won is None else int(won), "pnl": f"{pnl:.2f}"}


def test_m15_real_fills_graded_per_coin():
    rows = [m15_row("KXBTC15M", f"BTC{i}", 0.90, i % 10 != 0) for i in range(50)]
    rows += [m15_row("KXSILVER15M", "S1", 0.80, True),
             m15_row("KXBTC15M", "P1", 0.90, True, mode="paper"),                 # paper: ignored
             m15_row("KXBTC15M", "U1", 0.90, True, filled=0),                     # unfilled: ignored
             m15_row("KXBTC15M", "O1", 0.90, None),                               # not graded yet
             m15_row("KXBTC15M", "E1", 0.90, True, close="2026-11-07T23:00:00+00:00")]  # before live start
    r = allocator.results([], [], rows)
    assert set(r) == {"m15_btc", "m15_silver"}
    assert len(r["m15_btc"]) == 50 and len(r["m15_silver"]) == 1
    assert abs(sum(u["pnl"] for u in r["m15_btc"]) - (45 * 1.0 - 50 * 0.91)) < 1e-6


def test_m15_names_follow_config():
    cfg = load_config()
    cfg.raw["m15"] = {"mode": "live", "live_series": ["KXBTC15M", "KXCOPPER15M"]}
    assert allocator.m15_names(cfg) == ["m15_btc", "m15_copper"]
    cfg.raw["m15"]["mode"] = "paper"
    assert allocator.m15_names(cfg) == []


def test_fetch_m15_reads_daily_files_and_skips_missing():
    from wxbot.util import HttpError
    cfg = load_config()

    class H:
        def __init__(self):
            self.urls = []

        def get_text(self, url):
            self.urls.append(url)
            if url.endswith("markets-2026-11-08.csv"):
                return "ticker,series,mode,close,filled,cost,fee,won,pnl\nT,KXBTC15M,live,2026-11-08T05:00:00+00:00,1,0.9,0.01,1,0.09\n"
            raise HttpError(404, url)

    h = H()
    rows = allocator.fetch_m15(h, cfg, datetime(2026, 11, 9, 12, tzinfo=UTC))
    assert len(rows) == 1 and rows[0]["series"] == "KXBTC15M"
    assert len(h.urls) == 3 and "/m15-data/m15/markets-2026-11-07.csv" in h.urls[0]
