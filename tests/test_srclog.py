import gzip
import json

from wxbot import srclog


class Resp:
    def __init__(self, text="", status=200, headers=None, js=None):
        self.text = text
        self.content = text.encode()
        self.status_code = status
        self.headers = headers or {}
        self._js = js

    def json(self):
        return self._js if self._js is not None else json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, pages):
        self.pages = pages          # url -> list of Resp (last one repeats)
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append((url, params))
        if url.endswith("/markets"):
            return Resp(js={"markets": [{"ticker": "KXTRUMPAPPROVE-X-E38.4", "yes_bid_dollars": "0.08",
                                         "yes_ask_dollars": "0.10", "last_price_dollars": "0.09",
                                         "volume_fp": "100"}]})
        if url.endswith("/events"):
            return Resp(js={"events": []})
        lst = self.pages[url]
        return lst.pop(0) if len(lst) > 1 else lst[0]


RCP = ('<html><script>var x=1</script><table><tr><td>RCP Average</td><td>10/1 - 10/12</td>'
       '<td>{a}</td><td>57.9</td></tr></table></html>')


def make(tmp_path, pages, clock):
    src = srclog.Source("rcp", "https://rcp.test/page", srclog.key_rcp, lambda now: 10.0,
                        ("KXTRUMPAPPROVE",))
    return srclog.SrcLog(tmp_path, sources=[src], session=FakeSession(pages), clock=clock,
                         rt_refresh_s=10 ** 9)


def rows(tmp_path):
    out = []
    for p in sorted(tmp_path.glob("srclog-*.jsonl")):
        out += [json.loads(x) for x in p.read_text().splitlines()]
    return out


def test_key_rcp_ignores_scripts_and_finds_average():
    k = srclog.key_rcp(Resp(RCP.format(a="38.4")))
    assert k.startswith("RCP Average") and "38.4" in k and "var x" not in k


def test_change_logs_and_snapshots_kalshi(tmp_path):
    t = [1_700_000_000.0]
    pages = {"https://rcp.test/page": [Resp(RCP.format(a="38.1")), Resp(RCP.format(a="38.1")),
                                       Resp(RCP.format(a="38.4"))]}
    lg = make(tmp_path, pages, lambda: t[0])
    lg.rt_next = 10 ** 12
    for _ in range(3):
        lg.step()
        t[0] += 10
    kinds = [r["kind"] for r in rows(tmp_path)]
    assert kinds.count("first") == 1 and kinds.count("change") == 1
    ch = [r for r in rows(tmp_path) if r["kind"] == "change"][0]
    assert "38.4" in ch["key"] and "38.1" in ch["prev"]
    snaps = [r for r in rows(tmp_path) if r["kind"] == "kalshi"]
    assert snaps and snaps[0]["delay"] == 0 and snaps[0]["markets"][0][1] == 0.08
    assert len(lg.snaps) == len(srclog.SNAP_DELAYS) - 1      # +10/+30/+60/+300 s still queued
    # no authenticated or order endpoints are ever touched
    assert all("portfolio" not in u and "orders" not in u for u, _ in lg.s.calls)


def test_blocked_source_is_logged_not_fatal(tmp_path):
    t = [1_700_000_000.0]
    lg = make(tmp_path, {"https://rcp.test/page": [Resp("denied", status=403)]}, lambda: t[0])
    lg.rt_next = 10 ** 12
    lg.step()
    t[0] += 10
    lg.step()
    polls = [r for r in rows(tmp_path) if r["kind"] == "poll"]
    assert len(polls) == 1 and polls[0]["status"] == 403     # logged once, not every poll


def test_uploads_only_finished_hours(tmp_path):
    t = [1_700_000_000.0]
    sent = {}
    lg = make(tmp_path, {"https://rcp.test/page": [Resp(RCP.format(a="38.1"))]}, lambda: t[0])
    lg.uploader = lambda path, data: sent.__setitem__(path, data)
    lg.rt_next = 10 ** 12
    lg.step()
    lg.upload_done_hours(t[0])
    assert not sent
    t[0] += 3600
    lg.upload_done_hours(t[0])
    assert len(sent) == 1
    (path, data), = sent.items()
    assert path.startswith("srclog/srclog-") and b"rcp" in gzip.decompress(data)


def test_main_drops_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("SRCLOG_DIR", str(tmp_path))
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", "secret")
    monkeypatch.setenv("NTFY_TOPIC", "secret")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    seen = {}

    def fake_run(self):
        import os
        seen["k"] = os.environ.get("KALSHI_PRIVATE_KEY")
        seen["n"] = os.environ.get("NTFY_TOPIC")
    monkeypatch.setattr(srclog.SrcLog, "run", fake_run)
    assert srclog.main() == 0
    assert seen == {"k": None, "n": None}


def test_tsa_and_headers_keys():
    assert srclog.key_tsa(Resp("<td>11/9/2026</td><td>2,612,345</td>")) == "11/9/2026 2,612,345"
    a = srclog.key_headers(Resp("", headers={"last-modified": "Tue", "content-length": "10"}))
    b = srclog.key_headers(Resp("", headers={"last-modified": "Wed", "content-length": "10"}))
    assert a != b


def test_rt_title_and_slug_matching():
    assert srclog.rt_movie("Dune: Part Three: Rotten Tomatoes score") == "Dune: Part Three"
    assert srclog.rt_movie("Ebenezer · Rotten Tomatoes score") == "Ebenezer"

    class S:
        headers = {}

        def __init__(self, page):
            self.page = page

        def get(self, url, params=None, timeout=None, headers=None):
            return Resp(self.page)
    page = ('<a href="https://www.rottentomatoes.com/m/rotten_tomatoes">x</a>'
            '<a href="https://www.rottentomatoes.com/m/dune_part_two">x</a>'
            '<a href="https://www.rottentomatoes.com/m/how_to_rob_a_bank_2026">x</a>')
    lg = srclog.SrcLog.__new__(srclog.SrcLog)
    lg.s = S(page)
    assert lg.rt_lookup("Dune: Part Three") is None            # never the wrong sequel
    assert lg.rt_lookup("How to Rob a Bank").endswith("/m/how_to_rob_a_bank_2026")


def test_rt_key_uses_own_scorecard_only():
    page = ('"criticsScore":{"likedCount":92,"reviewCount":104,"score":"88"}'
            ' ... "tomatometer":{"tomatometer":97}')
    k = srclog.key_rt(Resp(page))
    assert "104" in k and "97" not in k


def test_blocked_source_backs_off(tmp_path):
    t = [1_700_000_000.0]
    lg = make(tmp_path, {"https://rcp.test/page": [Resp("denied", status=403)]}, lambda: t[0])
    lg.rt_next = 10 ** 12
    lg.step()
    assert lg.sources[0].next_at >= t[0] + srclog.BLOCKED_RETRY_S


def test_head_key_from_headers():
    k = srclog.key_headers(Resp("", headers={"last-modified": "Tue", "etag": "e1",
                                             "content-length": "9"}))
    assert k == "Tue|e1|9"
    assert srclog.key_headers(Resp("")) is None
