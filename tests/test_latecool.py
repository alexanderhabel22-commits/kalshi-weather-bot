from datetime import date, timedelta

from wxbot import latecool as lc
from wxbot import latepaper as lp
from wxbot.stations import cli_window

from tests.test_latepaper import CLOSE, HIGH, LOW, NY, START, ladder, obs


def today(series_info, ms, now, vals=(40, 42, 44, 44.6)):
    ev = ms[0]["event_ticker"]
    return {ev: (series_info, ms, obs(list(vals), now - timedelta(minutes=10)), START)}


def test_writes_only_high_need_inside_the_later_window():
    now = CLOSE - timedelta(hours=7)
    rows = []
    n = lc.place(rows, today(HIGH, ladder("KXHIGHNY"), now), now)
    assert n == 1 and len(rows) == 1
    o = rows[0]
    assert (o["metric"], o["kind"], o["side"], o["bracket"]) == ("high", "need", "no", "48° or above")
    # the main paper log writes holds too in the same scan (holds is not in the twin)
    main = []
    lp.place(main, today(HIGH, ladder("KXHIGHNY"), now), now, {"wait_while_rising": []})
    assert {r["kind"] for r in main} == {"need", "holds"}


def test_nothing_before_nine_hours_left():
    now = CLOSE - timedelta(hours=11)          # inside the main 12h window, not the twin's 9h
    rows = []
    assert lc.place(rows, today(HIGH, ladder("KXHIGHNY"), now), now) == 0 and rows == []
    main = []
    assert lp.place(main, today(HIGH, ladder("KXHIGHNY"), now), now) > 0


def test_lows_are_ignored():
    now = CLOSE - timedelta(hours=7)
    rows = []
    assert lc.place(rows, today(LOW, ladder(), now, vals=(50, 48, 46, 44.6)), now) == 0


def test_warm_months_are_ignored():
    day = date(2026, 6, 12)
    _, end = cli_window(day, NY.tz)
    close = end + timedelta(hours=1)
    ms = [dict(m, ticker=m["ticker"].replace("26NOV12", "26JUN12"),
               event_ticker="KXHIGHNY-26JUN12", close_time=close.isoformat().replace("+00:00", "Z"))
          for m in ladder("KXHIGHNY")]
    now = close - timedelta(hours=7)
    start, _ = cli_window(day, NY.tz)
    t = {"KXHIGHNY-26JUN12": (HIGH, ms, obs([40, 42, 44, 44.6], now - timedelta(minutes=10)), start)}
    rows = []
    assert lc.place(rows, t, now) == 0
    assert lc.place(rows, t, now, {"months": [6]}) == 1     # months is a setting


def test_each_bracket_written_once_and_holds_never_kept():
    now = CLOSE - timedelta(hours=7)
    rows = []
    lc.place(rows, today(HIGH, ladder("KXHIGHNY"), now), now)
    later = now + timedelta(minutes=30)
    assert lc.place(rows, today(HIGH, ladder("KXHIGHNY"), later), later) == 0
    assert len(rows) == 1 and all(r["kind"] == "need" for r in rows)


def test_save_and_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(lc, "ORDERS", tmp_path / "paper_late_cool.csv")
    now = CLOSE - timedelta(hours=7)
    rows = []
    lc.place(rows, today(HIGH, ladder("KXHIGHNY"), now), now)
    lc.save_orders(rows)
    back = lc.load_orders()
    assert [r["id"] for r in back] == [r["id"] for r in rows]
    assert lc.ORDERS != lp.ORDERS

