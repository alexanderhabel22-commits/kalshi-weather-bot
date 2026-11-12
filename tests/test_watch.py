import json
from datetime import datetime, timezone
from types import SimpleNamespace

from wxbot import watch
from wxbot.util import load_config

NOW = datetime(2026, 11, 10, 22, 20, tzinfo=timezone.utc).timestamp()


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


class FakeGH:
    def __init__(self, status=None, markets="", control=None, runs=None):
        self.files = {
            ("m15/status.json", "m15-data"): json.dumps(status or {
                "at": iso(NOW - 300), "mode": "live", "control": "live", "errors": 0,
                "live_exposure": 1.5, "spot_ok": True,
                "pnl": {"live_total": -8.0, "live_days": {"2026-11-10": -14.0}},
                "series": {"KXBTC15M": {"fills": 406, "pnl": 2.47}}}),
            ("m15/markets-2026-11-10.csv", "m15-data"): markets,
            ("m15_control.json", "main"): json.dumps(control or {"mode": "live"}),
            ("data/trader_state.json", "main"): json.dumps({"mode": "live", "halted": {}}),
            ("data/bankroll.json", "main"): json.dumps({"at": iso(NOW - 120), "total": 55.0}),
        }
        self._runs = runs if runs is not None else [
            {"name": "weather-scan", "conclusion": "success", "created_at": iso(NOW - 200)},
            {"name": "trade", "conclusion": "success", "created_at": iso(NOW - 180)}]
        self.puts = []

    def text(self, path, ref="main"):
        return self.files.get((path, ref))

    def json(self, path, ref="main"):
        t = self.text(path, ref)
        return json.loads(t) if t else None

    def runs(self):
        return self._runs

    def put(self, path, text, message, branch="main"):
        self.puts.append((path, branch, text))
        self.files[(path, branch)] = text


class FakeClient:
    def __init__(self, haiku=None, opus=None):
        self.calls = []
        self.answers = {"claude-haiku-5-5": haiku or {"level": "ok", "summary": "fine"},
                        "claude-opus-5-5": opus}
        self.messages = SimpleNamespace(create=self.create)

    def create(self, model, **kw):
        # escalations run on Haiku since Nov 12; "claude-opus-5-5" here labels the escalation step
        model = "claude-opus-5-5" if kw.get("system") == watch.OPUS_SYSTEM else model
        self.calls.append(model)
        body = json.dumps(self.answers[model])
        return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=1000, output_tokens=50),
                               content=[SimpleNamespace(type="text", text=body)])


def runner(cmd, **kw):
    return SimpleNamespace(stdout="active" if "is-active" in cmd else "")


def go(tmp_path, monkeypatch, gh, client, seed=True):
    sent = []
    st = tmp_path / "w.json"
    if seed and not st.exists():          # a watcher that already ran today with no overfills
        st.write_text(json.dumps({"over_seen": {}, "over_day": "2026-11-10"}))
    monkeypatch.setattr(watch, "mem_used", lambda path="/proc/meminfo": getattr(gh, "mem", 40.0))
    monkeypatch.setattr(watch.notify, "send", lambda http, cfg, title, body, **kw: sent.append((title, body)))
    rec = watch.run(load_config(), http=object(), client=client, gh=gh, now=NOW, runner=runner,
                    state_path=tmp_path / "w.json")
    return rec, sent


def test_healthy_run_only_calls_haiku(tmp_path, monkeypatch):
    gh, c = FakeGH(), FakeClient()
    rec, sent = go(tmp_path, monkeypatch, gh, c)
    assert c.calls == ["claude-haiku-5-5"]
    assert rec["flags"] == [] and not sent and rec["action"] is None
    assert any(p[0] == "m15/watch.json" and p[1] == "m15-data" for p in gh.puts)
    assert not any(p[0] == "m15_control.json" for p in gh.puts)


def test_losses_alone_never_pause(tmp_path, monkeypatch):
    st = {"at": iso(NOW - 60), "mode": "paper", "control": "live", "roll_stop": {"at": 1},
          "errors": 0, "pnl": {"live_total": -20}}
    gh = FakeGH(status=st)
    c = FakeClient(haiku={"level": "escalate", "summary": "big loss"},
                   opus={"severity": "pause_m15", "title": "t", "message": "m", "diagnosis": "d",
                         "proposed_fix": "f", "needs_alex": False})
    rec, sent = go(tmp_path, monkeypatch, gh, c)
    assert not any(p[0] == "m15_control.json" for p in gh.puts)   # no malfunction flag -> no pause
    assert rec["action"] is None


