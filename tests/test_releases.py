from datetime import datetime, timezone

from wxbot import releases, review


def _ts(s):
    return int(datetime.fromisoformat(s).timestamp())


def test_release_time_rounding():
    assert releases.release_ts("2026-10-14T12:29:00Z") == _ts("2026-10-14T12:30:00+00:00")
    assert releases.release_ts("2026-10-28T17:55:00Z") == _ts("2026-10-28T18:00:00+00:00")
    assert releases.release_ts("2026-10-28T23:00:00Z") is None


def test_window_kind():
    known = {_ts("2026-10-14T12:30:00+00:00"): "cpi"}
    assert releases.window_kind("2026-10-14T12:45:00+00:00", known) == "cpi"   # 8:30-8:45 ET
    assert releases.window_kind("2026-10-14T12:30:00+00:00", known) is None    # 8:15-8:30 ET
    assert releases.window_kind("2026-10-14T13:00:00+00:00", known) is None


class _Http:
    def get_json(self, url, params=None):
        if params["series_ticker"] == "KXFEDDECISION":
            return {"markets": [{"close_time": "2026-12-09T18:55:00Z"}]}
        return {"markets": []}


def test_refresh_adds_fed_and_presser(tmp_path):
    p = tmp_path / "rel.json"
    n = releases.refresh(_Http(), datetime(2026, 11, 11, tzinfo=timezone.utc), path=p)
    known = releases.load(p)
    assert n == 2
    assert known[_ts("2026-12-09T19:00:00+00:00")] == "fomc"
    assert known[_ts("2026-12-09T19:30:00+00:00")] == "fomc_presser"


def test_review_shows_paper_rule(monkeypatch):
    rel = {_ts("2026-10-14T12:30:00+00:00"): "cpi"}
    monkeypatch.setattr(releases, "load", lambda path=releases.PATH: rel)
    units = [{"n": 1, "cost": 0.85, "pnl": -0.85, "at": "2026-10-14T12:45:00+00:00"},
             {"n": 1, "cost": 0.85, "pnl": 0.15, "at": "2026-10-14T14:45:00+00:00"},
             {"n": 1, "cost": 0.85, "pnl": 0.15, "at": "2026-10-15T14:45:00+00:00"}]
    x = review.extras(units)
    lines = review._release_lines(x["release"])
    assert any("$+0.30 without release windows vs $-0.55 actual" in ln for ln in lines)
