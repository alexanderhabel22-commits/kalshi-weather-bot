"""Build one analysis file from the downloaded backtest folders.

usage: python research_wx/build.py out.pkl root [root ...]
Each root holds kalshi*/ , forecasts/ or om/ , mos/ , readings/ (later roots add to
earlier ones). Output: dict with
  days    : list of dict(series, city, metric, icao, tz, day, actual, close_ts)
  markets : list of dict(ticker, series, day, lo, hi, yes, close_ts, vol, c=candle arrays)
  fc      : {icao: {model: {"t0": first hour (unix), "d0".."d3": float arrays (nan = missing)}}}
  mos     : {icao: {model: {runtime_ts: {"x": [(ftime_ts, value)], "sd": {...}}}}}  (max/min rows)
"""
import csv
import glob
import gzip
import io
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")
from wxbot import kalshi  # noqa: E402
from wxbot.stations import default_catalog  # noqa: E402

UTC = timezone.utc


def rd(p):
    with gzip.open(p, "rb") as f:
        return f.read()


def fl(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan


def px(side, k):
    side = side or {}
    return fl(side.get(k + "_dollars", side.get(k)))


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def main(out, roots):
    cat = {si.ticker: si for si in default_catalog()}
    days, markets, seen = {}, [], set()
    for root in roots:
        for p in sorted(glob.glob(str(Path(root) / "kalshi*" / "*.json.gz"))):
            d = json.loads(rd(p))
            si = cat.get(d["series"])
            if si is None:
                continue
            for m in d["markets"]:
                tk = m["ticker"]
                if tk in seen or m.get("result") not in ("yes", "no"):
                    continue
                br = kalshi.parse_bracket(m)
                dd = kalshi.event_date(m.get("event_ticker", ""))
                cs = d["candles"].get(tk)
                if br is None or dd is None or not cs:
                    continue
                seen.add(tk)
                val = fl(m.get("expiration_value"))
                close = ts(m["close_time"])
                key = (d["series"], dd)
                if key not in days:
                    days[key] = dict(series=d["series"], city=si.city, metric=si.metric,
                                     icao=si.station.icao, tz=si.station.tz, day=dd,
                                     actual=val, close_ts=close)
                elif np.isnan(days[key]["actual"]) and not np.isnan(val):
                    days[key]["actual"] = val
                c = np.array([[x["end_period_ts"], px(x.get("yes_bid"), "close"), px(x.get("yes_ask"), "close"),
                               px(x.get("price"), "low"), px(x.get("price"), "high"),
                               fl(x.get("volume_fp", x.get("volume")))] for x in cs], dtype=float)
                markets.append(dict(ticker=tk, series=d["series"], day=dd, lo=br[0], hi=br[1],
                                    yes=m["result"] == "yes", close_ts=close,
                                    vol=fl(m.get("volume_fp", m.get("volume"))), c=c))
    fc = {}
    for root in roots:
        for p in sorted(glob.glob(str(Path(root) / "forecasts" / "*.json.gz"))):
            icao = Path(p).name.split(".")[0]
            for model, h in json.loads(rd(p)).items():
                add_fc(fc, icao, model, h)
        for p in sorted(glob.glob(str(Path(root) / "om" / "*.json.gz"))):
            icao, model = Path(p).name.split(".")[0].split("__")
            add_fc(fc, icao, model, json.loads(rd(p))["hourly"])
    mos = {}
    for root in roots:
        for p in sorted(glob.glob(str(Path(root) / "mos" / "*.csv.gz"))):
            icao, model = Path(p).name.split(".")[0].split("_")
            add_mos(mos.setdefault(icao, {}).setdefault(model, {}), rd(p).decode())
    obs = {}
    for root in roots:
        for p in sorted(glob.glob(str(Path(root) / "readings" / "*.csv.gz"))):
            icao = Path(p).name.split(".")[0]
            add_obs(obs.setdefault(icao, {}), rd(p).decode())
    obs = {k: np.array(sorted(v.items()), dtype=float) for k, v in obs.items()}
    pickle.dump(dict(days=list(days.values()), markets=markets, fc=fc, mos=mos, obs=obs), open(out, "wb"))
    print(len(days), "city-days", len(markets), "markets", {k: sorted(v) for k, v in list(fc.items())[:1]},
          {k: {m: len(r) for m, r in v.items()} for k, v in list(mos.items())[:1]})


def add_fc(fc, icao, model, h):
    t = h.get("time") or []
    if not t:
        return
    t0 = datetime.fromisoformat(t[0]).replace(tzinfo=UTC).timestamp()
    rec = {"t0": t0}
    for tag, var in (("d0", "temperature_2m"), ("d1", "temperature_2m_previous_day1"),
                     ("d2", "temperature_2m_previous_day2"), ("d3", "temperature_2m_previous_day3")):
        if h.get(var):
            a = np.array([np.nan if v is None else v for v in h[var]], dtype=float)
            if np.isfinite(a).sum() > 100:
                rec[tag] = a
    if len(rec) > 1:
        fc.setdefault(icao, {})[model] = rec


def add_mos(dst, text):
    rdr = csv.DictReader(io.StringIO(text))
    for r in rdr:
        v = r.get("txn") or r.get("n_x") or r.get("x_n")
        if v in (None, "", "M"):
            continue
        try:
            rt = datetime.strptime(r["runtime"][:16], "%Y-%m-%d %H:%M").replace(tzinfo=UTC).timestamp()
            ft = datetime.strptime(r["ftime"][:16], "%Y-%m-%d %H:%M").replace(tzinfo=UTC).timestamp()
        except (KeyError, ValueError):
            continue
        run = dst.setdefault(rt, {"x": [], "sd": []})
        run["x"].append((ft, fl(v)))
        run["sd"].append((ft, fl(r.get("xnd"))))


def add_obs(dst, text):
    """Precise METAR T-group readings: {unix ts: degF}."""
    import re
    for line in text.splitlines()[1:]:
        parts = line.split(",", 3)
        if len(parts) < 4:
            continue
        m = re.search(r"\bT([01])(\d{3})[01]\d{3}\b", parts[3])
        if not m:
            continue
        c = int(m.group(2)) / 10 * (-1 if m.group(1) == "1" else 1)
        try:
            t = datetime.strptime(parts[1], "%Y-%m-%d %H:%M").replace(tzinfo=UTC).timestamp()
        except ValueError:
            continue
        dst[t] = c * 9 / 5 + 32


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
