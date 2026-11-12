from datetime import date, timedelta, timezone

from wxbot import latepaper as lp
from wxbot.stations import SeriesInfo, Station, cli_window
from wxbot.weather import Obs, Observations

UTC = timezone.utc
NY = Station("KNYC", "Central Park", 40.78, -73.97, "America/New_York")
LOW = SeriesInfo("KXLOWTNYC", "low", NY, "NYC")
HIGH = SeriesInfo("KXHIGHNY", "high", NY, "NYC")
DAY = date(2026, 11, 12)
START, END = cli_window(DAY, NY.tz)
CLOSE = END + timedelta(hours=1)


def mk(tk, kind, lo=None, hi=None, bid=0.10, ask=0.16, series="KXLOWTNYC"):
    m = {"ticker": f"{series}-26NOV12-{tk}", "event_ticker": f"{series}-26NOV12",
         "strike_type": kind, "close_time": CLOSE.isoformat().replace("+00:00", "Z"),
         "yes_bid_dollars": f"{bid:.2f}", "yes_ask_dollars": f"{ask:.2f}"}
    if kind == "between":
        m.update(floor_strike=lo, cap_strike=hi)
    elif kind == "greater":
        m["floor_strike"] = lo
    else:
        m["cap_strike"] = hi
    return m


def ladder(series="KXLOWTNYC"):
    # 41 or below | 42-43 | 44-45 | 46-47 | 48 or above
    return [mk("T42", "less", hi=42, bid=0.02, ask=0.08, series=series),
            mk("B42.5", "between", 42, 43, bid=0.10, ask=0.20, series=series),
            mk("B44.5", "between", 44, 45, bid=0.55, ask=0.63, series=series),
            mk("B46.5", "between", 46, 47, bid=0.12, ask=0.14, series=series),
            mk("T47", "greater", lo=47, bid=0.01, ask=0.09, series=series)]


def obs(vals, end):
    return Observations([Obs(end - timedelta(hours=len(vals) - 1 - i), v, True) for i, v in enumerate(vals)], [], [])


def test_low_sells_brackets_below_the_low_so_far():
    plan = lp.plan_orders("low", ladder(), 44.6, lp.DEFAULTS)       # station low so far: 45
    got = {(o["bracket"], o["kind"], o["side"], o["price"]) for o in plan}
    assert got == {("41° or below", "need", "no", 0.93),            # sell YES at 7c
                   ("42° to 43°", "need", "no", 0.81),              # sell YES at 19c
                   ("44° to 45°", "holds", "yes", 0.56)}            # buy YES at bid + 1c
    # 46-47 is tighter than 4c and 48+ is already ruled out: no orders there


def test_high_sells_brackets_above_the_high_so_far():
    plan = lp.plan_orders("high", ladder("KXHIGHNY"), 44.6, lp.DEFAULTS)
    assert {(o["bracket"], o["kind"]) for o in plan} == {("48° or above", "need"), ("44° to 45°", "holds")}
    assert next(o for o in plan if o["kind"] == "need")["price"] == 0.92


def test_narrow_spreads_are_skipped():
    ms = [mk("B42.5", "between", 42, 43, bid=0.10, ask=0.13)]
    assert lp.plan_orders("low", ms, 45.0, lp.DEFAULTS) == []


def test_place_once_inside_the_window_only():
    now = CLOSE - timedelta(hours=9)
    today = {"KXLOWTNYC-26NOV12": (LOW, ladder(), obs([47.0, 45.1, 46.0, 50.0], now), START)}
    orders = []
    assert lp.place(orders, today, now) == 3
    assert lp.place(orders, today, now + timedelta(minutes=30)) == 0      # never twice
    assert {o["status"] for o in orders} == {"open"}
    assert orders[0]["extreme"] == "45.1" and orders[0]["hours_left"] == "9.0"
    assert lp.place([], today, CLOSE - timedelta(hours=14)) == 0          # too early
    assert lp.place([], today, CLOSE - timedelta(hours=3)) == 0           # too late


def test_needs_fresh_precise_readings():
    now = CLOSE - timedelta(hours=9)
    stale = obs([47.0, 45.1, 46.0], now - timedelta(hours=3))
    assert lp.place([], {"e": (LOW, ladder(), stale, START)}, now) == 0
    few = obs([45.0, 46.0], now)
    assert lp.place([], {"e": (LOW, ladder(), few, START)}, now) == 0
    rough = Observations([Obs(now - timedelta(hours=i), 45.0, False) for i in range(4)], [], [])
    assert lp.place([], {"e": (LOW, ladder(), rough, START)}, now) == 0


def test_report_groups_by_metric_and_kind():
    rows = [{"status": "filled", "metric": "low", "kind": "need", "price": "0.80", "pnl": "0.20", "date": "2026-11-12"},
            {"status": "filled", "metric": "low", "kind": "need", "price": "0.90", "pnl": "-0.90", "date": "2026-11-13"},
            {"status": "open", "metric": "low", "kind": "need", "price": "0.90", "pnl": "", "date": "2026-11-13"}]
    text = "\n".join(lp.report_section(rows))
    assert "| lows, need | 2 | 1 | $-0.70 on $1.70 |" in text
    assert "| highs, holds | 0 |" in text
