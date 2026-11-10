from datetime import date

from research_ent import logger as L

BOM_HTML = """<html><body><table>
<tr><th>Rank</th><th>LW</th><th>Release</th><th>Daily</th><th>%± YD</th><th>%± LW</th><th>Theaters</th>
<th>Avg</th><th>To Date</th><th>Days</th><th>Distributor</th></tr>
<tr><td>1</td><td>2</td><td><a href="/release/rl123456/?ref_=bo_sdp">Big Movie</a></td><td>$4,512,300</td>
<td>-</td><td>-</td><td>3,900</td><td>$1,157</td><td>$80,100,000</td><td>10</td><td>Studio A</td></tr>
</table><table><tr><td>other</td></tr></table></body></html>"""


def test_bom_table(monkeypatch):
    monkeypatch.setattr(L, "get", lambda *a, **k: BOM_HTML)
    rows, html = L.bom_day(date(2026, 11, 9))
    assert len(rows) == 1
    r = dict(zip(L.BOX_COLS, rows[0]))
    assert r["release"] == "Big Movie" and r["release_id"] == "rl123456"
    assert r["gross"] == 4512300 and r["theaters"] == 3900 and r["to_date"] == 80100000
    assert r["days_in_release"] == "10" and r["distributor"] == "Studio A"


def test_rt_parse_scorecard_and_ldjson():
    a = ('<title>Big Movie | Rotten Tomatoes</title><script id="media-scorecard-json" type="application/json">'
         '{"criticsScore":{"score":"87","reviewCount":142},"audienceScore":{"score":"91","reviewCount":2500}}'
         '</script>')
    d = L.rt_parse(a)
    assert d["critics"] == "87" and d["critics_n"] == 142 and d["audience"] == "91"
    b = '<script type="application/ld+json">{"aggregateRating":{"ratingValue":"64","reviewCount":40}}</script>'
    d = L.rt_parse(b)
    assert d["critics"] == "64" and d["src"] == "ldjson"


def test_rt_targets_prefers_rules_url():
    ms = [{"_kind": "rt", "ticker": "KXRTX-1", "event_ticker": "KXRTX", "title": "Rotten Tomatoes score for Big Movie?",
           "rules_primary": "per https://www.rottentomatoes.com/m/big_movie."},
          {"_kind": "rt", "ticker": "KXRTY-1", "event_ticker": "KXRTY", "title": "Rotten Tomatoes score for Other Film?",
           "rules_primary": ""},
          {"_kind": "box", "ticker": "KXBOX-1", "event_ticker": "KXBOX", "title": "x"}]
    t = L.rt_targets(ms)
    assert t == {"KXRTX": "https://www.rottentomatoes.com/m/big_movie", "KXRTY": "Other Film"}


def test_series_match():
    assert L.SERIES_RE.search("KXRTWICKED") and L.SERIES_RE.search("Opening weekend box office")
    assert not L.SERIES_RE.search("Highest temperature in NYC")


def test_rt_title():
    assert L.rt_title("Rotten Tomatoes score for Wicked: For Good?") == "Wicked: For Good"
    assert L.rt_title("Will Zootopia 2's Rotten Tomatoes score be above 90%?") == "Zootopia 2"
    assert L.rt_title("Zootopia 2 Rotten Tomatoes score") == "Zootopia 2"


def test_norm_and_slug():
    assert L.norm("Dune: Part Two | Rotten Tomatoes") != L.norm("Dune: Part Three")
    assert L.norm("Ebenezer (2026) | Rotten Tomatoes") == L.norm("Ebenezer")
    assert L.slug("The Fantastic Four: First Steps") == "the_fantastic_four_first_steps"
    assert L.slug("Wicked: For Good") == "wicked_for_good"


def test_rt_find_rejects_wrong_film(monkeypatch):
    pages = {"https://www.rottentomatoes.com/m/dune_part_three": None,
             "https://www.rottentomatoes.com/m/dune_part_three_2026": "<title>Dune: Part Three | Rotten Tomatoes</title>"}

    def fake(url, params=None, js=True, **k):
        if "search" in url:
            return '<a href="https://www.rottentomatoes.com/m/dune_part_two">Dune: Part Two</a>'
        return pages.get(url)
    monkeypatch.setattr(L, "get", fake)
    url, html = L.rt_find("Dune: Part Three", year=2026)
    assert url.endswith("dune_part_three_2026")
