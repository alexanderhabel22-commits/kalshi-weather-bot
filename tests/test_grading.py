from datetime import datetime, timezone

from helpers import FakeHttp
from wxbot import ledger, report, tracking
from wxbot.scanner import run_scan
from wxbot.util import load_config

UTC = timezone.utc
LOCK = "KXHIGHNY-26NOV03-B59.5"


def cfg_for_test():
    cfg = load_config()
    cfg.raw["markets"]["series"] = ["KXHIGHNY", "KXLOWTNYC"]
    cfg.raw["markets"]["auto_discover"] = False
    cfg.raw["model"]["samples"] = 6000
    return cfg


def scan(cfg, h, m, **kw):
    now = datetime(2026, 11, 3, h, m, tzinfo=UTC)
    return run_scan(cfg, now=now, http=FakeHttp(now, **kw))


def test_follow_up_prices_show_how_long_a_lock_lasted(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    cfg = cfg_for_test()
    scan(cfg, 21, 30)                                            # alert at 95¢
    scan(cfg, 21, 40)                                            # still 95¢
    moved = {LOCK: {"no_ask_dollars": "0.9900", "no_ask": 99}}
    scan(cfg, 21, 50, overrides=moved)                           # priced in
    scan(cfg, 22, 35, overrides=moved)
    r = next(r for r in ledger.load() if r["ticker"] == LOCK)
    assert r["avail_min"] == "10"                                 # lasted 10+ min
    assert float(r["ask_15m"]) == 0.99 and float(r["ask_60m"]) == 0.99
    assert float(r["last_ask"]) == 0.99


def test_lock_census_records_every_decided_bracket(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    cfg = cfg_for_test()
    scan(cfg, 21, 30)
    day = tracking.load_census()["2026-11-03"]
    assert day[f"{LOCK}|no"]["first_ask"] == 0.95
    assert day["KXHIGHNY-26NOV03-T57|no"]["first_ask"] == 0.99   # already priced in
    assert "KXLOWTNYC-26NOV03-T49|yes" in day
    assert not any(k.startswith("KXHIGHNY-26NOV04") for k in day)   # tomorrow: nothing decided


def test_forecasts_logged_every_3h_and_graded(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    cfg = cfg_for_test()
    scan(cfg, 21, 30)
    n1 = len(tracking.load_forecasts())
    scan(cfg, 21, 40)                                            # same 3-hour block
    assert len(tracking.load_forecasts()) == n1 == 3             # NYC high x2 days + low
    morning = datetime(2026, 11, 4, 14, 0, tzinfo=UTC)
    settled = {"KXHIGHNY-26NOV03-T57": {"result": "no", "expiration_value": "61"},
               "KXLOWTNYC-26NOV03-T47": {"result": "no", "expiration_value": "48"}}
    run_scan(cfg, now=morning, http=FakeHttp(morning, settled=settled))
    rows = tracking.load_forecasts()
    graded = {r["event"]: r["actual"] for r in rows if r["actual"]}
    assert graded == {"KXHIGHNY-26NOV03": "61", "KXLOWTNYC-26NOV03": "48"}


def test_report_grades_everything(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/kalshi-weather-bot")
    cfg = cfg_for_test()
    cfg.raw["alerts"]["edge_alerts"] = "shadow"
    scan(cfg, 21, 30)
    scan(cfg, 21, 50)
    # you took the high-temp lock: 3 NO at 95¢
    report.MY_TRADES.write_text("ticker,side,contracts,price,note\n"
                                f"{LOCK},no,3,0.95,took it\n", encoding="utf-8")
    rows = ledger.load()
    settled = {r["ticker"]: {"result": r["side"], "expiration_value": "61"} for r in rows}
    settled["KXHIGHNY-26NOV03-T57"] = {"result": "no", "expiration_value": "61"}
    settled["KXLOWTNYC-26NOV03-T47"] = {"result": "no", "expiration_value": "48"}
    evening = datetime(2026, 11, 5, 2, 15, tzinfo=UTC)          # Nov 4, 9:15 PM ET
    http = FakeHttp(evening, settled=settled)
    run_scan(cfg, now=evening, http=http)
    text = report.REPORT.read_text(encoding="utf-8")
    assert "## Alerts sent to your phone" in text and "**Record: 3-0**" in text
    assert "## Your actual trades" in text and "**1-0**, +$0.14" in text      # 3 - 2.85 - 0.01
    assert "## Lock census" in text and "## Silent EDGE test" in text
    assert "## Forecast accuracy" in text and "| 6–12h out | 2 | +0.0°F" in text
    assert "Still buyable at the alert price **15 min later: 100%**" in text
    summary = next(p for p in http.posts if p["title"] == "Kalshi bot: daily money summary")
    assert summary["click"] == "https://github.com/someone/kalshi-weather-bot/blob/main/data/REPORT.md"
