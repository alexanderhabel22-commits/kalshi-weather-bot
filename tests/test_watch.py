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
        self.calls.append(model)
        body = json.dumps(self.answers[model])
        return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=1000, output_tokens=50),
                               content=[SimpleNamespace(type="text", text=body)])


def runner(cmd, **kw):
    return SimpleNamespace(stdout="active" if "is-active" in cmd else "")


def go(tmp_path, monkeypatch, gh, client):
    sent = []
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
    # second run within re-alert window: no new Opus call, no new push
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
