from datetime import date, timedelta

import pytest

from wxbot import backtest as bt


def test_bracket_probs_partition_to_one():
    mu, sd = 61.3, 1.8
    parts = [(None, 56), (57, 58), (59, 60), (61, 62), (63, 64), (65, None)]
    assert sum(bt.bracket_prob(mu, sd, lo, hi) for lo, hi in parts) == pytest.approx(1.0)
    assert bt.bracket_prob(mu, sd, 61, 62) > bt.bracket_prob(mu, sd, 65, None)


def _day(i, actual, fc):
    return bt.Day("KXHIGHNY", "NYC", "high", date(2026, 9, 1) + timedelta(days=i), actual,
                  {"gfs": {"d1": fc}}, [])


def test_predict_is_walk_forward_and_learns_bias():
    # model always runs 2F cold; the fit must learn that from earlier days only
    days = [_day(i, 70 + (i % 3), 68 + (i % 3)) for i in range(30)]
    mu, sd = bt.predict(days, days[29], "d1")
    assert mu == pytest.approx(days[29].actual, abs=0.01)
    assert sd >= 1.2                                       # spread floor
    assert bt.predict(days, days[5], "d1") is None          # not enough history yet
    # changing a future day can't change today's forecast
    days2 = days[:20] + [_day(i, 99, 50) for i in range(20, 30)]
    assert bt.predict(days2, days2[19], "d1") == bt.predict(days, days[19], "d1")
