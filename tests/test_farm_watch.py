from datetime import datetime, timezone

from research_farm import farm_watch as F

NOW = datetime(2026, 11, 21, 14, 47, tzinfo=timezone.utc)


def fake_fetch(open_by_series):
    def fetch(path):
        if path == "/series":
            return {"series": [
                {"ticker": "KXCORND", "category": "Commodities", "frequency": "daily"},
                {"ticker": "KXHOGSD", "category": "Commodities", "frequency": "daily"},
                {"ticker": "KXCORNMON", "category": "Commodities", "frequency": "monthly"},
                {"ticker": "KXUCLCORNERS", "category": "Sports", "frequency": "custom"},
            ]}
        t = path.split("series_ticker=")[1].split("&")[0]
        n = open_by_series.get(t, 0)
        return {"markets": [{"ticker": f"{t}-26NOV21-B{i}", "yes_bid_dollars": "0.40",
                             "yes_ask_dollars": "0.46", "volume_fp": "10"} for i in range(n)]}
    return fetch


def test_extra_series_only_recurring_farm_commodities():
    assert F.extra_series(fake_fetch({})("/series")["series"]) == ["KXHOGSD"]


def test_no_push_when_nothing_listed(tmp_path):
    sent = []
    st = F.run(tmp_path, NOW, fetch=fake_fetch({}), sender=lambda *a: sent.append(a) or True)
    assert sent == [] and st["listed"] == {} and "KXHOGSD" in st["open_counts"]


def test_push_once_per_series(tmp_path):
    sent = []
    snd = lambda *a: sent.append(a) or True  # noqa: E731
    F.run(tmp_path, NOW, fetch=fake_fetch({"KXCORND": 3}), sender=snd)
    assert len(sent) == 1 and "KXCORND" in sent[0][0] and "6c" in sent[0][1]
    F.run(tmp_path, NOW, fetch=fake_fetch({"KXCORND": 3}), sender=snd)
    assert len(sent) == 1                       # already reported, no second push
    F.run(tmp_path, NOW, fetch=fake_fetch({"KXCORND": 3, "KXWHEATD": 2}), sender=snd)
    assert len(sent) == 2 and "KXWHEATD" in sent[1][0] and "KXCORND" not in sent[1][0]
