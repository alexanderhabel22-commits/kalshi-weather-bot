from datetime import datetime, timedelta, timezone

from wxbot import launches
from wxbot.util import HttpError, load_config

UTC = timezone.utc


def base_series(n=150):
    return [{"ticker": f"KXOLD{i}", "category": "Sports", "frequency": "custom", "title": f"Old {i}"}
            for i in range(n)]


class Http:
    def __init__(self, series, markets=None):
        self.series = series
        self.markets = markets or {}
        self.posts = []

    def get_json(self, url, params=None, headers=None):
        if url.endswith("/series"):
            return {"series": self.series}
        if url.endswith("/markets"):
            return {"markets": self.markets.get(params["series_ticker"], [])}
        raise HttpError(404, url)

    def post_json(self, url, payload, headers=None):
        self.posts.append(payload)
        return {}


def mk(t, bid, ask):
    return {"ticker": t, "yes_bid_dollars": str(bid), "yes_ask_dollars": str(ask), "volume_fp": "12"}


def test_baseline_then_new_series_and_category(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    cfg = load_config()
    cfg.raw["alerts"]["launch_alerts"] = "on"
    now = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)
    db = {}
    http = Http(base_series())
    assert launches.watch(http, cfg, db, now) == []
    assert db["baseline_at"] and len(db["known"]) == 150 and not http.posts

    new = [{"ticker": "KXTEMPDFWH", "category": "Climate and Weather", "frequency": "hourly",
            "title": "Hourly DFW Temperature"},
           {"ticker": "KXNEWTHING", "category": "Brand New", "frequency": "one_off", "title": "New"},
           {"ticker": "KXQUIET", "category": "Sports", "frequency": "one_off", "title": "Quiet"}]
    http = Http(base_series() + new)
    later = now + timedelta(minutes=30)
    assert launches.watch(http, cfg, db, later) == []          # no markets open yet
    assert set(db["pending"]) == {"KXTEMPDFWH", "KXNEWTHING", "KXQUIET"}
    assert [c["category"] for c in db["new_categories"]] == ["Climate and Weather", "Brand New"]

    http.markets = {"KXTEMPDFWH": [mk("A", 0.40, 0.50), mk("B", 0.10, 0.20), mk("C", 0.3, 0.32)],
                    "KXQUIET": [mk("Q", 0.5, 0.51)]}
    ready = launches.watch(http, cfg, db, later + timedelta(minutes=30))
    assert {i["series"] for i in ready} == {"KXTEMPDFWH", "KXQUIET"}
    dfw = next(i for i in ready if i["series"] == "KXTEMPDFWH")
    assert dfw["alert_worthy"] and dfw["median_spread"] == 0.10 and "wide" in dfw["recommendation"]
    assert not next(i for i in ready if i["series"] == "KXQUIET")["alert_worthy"]
    assert len(http.posts) == 1 and "Hourly DFW" in http.posts[0]["title"]
    assert "KXNEWTHING" in db["pending"]
    text = "\n".join(launches.report_section(db))
    assert "Brand New" in text and "KXTEMPDFWH" in text


def test_shadow_mode_never_pushes_and_quiet_queues(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    cfg = load_config()
    now = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)
    db = {}
    launches.watch(Http(base_series()), cfg, db, now)
    new = [{"ticker": "KXFAST", "category": "Crypto", "frequency": "fifteen_min", "title": "Fast"}]
    http = Http(base_series() + new, {"KXFAST": [mk("F", 0.4, 0.6)]})
    launches.watch(http, cfg, db, now + timedelta(minutes=30))
    assert not http.posts and db["queue"] == []                 # default is shadow

    cfg.raw["alerts"]["launch_alerts"] = "on"
    new2 = [{"ticker": "KXFAST2", "category": "Crypto", "frequency": "daily", "title": "Fast2"}]
    http = Http(base_series() + new + new2, {"KXFAST2": [mk("G", 0.4, 0.6)]})
    launches.watch(http, cfg, db, now + timedelta(hours=1), quiet=True)
    assert not http.posts and db["queue"] == ["KXFAST2"]
    http.series = http.series + []
    launches.watch(http, cfg, db, now + timedelta(hours=2))
    assert len(http.posts) == 1 and db["queue"] == []


def test_incomplete_series_list_is_ignored():
    cfg = load_config()
    db = {}
    launches.watch(Http(base_series()), cfg, db, datetime(2026, 11, 5, tzinfo=UTC))
    assert launches.watch(Http(base_series(20)), cfg, db, datetime(2026, 11, 5, 1, tzinfo=UTC)) == []
    assert len(db["known"]) == 150
