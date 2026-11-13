"""Coverage sweep pull (Nov 13, 2026). Read-only public data; never trades.

Same downloader as research_main/fetch.py, pointed at series the live main-bot
strategies do NOT scan, so the exact live rules can be replayed on them.

  python research_cover/fetch_cover.py OUT macro|favorites START [BUDGET_MIN]
macro: the Fed/CPI fade universe extended to other scheduled-number series.
favorites: big events (1M+ contracts) in categories the live favorites rule skips.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OUT, SEC, START = sys.argv[1], sys.argv[2], sys.argv[3]
BUDGET = float(sys.argv[4]) if len(sys.argv) > 4 else 120
sys.argv = [sys.argv[0], OUT]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "research_main"))
import fetch as F  # noqa: E402

MACRO_NEW = [
    # other central banks (same shape as KXFEDDECISION)
    "KXCBDECISIONJAPAN", "KXCBDECISIONCANADA", "KXCBDECISIONEU", "KXCBDECISIONENGLAND",
    "KXCBDECISIONMEXICO", "KXCBDECISIONAUSTRALIA", "KXCBDECISIONBRAZIL", "KXCBDECISIONKOREA",
    "KXCBDECISIONCHINA", "KXCBDCZECH", "KXCBDISRAEL", "KXCBDECISIONRUSSIA",
    # Fed-adjacent
    "KXFEDCOMBO", "KXCPICOMBO", "KXEMPLOYMENTCOMBO", "KXFOMCDISSENTCOUNT", "KXFEDDISSENT",
    # inflation variants
    "KXECONSTATCPIYOY", "KXECONSTATCPI", "KXECONSTATCORECPIYOY", "KXECONSTATCPICORE", "KXCPICOREYOY",
    "KXUSGASCPI", "KXCPINDEX", "KXCPIUSEDCAR", "KXCPIGAS", "KXUSPPI", "KXUSPPIYOY",
    # jobs / growth
    "KXU3", "KXECONSTATU3", "KXUE", "KXADP", "KXGDP", "KXISMPMI", "KXUSRETAIL", "KXHOUSINGSTART",
    "KXUSNFP",
    # weekly / monthly / daily numbers
    "KX30YMORTW", "KXFRM", "KXSPRLVL", "KXAAAGASWNJ", "KXGAS", "KXUSFLYCAN", "KXAAAGASM",
    "KXAAAGASMTX", "KXDIESELMON", "KXEGGS", "KXOIL", "KXSOFRD", "KXTRUEV", "KXTXERCOTPEAKD",
    "KXTNOTEW", "KXTNOTED",
]
FAV_CATS_NEW = ["Science and Technology", "Companies", "Economics", "Financials", "Crypto",
                "World", "Health", "Climate and Weather", "Social", "Transportation", "Culture"]


def macro():
    lo = F.day(START)
    with ThreadPoolExecutor(6) as ex:
        ms = [m for lst in ex.map(lambda s: F.list_markets(s, lo), MACRO_NEW) for m in lst]
    ms = [m for m in ms if m["volume"] >= 100]
    F.log("macro-new markets", len(ms))
    F.CFG["macro"]["budget"] = BUDGET
    F.pull("macro", ms)


def favorites():
    series = {}
    for cat in FAV_CATS_NEW:
        js = F.get("/series", {"category": cat, "include_volume": "true"}) or {}
        for s in js.get("series") or []:
            if s.get("category") == cat and (F.f(s.get("volume_fp")) or 0) >= F.FAV_MIN_SERIES \
                    and s.get("frequency") not in ("fifteen_min", "hourly", "daily"):
                series[s["ticker"]] = cat
    F.log("favorites-new big series", len(series))
    lo = F.day(START)
    with ThreadPoolExecutor(6) as ex:
        ms = [m for lst in ex.map(lambda s: F.list_markets(s, lo), sorted(series)) for m in lst]
    ev: dict = {}
    for m in ms:
        ev[m["event"]] = ev.get(m["event"], 0.0) + m["volume"]
    for m in ms:
        m["cat"] = series[m["series"]]
        m["event_volume"] = round(ev[m["event"]])
    ms = [m for m in ms if m["event_volume"] >= 1_000_000 and m["volume"] >= 100]
    F.log("favorites-new markets kept", len(ms), "events", len({m['event'] for m in ms}))
    F.CFG["favorites"]["budget"] = BUDGET
    F.pull("favorites", ms)


if __name__ == "__main__":
    {"macro": macro, "favorites": favorites}[SEC]()
    F.log("all done")