def test_overfill_pauses_when_opus_agrees(tmp_path, monkeypatch):
    csv = f"ticker,mode,close,filled,cost,pnl\nKXBTC15M-X,live,{iso(NOW - 600)},5,4.0,-4.0\nKXBTC15M-OLD,live,{iso(NOW - 9000)},3,2.4,0\n"
    gh = FakeGH(markets=csv)
    c = FakeClient(haiku={"level": "escalate", "summary": "overfill"},
                   opus={"severity": "pause_m15", "title": "Overfill", "message": "Paused.",
                         "diagnosis": "d", "proposed_fix": "f", "needs_alex": False})
    rec, sent = go(tmp_path, monkeypatch, gh, c)
    assert rec["action"] == "paused_m15"
    assert json.loads(gh.files[("m15_control.json", "main")])["mode"] == "paper"
    assert sent and sent[0][1].startswith("PAUSED")
    # second run: the same overfill is not flagged again, so no new Opus call or push
    c.answers["claude-haiku-5-5"] = {"level": "ok", "summary": "seen"}
    rec2, sent2 = go(tmp_path, monkeypatch, gh, c)
    assert c.calls.count("claude-opus-5-5") == 1 and not sent2


def test_opus_can_veto_pause(tmp_path, monkeypatch):
    gh = FakeGH(markets="ticker,mode,filled,cost,pnl\nKXBTC15M-X,live,3,2.4,0\n")
    c = FakeClient(haiku={"level": "escalate", "summary": "x"},
                   opus={"severity": "alert", "title": "Check fills", "message": "Look.",
                         "diagnosis": "d", "proposed_fix": "f", "needs_alex": False})
    rec, sent = go(tmp_path, monkeypatch, gh, c)
    assert rec["action"] is None and sent
    assert json.loads(gh.files[("m15_control.json", "main")])["mode"] == "live"


def test_scan_gap_and_stale_status_flagged(tmp_path, monkeypatch):
    st = {"at": iso(NOW - 3600), "mode": "live", "control": "live", "errors": 0}
    gh = FakeGH(status=st, runs=[{"name": "weather-scan", "conclusion": "success",
                                  "created_at": iso(NOW - 3000)}])
    c = FakeClient(opus={"severity": "alert", "title": "Scans stopped", "message": "m",
                         "diagnosis": "d", "proposed_fix": "f", "needs_alex": True})
    rec, sent = go(tmp_path, monkeypatch, gh, c)
    codes = {f["code"] for f in rec["flags"]}
    assert {"SCAN_GAP", "M15_STATUS_STALE"} <= codes
    assert "claude-opus-5-5" in c.calls and sent and rec["action"] is None


def test_no_key_does_nothing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert watch.run(load_config()) == {"skipped": "no key"}


def test_late_rebook_flagged_once_and_history_seeded(tmp_path, monkeypatch):
    two = "ticker,mode,close,filled,cost,pnl\nKXBTC15M-A,live,2026-11-10T19:00:00Z,2,1.6,0.4\n"
    four = "ticker,mode,close,filled,cost,pnl\nKXBTC15M-A,live,2026-11-10T19:00:00Z,4,3.3,0.7\n"
    c = FakeClient(haiku={"level": "ok", "summary": "x"},
                   opus={"severity": "alert", "title": "t", "message": "m", "diagnosis": "d",
                         "proposed_fix": "f", "needs_alex": False})
    rec, _ = go(tmp_path, monkeypatch, FakeGH(markets=two), c)
    assert rec["flags"] == []
    rec, sent = go(tmp_path, monkeypatch, FakeGH(markets=four), c)   # re-booked hours after close
    assert [f["code"] for f in rec["flags"]] == ["M15_OVERFILL"] and sent
    rec, _ = go(tmp_path, monkeypatch, FakeGH(markets=four), c)
    assert rec["flags"] == []


def test_first_run_treats_existing_overfills_as_history(tmp_path, monkeypatch):
    four = "ticker,mode,close,filled,cost,pnl\nKXBTC15M-A,live,2026-11-10T19:00:00Z,4,3.3,0.7\n"
    rec, sent = go(tmp_path, monkeypatch, FakeGH(markets=four), FakeClient(), seed=False)
    assert rec["flags"] == [] and not sent


