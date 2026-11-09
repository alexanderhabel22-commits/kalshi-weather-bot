from wxbot import review


def test_milestones():
    assert review.milestone(99) == 0
    assert review.milestone(299) == 100
    assert review.milestone(300) == 300
    assert review.milestone(749) == 500
    assert review.milestone(1999) == 1000
    assert review.milestone(5000) == 5000
    assert review.milestone(7499) == 5000
    assert review.milestone(7500) == 7500
    assert review.milestone(23000) == 22500


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


def test_run_milestones_then_monthly_backstop(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from wxbot import trader, checkpoint
    from wxbot.util import Config, read_json
    monkeypatch.setattr(trader, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path / "reviews")
    monkeypatch.setattr(trader, "load_all", lambda: [])
    rows = [{"mode": "live", "series": "KXBTC15M", "filled": "1", "cost": "0.85", "fee": "0",
             "pnl": "0.15" if i % 6 else "-0.85", "close": f"2026-11-09T00:{i % 60:02d}:00+00:00"}
            for i in range(120)]
    monkeypatch.setattr(checkpoint, "m15_rows", lambda: rows)
    cfg = Config()
    t0 = datetime(2026, 11, 9, 18, tzinfo=timezone.utc)
    assert review.run(cfg, t0, dry_run=False) == []          # already past 100 on day one: skipped
    st = read_json(trader.STATE)
    assert st["reviews"]["KXBTC15M"] == 100 and "KXBTC15M" in st["reviews_at"]
    rows += [dict(rows[1]) for _ in range(200)]              # reaches 320 -> the 300 review
    out = review.run(cfg, t0 + timedelta(hours=2))
    assert len(out) == 1 and out[0].endswith("KXBTC15M-300.md")
    assert review.run(cfg, t0 + timedelta(days=10)) == []    # nothing new, under a month
    out = review.run(cfg, t0 + timedelta(days=31))
    assert len(out) == 1 and out[0].endswith("KXBTC15M-monthly.md")
