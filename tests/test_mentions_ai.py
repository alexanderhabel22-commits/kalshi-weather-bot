from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from wxbot import mentions_ai
from wxbot.trader import Context, Intent
from wxbot.util import load_config

NOW = datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc)
EV = "KXTRUMPMENTION-26NOV11"


class Block(SimpleNamespace):
    pass


class FakeClient:
    def __init__(self, text, reject=()):
        self.calls, self.text, self.reject = [], text, set(reject)
        self.messages = self

    def create(self, **kw):
        import anthropic
        import httpx2 as httpx
        tool = kw["tools"][0]["type"]
        self.calls.append(kw)
        if tool in self.reject:
            req = httpx.Request("POST", "https://x")
            raise anthropic.BadRequestError("bad tool", response=httpx.Response(400, request=req), body=None)
        return SimpleNamespace(stop_reason="end_turn",
                               content=[Block(type="server_tool_use"), Block(type="text", text=self.text)],
                               usage=SimpleNamespace(input_tokens=1200, output_tokens=90,
                                                     server_tool_use=SimpleNamespace(web_search_requests=2)))


def ctx(state=None):
    return Context(load_config(), None, NOW, state if state is not None else {}, [], {}, "live")


def intents():
    return [Intent("mentions", f"{EV}-NETF", EV, "no", 0.82, 4, title="Will Trump say Netflix?", note="no bid 0.81"),
            Intent("mentions", f"{EV}-TARI", EV, "no", 0.86, 4, title="Will Trump say Tariff?")]


MARKETS = [{"ticker": f"{EV}-NETF", "yes_sub_title": "Netflix", "title": "Will Trump say Netflix?",
            "occurrence_datetime": "2026-11-11T16:00:00Z", "rules_primary": "If Trump says Netflix..."},
           {"ticker": f"{EV}-TARI", "yes_sub_title": "Tariff", "title": "Will Trump say Tariff?"}]

REPLY = ('{"words": [{"ticker": "%s-NETF", "p_said": 0.55, "topical": true, "reason": "Paramount deal news"},'
         ' {"ticker": "%s-TARI", "p_said": 0.12, "topical": false, "reason": "quiet"}]}' % (EV, EV))


@pytest.fixture(autouse=True)
def tmp_log(tmp_path, monkeypatch):
    monkeypatch.setattr(mentions_ai, "LOG", tmp_path / "mentions_ai.csv")


def test_scores_logged_and_noted_once():
    pytest.importorskip("anthropic")
    c, its, st = FakeClient(REPLY), intents(), {}
    assert mentions_ai.annotate(ctx(st), its, MARKETS, client=c) == 1
    assert its[0].note == "no bid 0.81 | ai p=0.55 topical" and its[1].note == "ai p=0.12"
    assert "Netflix" in c.calls[0]["messages"][0]["content"]
    assert "0.82" not in c.calls[0]["messages"][0]["content"]          # the market price isn't shown
    rows = mentions_ai.load_scores()
    assert rows[f"{EV}-NETF"]["p_said"] == "0.55" and rows[f"{EV}-NETF"]["searches"] == "2"
    # next run: cached, no new call, note not doubled
    its2 = intents()
    assert mentions_ai.annotate(ctx(st), its2, MARKETS, client=c) == 0 and len(c.calls) == 1
    assert its2[1].note == "ai p=0.12"


def test_falls_back_to_other_search_tool():
    pytest.importorskip("anthropic")
    c, st = FakeClient(REPLY, reject={"web_search_20250305"}), {}
    mentions_ai.annotate(ctx(st), intents(), MARKETS, client=c)
    assert st["ai"]["search_tool"] == "web_search_20260209" and len(c.calls) == 2


def test_daily_cap_and_no_key(monkeypatch):
    st = {"ai": {"day": "2026-11-10", "calls": 25}}
    c = FakeClient(REPLY)
    assert mentions_ai.annotate(ctx(st), intents(), MARKETS, client=c) == 0 and not c.calls
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    its = intents()
    assert mentions_ai.annotate(ctx({}), its, MARKETS) == 0 and its[1].note == ""


def test_never_raises():
    class Boom:
        messages = property(lambda s: (_ for _ in ()).throw(RuntimeError("x")))
    its = intents()
    assert mentions_ai.annotate(ctx({}), its, MARKETS, client=Boom()) in (0, 1)


def test_parse_handles_junk():
    assert mentions_ai.parse("no json") == []
    assert mentions_ai.parse('x {"words": [{"ticker": "A", "p_said": 1.7}]} y')[0]["p"] == 1.0


def test_review_counterfactual():
    scores = {"A": {"p_said": "0.6", "topical": "1"}, "B": {"p_said": "0.1", "topical": "0"},
              "C": {"p_said": "0.45", "topical": "0"}}
    rows = [{"strategy": "mentions", "status": "settled", "filled": "4", "ticker": "A", "pnl": "-3.28", "mode": "live"},
            {"strategy": "mentions", "status": "settled", "filled": "4", "ticker": "B", "pnl": "0.60", "mode": "live"},
            {"strategy": "mentions", "status": "settled", "filled": "4", "ticker": "C", "pnl": "0.50", "mode": "live"},
            {"strategy": "favorites", "status": "settled", "filled": "4", "ticker": "B", "pnl": "9", "mode": "live"}]
    r = mentions_ai.review(rows, scores)
    assert r["bets"] == 3 and r["pnl"] == pytest.approx(-2.18)
    cut5 = next(c for c in r["cuts"] if c["threshold"] == 0.5)
    assert cut5["n"] == 1 and cut5["lost"] == 1 and cut5["pnl_after"] == pytest.approx(1.10)
    assert r["topical"]["n"] == 1
    assert "skip if p" in mentions_ai.render(r)


def test_word_and_prompt():
    assert mentions_ai.word_of({"title": "Will Trump say Crypto?"}) == "Crypto"
    txt = mentions_ai.prompt(EV, MARKETS)
    assert "ET" in txt and "Rules:" in txt and f"{EV}-TARI" in txt
