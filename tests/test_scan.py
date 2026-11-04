from datetime import datetime, timezone

from helpers import FakeHttp
from wxbot import ledger
from wxbot.scanner import STATE, run_scan
from wxbot.util import load_config, read_json

UTC = timezone.utc


def cfg_for_test():
    cfg = load_config()
    cfg.raw["markets"]["series"] = ["KXHIGHNY", "KXLOWTNYC"]
    cfg.raw["markets"]["auto_discover"] = False
    cfg.raw["model"]["samples"] = 8000
    return cfg


def test_scan_alerts_dedupes_and_settles(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "test-topic-xyz")
    cfg = cfg_for_test()
    now = datetime(2026, 11, 3, 21, 30, tzinfo=UTC)          # 4:30 PM ET
    http = FakeHttp(now)
    out = run_scan(cfg, now=now, http=http)

    assert not out["problems"], out["problems"]
    titles = [p["title"] for p in http.posts]
    assert len(titles) == 2, titles                           # NYC high lock + NYC low lock
    assert any("LOCK: Buy NO · NYC high today · 59° to 60°" == t for t in titles)
    assert any("LOCK: Buy YES · NYC low today · 48° or below" == t for t in titles)
    high = next(p for p in http.posts if "NYC high" in p["title"])
    assert "Limit price: 95¢ or less" in high["message"] and "Paper record:" in high["message"]
    assert "can't come in below 61°F" in high["message"]
    assert high["topic"] == "test-topic-xyz"
    assert high["click"] == "https://kalshi.com/markets/kxhighny"

    rows = ledger.load()
    assert len(rows) == 2 and all(r["status"] == "open" for r in rows)
    assert all(int(r["contracts"]) >= 1 for r in rows)
    assert ledger.open_exposure(rows) <= 0.40 * cfg.bankroll

    # 10 minutes later: the same locks are not re-sent; the next-best idea
    # for each city-day (the NYC high 61-62 model call) is.
    later = datetime(2026, 11, 3, 21, 40, tzinfo=UTC)
    http2 = FakeHttp(later)
    run_scan(cfg, now=later, http=http2)
    sent2 = [p["title"] for p in http2.posts]
    assert not any("59° to 60°" in t for t in sent2)
    assert not any("48° or below" in t for t in sent2)
    assert "EDGE: Buy YES · NYC high today · 61° to 62°" in sent2
    rows = ledger.load()
    assert ledger.open_exposure(rows, "KXHIGHNY-26NOV03") <= 0.12 * cfg.bankroll + 1e-9

    # next morning the markets settle; the ledger grades them
    rows = ledger.load()
    settled = {r["ticker"]: {"result": "yes" if r["side"] == "yes" else "no", "expiration_value": "61"}
               for r in rows}
    morning = datetime(2026, 11, 4, 14, 0, tzinfo=UTC)
    http3 = FakeHttp(morning, settled=settled)
    run_scan(cfg, now=morning, http=http3)
    rows = ledger.load()
    assert all(r["status"] == "won" for r in rows)
    s = ledger.stats(rows)
    assert s["wins"] == len(rows) and s["pnl"] > 0


def test_quiet_hours_and_summary(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    cfg = cfg_for_test()
    # 2:30 AM ET -> quiet: nothing sent, nothing recorded
    quiet = datetime(2026, 11, 3, 7, 30, tzinfo=UTC)
    http = FakeHttp(quiet)
    run_scan(cfg, now=quiet, http=http)
    assert http.posts == [] and ledger.load() == []

    # 9:15 PM ET -> daily summary goes out once
    evening = datetime(2026, 11, 4, 2, 15, tzinfo=UTC)
    http = FakeHttp(evening)
    run_scan(cfg, now=evening, http=http)
    summaries = [p for p in http.posts if p["title"] == "Weather bot daily summary"]
    assert len(summaries) == 1
    assert read_json(STATE)["last_summary"] == "2026-11-03"
    http = FakeHttp(evening)
    run_scan(cfg, now=evening, http=http)
    assert not [p for p in http.posts if p["title"] == "Weather bot daily summary"]


def test_outage_alert_after_three_failed_scans(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    cfg = cfg_for_test()
    now = datetime(2026, 11, 3, 18, 0, tzinfo=UTC)
    posts = []
    for i in range(4):
        http = FakeHttp(now, kalshi_down=True)
        out = run_scan(cfg, now=now, http=http)
        posts += http.posts
        assert "could not reach Kalshi" in out["problems"]
    problems = [p for p in posts if p["title"] == "Weather bot problem"]
    assert len(problems) == 1


def test_forecast_outage_only_allows_locks(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    cfg = cfg_for_test()
    for minute in (30, 40, 50):   # several scans: the 61-62 forecast call must never appear
        now = datetime(2026, 11, 3, 21, minute, tzinfo=UTC)
        http = FakeHttp(now, ens_down=True)
        out = run_scan(cfg, now=now, http=http)
        assert all(t["title"].startswith("LOCK") for t in out["sent"]), out["sent"]
        assert any("no forecast data" in p for p in out["problems"])
    assert all(r["kind"] == "lock" for r in ledger.load())
    assert not any(e["event"].endswith("NOV04") for e in out["events"])   # no day-ahead without forecasts


def test_dry_run_saves_nothing(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    cfg = cfg_for_test()
    now = datetime(2026, 11, 3, 21, 30, tzinfo=UTC)
    http = FakeHttp(now)
    out = run_scan(cfg, now=now, http=http, dry_run=True)
    assert len(out["sent"]) == 2 and http.posts == [] and ledger.load() == []


def test_aggressive_mode_sizes_locks_bigger_only(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    now = datetime(2026, 11, 3, 21, 30, tzinfo=UTC)

    def lock_contracts(level):
        monkeypatch.setenv("RISK_LEVEL", level)
        cfg = cfg_for_test()
        assert cfg.risk_level == level
        run_scan(cfg, now=now, http=FakeHttp(now))
        rows = ledger.load()
        ledger.save([])
        from wxbot.scanner import STATE
        STATE.unlink(missing_ok=True)
        return {r["ticker"]: int(r["contracts"]) for r in rows}, cfg.strategy_params

    normal, p_normal = lock_contracts("normal")
    aggressive, p_aggr = lock_contracts("aggressive")
    t = "KXHIGHNY-26NOV03-B59.5"
    assert aggressive[t] > normal[t]
    assert aggressive[t] * 0.95 <= 0.20 * 35 + 1e-9               # 20% per-lock cap
    assert p_aggr["max_bet_frac_model"] == p_normal["max_bet_frac_model"] == 0.05
    assert p_aggr["min_edge_model"] == p_normal["min_edge_model"]
