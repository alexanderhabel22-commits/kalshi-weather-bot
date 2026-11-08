from datetime import datetime, timedelta, timezone

from wxbot import checkpoint
from wxbot.util import load_config

UTC = timezone.utc
NOW = datetime(2026, 11, 17, 14, 0, tzinfo=UTC)


def row(strategy, event, price, filled, won, status="settled", count=None, fee=0.0):
    pnl = filled * ((1.0 if won else 0.0) - price) - fee
    return {"strategy": strategy, "mode": "live", "event": event, "ticker": event, "side": "no",
            "price": f"{price:.2f}", "count": count or filled, "filled": filled, "status": status,
            "pnl": f"{pnl:.2f}" if status == "settled" else "", "fee": f"{fee:.2f}",
            "placed_at": (NOW - timedelta(days=5)).isoformat(), "settled_at": NOW.isoformat(),
            "note": ""}


def build(rows, bankroll=50.0):
    return checkpoint.build(load_config(), rows, NOW, {}, bankroll,
                            names=["mentions", "favorites", "macro_fade", "weather"])


def test_too_early_keeps_and_hides_range():
    out = build([row("favorites", f"F{i}", 0.90, 1, True) for i in range(4)])
    f = out["strategies"]["favorites"]
    assert f["verdict"] == "KEEP" and f["ci"] is None and "too early" in f["why"][0]


def test_clear_loser_is_killed():
    rows = [row("mentions", f"M{i}", 0.85, 1, i % 3 != 0) for i in range(30)]   # 67% hit at 85c
    m = build(rows, bankroll=1000)["strategies"]["mentions"]
    assert m["ci"][1] < 0 and m["verdict"].startswith("KILL")


def test_proven_edge_is_raised():
    rows = [row("favorites", f"F{i}", 0.80, 1, i % 20 != 0) for i in range(40)]  # 95% hit at 80c
    f = build(rows)["strategies"]["favorites"]
    assert f["ci"][0] > 0 and f["verdict"] == "RAISE"


def test_big_real_loss_cuts_even_when_early():
    rows = [row("weather", f"W{i}", 0.50, 4, False) for i in range(5)]   # -$10 on a $50 bankroll
    assert build(rows)["strategies"]["weather"]["verdict"] == "CUT"


def test_events_counted_once_and_fill_rate_includes_resting():
    rows = [row("mentions", "SPEECH", 0.80, 1, True) for _ in range(3)]
    rows += [row("mentions", "OTHER", 0.80, 0, False, status="resting", count=3)]
    m = build(rows)["strategies"]["mentions"]
    assert m["n"] == 1 and m["fills"]["offered"] == 6 and m["fills"]["rate"] == 0.5


def test_events_needed_grows_for_small_edges():
    assert checkpoint.needed(0.29, 0.04, 2.487) > 300
    assert checkpoint.needed(1.1, 0.02, 2.487) > checkpoint.needed(0.29, 0.04, 2.487) * 10


def test_allocator_switch_blocked_by_a_broken_strategy():
    rows = [row("mentions", f"M{i}", 0.85, 1, i % 3 != 0) for i in range(30)]
    out = checkpoint.build(load_config(), rows, NOW, {"multipliers": {}}, 1000,
                           names=["mentions"])
    assert out["allocator"][0] == "STAY SHADOW"
    ok = checkpoint.build(load_config(), [row("mentions", "M1", 0.85, 1, True)], NOW,
                          {"multipliers": {}}, 1000, names=["mentions"])
    assert ok["allocator"][0].startswith("SWITCH ON")


def test_render_runs():
    text = checkpoint.render(build([row("weather", f"W{i}", 0.30, 1, i % 2 == 0) for i in range(12)]))
    assert "## Verdicts" in text and "weather" in text


def m15row(series, i, won, filled=1, mode="live", price=0.85):
    close = (NOW - timedelta(minutes=15 * i)).isoformat()
    return {"series": series, "mode": mode, "close": close, "filled": filled, "cost": f"{price:.2f}",
            "fee": "0", "pnl": f"{(1 - price) if won else -price:.2f}" if filled else ""}


def test_m15_too_early_then_cut_then_raise():
    p = checkpoint.params(load_config())
    early = checkpoint.m15_build([m15row("KXBTC15M", i, True) for i in range(20)], NOW, p)
    assert early["KXBTC15M"]["verdict"] == "KEEP" and early["KXBTC15M"]["n"] == 20
    bad = [m15row("KXBTC15M", i, i % 4 != 0) for i in range(120)]          # 75% hit at 85c
    assert checkpoint.m15_build(bad, NOW, p)["KXBTC15M"]["verdict"].startswith(("KILL", "CUT"))
    good = [m15row("KXBTC15M", i, i % 50 != 0) for i in range(320)]        # 98% hit at 85c
    assert checkpoint.m15_build(good, NOW, p)["KXBTC15M"]["verdict"].startswith("RAISE")


def test_m15_ignores_paper_and_counts_fill_rate():
    p = checkpoint.params(load_config())
    rows = [m15row("KXSILVER15M", i, True, filled=i % 2) for i in range(10)]
    rows += [m15row("KXSILVER15M", 99, True, mode="paper")]
    s = checkpoint.m15_build(rows, NOW, p)["KXSILVER15M"]
    assert s["quoted"] == 10 and s["n"] == 5 and s["rate"] == 0.5
