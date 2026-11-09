from wxbot import review


def test_milestones():
    assert review.milestone(99) == 0
    assert review.milestone(299) == 100
    assert review.milestone(300) == 300
    assert review.milestone(749) == 500
    assert review.milestone(1999) == 1000
    assert review.milestone(5000) == 5000
    assert review.milestone(9999) == 5000
    assert review.milestone(10000) == 10000
    assert review.milestone(23000) == 20000


def _units(n, pnl):
    return [{"n": 1, "cost": 0.85, "pnl": pnl, "at": f"2026-11-09T{i % 24:02d}:00"} for i in range(n)]


CTX = {"bot": "15-min bot", "contracts": 1, "days": 3, "money": 20, "concurrent": 6,
       "per_market_max": 2, "share": 0.25}


def test_losing_bot_goes_back_to_paper():
    r = review.review("KXWTI15M", _units(300, -0.2), 300, CTX)
    assert r["action"] == "BACK TO PAPER" and r["rec"] == 0


def test_clear_edge_scales_up_within_cap():
    units = [{"n": 1, "cost": 0.85, "pnl": (0.15 if i % 10 else -0.85) + 0.05, "at": ""} for i in range(500)]
    r = review.review("KXBTC15M", units, 500, dict(CTX, money=2000))
    assert r["action"] == "SCALE UP"
    assert 1 <= r["rec"] <= 3                         # never more than 3x in one review
    assert r["grow"][5] > 0
    assert "SCALE UP" in review.render(r, __import__("datetime").datetime(2026, 11, 9))


def test_unclear_bot_keeps_its_size():
    units = [{"n": 1, "cost": 0.85, "pnl": 0.15 if i % 6 else -0.85, "at": ""} for i in range(300)]
    r = review.review("KXSILVER15M", units, 300, CTX)
    assert r["action"].startswith("KEEP") and r["rec"] == 1