def test_ram_and_service_memory(tmp_path, monkeypatch):
    p = tmp_path / "meminfo"
    p.write_text("MemTotal: 1000000 kB\nMemFree: 50000 kB\nMemAvailable: 100000 kB\n")
    assert watch.mem_used(str(p)) == 90.0
    assert watch.svc_mem_pct("MemoryCurrent=270000000\nMemoryMax=300000000") == 90.0
    assert watch.svc_mem_pct("MemoryCurrent=[not set]\nMemoryMax=infinity") is None
    gh = FakeGH()
    gh.mem = 91.0
    c = FakeClient(opus={"severity": "alert", "title": "RAM high", "message": "m", "diagnosis": "d",
                         "proposed_fix": "f", "needs_alex": True, "notebook": ["RAM ran hot Nov 11"]})
    rec, sent = go(tmp_path, monkeypatch, gh, c)
    assert [f["code"] for f in rec["flags"]] == ["MEM_HIGH"] and sent
    assert sent[0][0].startswith("👷 Foreman: RAM high")
    st = json.loads((tmp_path / "w.json").read_text())
    assert st["notes"] == ["RAM ran hot Nov 11"]
    assert any("new: server RAM 91%" in e for e in st["journal"])
    assert any("escalated: RAM high" in e for e in st["journal"])


def test_memory_fed_back_and_streaks(tmp_path, monkeypatch):
    seen = []
    c = FakeClient(haiku={"level": "watch", "summary": "spot feed down again",
                          "remember": "Coinbase feed drops most nights ~2 AM ET; harmless"})
    orig = c.create
    c.messages.create = lambda model, **kw: (seen.append(kw["messages"][0]["content"]), orig(model, **kw))[1]
    st = {"at": iso(NOW - 60), "mode": "live", "control": "live", "errors": 0, "spot_ok": False}
    for _ in range(3):
        go(tmp_path, monkeypatch, FakeGH(status=st), c)
    assert "Coinbase feed drops most nights" in seen[-1]          # its own note comes back
    assert "M15_SPOT" in seen[-1] and "(3 checks in a row)" in seen[-1]
    state = json.loads((tmp_path / "w.json").read_text())
    assert state["notes"] == ["Coinbase feed drops most nights ~2 AM ET; harmless"]   # no dupes
    assert sum("new: Coinbase" in e for e in state["journal"]) == 1   # logged once, not every run


def test_reworded_note_replaces_old_one():
    st = {"notes": ["Coinbase feed drops at night; harmless"]}
    watch.remember(st, "KXBTC15M decay_alert has recurred on every check; keep reporting it until it clears.", 8)
    watch.remember(st, "KXBTC15M decay_alert has recurred on every 2026-11-11 check; report it until it clears.", 8)
    assert len(st["notes"]) == 2 and "2026-11-11" in st["notes"][-1]
    assert st["notes"][0].startswith("Coinbase")


def test_copycat_stuck_flagged(tmp_path, monkeypatch):
    import os
    p = tmp_path / "cc.json"
    p.write_text("{}")
    os.utime(p, (NOW - 3600, NOW - 3600))
    monkeypatch.setattr(watch, "COPYCAT_STATUS", p)
    c = FakeClient(opus={"severity": "alert", "title": "Copycat stuck", "message": "m",
                         "diagnosis": "d", "proposed_fix": "f", "needs_alex": True})
    rec, sent = go(tmp_path, monkeypatch, FakeGH(), c)
    assert "COPYCAT_STALE" in {f["code"] for f in rec["flags"]} and sent and rec["action"] is None
    os.utime(p, (NOW - 120, NOW - 120))
    assert "COPYCAT_STALE" not in {f["code"] for f in watch.rules(watch.gather(FakeGH(), NOW, runner, cc_status=p), watch.settings(load_config()))}


def lip_gh(lip=None, control=None):
    gh = FakeGH()
    gh.files[("lip_control.json", "main")] = json.dumps(control or {
        "mode": "live", "budget": 20.0, "fill_stop": 10.0, "until": iso(NOW + 86400)})
    gh.files[("m15/lip-status.json", "m15-data")] = json.dumps(lip or {
        "at": iso(NOW - 300), "mode": "live", "control": "live", "budget": 20.0, "resting_cost": 19.2,
        "filled_cost": 0.0, "orders": {"KXA-1": {"px": 0.03, "n": 100}}, "events": []})
    return gh


