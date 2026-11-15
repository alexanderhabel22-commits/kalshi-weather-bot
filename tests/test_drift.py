from wxbot import drift


def units(n, c):
    return [{"n": 1, "pnl": c / 100, "cost": 0.8, "at": ""} for _ in range(n)]


def test_too_few_events_never_flags():
    assert drift.assess(units(10, -50), 5.0)["level"] == "too_few"


def test_levels():
    good = units(15, 10) + units(15, -5)            # mean +2.5c, noisy
    assert drift.assess(good, 2.0)["level"] == "ok"
    low = [{"n": 1, "pnl": 0.01, "cost": 0.9, "at": ""} for _ in range(30)]   # steady +1c
    assert drift.assess(low, 8.7)["level"] == "below_replay"
    assert drift.assess(units(30, -3), 8.7)["level"] == "losing"
    assert drift.assess(units(30, -3), None)["level"] == "losing"


def test_pushes_once_per_level(monkeypatch):
    monkeypatch.setattr("wxbot.review.main_units", lambda rows, live: {"macro_fade": units(30, -3)})
    state, pushes = drift.check([], ["macro_fade"], {})
    assert len(pushes) == 1 and "losing" in pushes[0]
    state, pushes = drift.check([], ["macro_fade"], state)
    assert pushes == []
