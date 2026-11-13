"""Rule E2 paper check (Nov 13, 2026; Alex: "is it worth testing E2 some more").

E2 = rule E (BTC 15-min, bid 14-10 min before close, favourite 80-95c) but ONLY when the
favourite bets AGAINST BTC's move over the hour before the window opened. 80-day replay:
test half E2 +2.1c/ct vs E with the trend +0.4c (m15-edge RESULTS.md row 1b). No bot change: this splits the
bot's own rule-E windows (fill 600-840 s before close) using Kalshi's settlement prices.

Bar (pre-registered): ~300 E2 windows, average >= +3c/ct after fees and the 90% range above 0
-> propose 1 contract of real money to Alex. Otherwise E2 is dropped.

Run: python tools/e2_check.py  (reads branch m15-data via git, Kalshi public API)
"""
import csv, io, json, random, subprocess, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.elections.kalshi.com/trade-api/v2/markets/"
START = "2026-11-11"  # rule E switched on 4:46 AM ET Nov 11


def git_show(path):
    return subprocess.run(["git", "show", f"origin/m15-data:{path}"], capture_output=True,
                          text=True).stdout


def rows():
    names = subprocess.run(["git", "ls-tree", "--name-only", "origin/m15-data", "m15/"],
                           capture_output=True, text=True).stdout.split()
    for n in sorted(names):
        if not n.startswith("m15/markets-") or n[12:22] < START:
            continue
        for r in csv.DictReader(io.StringIO(git_show(n))):
            if r["series"] == "KXBTC15M" and r["filled"] not in ("", "0") and r["result"] in ("yes", "no"):
                ttc = float(r["fill_ttc_s"] or 0)
                if 590 <= ttc <= 850:
                    yield r


_cache = {}


def ref_price(close_dt):
    """BTC reference (Kalshi expiration_value) for the KXBTC15M window closing at close_dt (UTC)."""
    et = close_dt.astimezone(timezone(timedelta(hours=-5)))
    tk = "KXBTC15M-" + et.strftime("%y%b%d%H%M").upper() + "-" + et.strftime("%M")
    if tk not in _cache:
        try:
            m = json.load(urllib.request.urlopen(API + tk, timeout=20))["market"]
            _cache[tk] = float(m.get("expiration_value") or "nan")
        except Exception:
            _cache[tk] = float("nan")
        time.sleep(0.1)
    return _cache[tk]


def boot(v, B=2000):
    if not v:
        return (0, 0)
    s = sorted(sum(random.choice(v) for _ in v) / len(v) for _ in range(B))
    return s[int(.05 * B)], s[int(.95 * B)]


def main():
    out = {"E2": [], "E_with_trend": [], "E_unknown": []}
    for r in rows():
        close = datetime.fromisoformat(r["close"])
        open_px = ref_price(close - timedelta(minutes=15))
        hour_px = ref_price(close - timedelta(minutes=75))
        n = float(r["filled"])
        pc = float(r["pnl"]) / n
        if open_px != open_px or hour_px != hour_px:
            out["E_unknown"].append((pc, n, r["mode"]))
            continue
        move = open_px - hour_px
        fav_up = r["fav_side"] == "yes"
        against = (move < 0 and fav_up) or (move > 0 and not fav_up)
        out["E2" if against else "E_with_trend"].append((pc, n, r["mode"]))
    for k, L in out.items():
        v = [x[0] for x in L]
        lo, hi = boot(v)
        live = sum(1 for x in L if x[2] == "live")
        print(f"{k:14s} windows {len(v):4d} (live {live})  avg {100*sum(v)/max(1,len(v)):+.1f}c/ct  "
              f"90% range {100*lo:+.1f}..{100*hi:+.1f}c")
    print("bar: ~300 E2 windows, avg >= +3c/ct and range above 0 -> propose 1 ct real money")


if __name__ == "__main__":
    sys.exit(main())
