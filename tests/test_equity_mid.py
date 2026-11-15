from wxbot.equity import side_mid


def test_closed_market_uses_last_trade_not_empty_book_mid():
    q = {"yes_bid": 0.0, "yes_ask": 1.0, "last": 0.99, "status": "closed"}
    assert side_mid(q, "yes") == 0.99
    assert side_mid(q, "no") == 0.01


def test_active_market_still_uses_mid():
    q = {"yes_bid": 0.40, "yes_ask": 0.44, "last": 0.10, "status": "active"}
    assert abs(side_mid(q, "yes") - 0.42) < 1e-9
    assert abs(side_mid(q, "no") - 0.58) < 1e-9