def codes(gh):
    return {x["code"] for x in watch.rules(watch.gather(gh, NOW, runner), watch.settings(load_config()))}


def test_lip_healthy_has_no_flags():
    assert not {c for c in codes(lip_gh()) if c.startswith("LIP")}


def test_lip_stale_and_fills_flagged():
    gh = lip_gh({"at": iso(NOW - 3600), "budget": 20.0, "resting_cost": 10, "filled_cost": 6.0,
                 "orders": {"KXA-1": {}}, "events": [{"t": NOW - 60, "kind": "reject", "error": "x"}] * 3})
    c = codes(gh)
    assert {"LIP_STATUS_STALE", "LIP_FILLS", "LIP_REJECTS"} <= c


def test_lip_over_budget_switches_it_off(tmp_path, monkeypatch):
    gh = lip_gh({"at": iso(NOW - 300), "mode": "live", "control": "live", "budget": 20.0,
                 "resting_cost": 30.0, "filled_cost": 0.0, "orders": {"KXA-1": {}}, "events": []})
    c = FakeClient(haiku={"level": "escalate", "summary": "over budget"},
                   opus={"severity": "pause_lip", "title": "Rewards bot over budget", "message": "Pulled it.",
                         "diagnosis": "d", "proposed_fix": "f", "needs_alex": False, "notebook": []})
    rec, sent = go(tmp_path, monkeypatch, gh, c)
    assert rec["action"] == "paused_lip"
    assert json.loads(gh.files[("lip_control.json", "main")])["mode"] == "off"
    assert json.loads(gh.files[("m15_control.json", "main")])["mode"] == "live"   # 15-min untouched
    assert sent and "SWITCHED OFF the rewards bot" in sent[0][1]


def test_lip_off_but_bidding_and_after_end():
    gh = lip_gh({"at": iso(NOW - 300), "mode": "off", "control": "off", "budget": 20.0,
                 "resting_cost": 5.0, "filled_cost": 0.0, "orders": {"KXA-1": {}}, "events": []},
                {"mode": "off", "budget": 20.0})
    assert "LIP_OFF_BUT_BIDDING" in codes(gh)
    done = lip_gh({"at": iso(NOW - 300), "mode": "off", "control": "live", "budget": 20.0,
                   "resting_cost": 0.0, "filled_cost": 0.0, "orders": {}, "events": []},
                  {"mode": "live", "until": iso(NOW - 7200)})
    assert not {c for c in codes(done) if c.startswith("LIP")}


def test_same_identifier_note_replaces_old_one():
    st = {"notes": ["Coinbase feed drops at night; harmless",
                    "15-MIN funding transfer endpoint returns 403 insufficient_scope (write::transfer); not a trading fault."]}
    watch.remember(st, "M15_FUND 403 insufficient_scope recurred again at 1:35 PM ET Nov 11 (fifth time that day).", 8)
    watch.remember(st, "M15_FUND has recurred four times on Nov 11; it stays a key-scope fix for Alex.", 8)
    assert len(st["notes"]) == 2 and st["notes"][0].startswith("Coinbase") and "four times" in st["notes"][1]


def test_escalation_defaults_to_haiku_and_spend_is_capped(tmp_path, monkeypatch):
    assert watch.settings(load_config())["opus_model"] == "claude-haiku-5-5"
    seen = []
    c = FakeClient()
    orig = c.create
    c.messages.create = lambda model, **kw: (seen.append(model), orig(model, **kw))[1]
    (tmp_path / "watch_spend.json").write_text(json.dumps({"day": "2026-11-10", "usd": 0.5499}))
    rec, sent = go(tmp_path, monkeypatch, FakeGH(), c)
    assert seen == [] and "cap" in rec["haiku"]["error"]          # over the cap: no API call at all


def test_spend_alert_once_a_day(tmp_path, monkeypatch):
    gh = FakeGH()
    gh.files[("copycat/status.json", "copycat-data")] = json.dumps(
        {"boss": {"spend": {"day": "2026-11-10", "usd": 0.9}}})
    rec, sent = go(tmp_path, monkeypatch, gh, FakeClient())
    assert rec["ai_spend"]["total"] > 0.7 and any("AI spend" in t for t, _ in sent)
    rec, sent = go(tmp_path, monkeypatch, gh, FakeClient())
    assert not sent
