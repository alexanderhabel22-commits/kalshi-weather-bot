"""Add NWS MOS day-ahead highs/lows (NBM 'NBS' txn, GFS MOS 'n_x') to a days pickle
as extra models "nbm" and "mav" (lead "d1": latest run issued >= 37h before the
climate day ends, i.e. what was out by midday the day before).

usage: python tools/add_mos.py days.pkl mos_dir out.pkl"""
import csv, gzip, pickle, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, '.')
from wxbot.stations import cli_window, default_catalog

UTC = timezone.utc
days = pickle.load(open(sys.argv[1], 'rb'))
mos = sys.argv[2]
cat = {si.ticker: si for si in default_catalog()}
cache = {}


def table(icao, model):
    key = (icao, model)
    if key not in cache:
        col = 'txn' if model == 'NBS' else 'n_x'
        t = {}   # ftime -> [(runtime, value)]
        try:
            with gzip.open(f'{mos}/{icao}_{model}.csv.gz', 'rt') as f:
                for r in csv.DictReader(f):
                    v = r.get(col)
                    try:
                        v = float(v)
                    except (TypeError, ValueError):
                        continue
                    ft = datetime.fromisoformat(r['ftime']).replace(tzinfo=UTC)
                    rt = datetime.fromisoformat(r['runtime']).replace(tzinfo=UTC)
                    t.setdefault(ft, []).append((rt, v))
        except FileNotFoundError:
            pass
        cache[key] = t
    return cache[key]


n = {'nbm': 0, 'mav': 0}
for d in days:
    si = cat.get(d.series)
    if not si:
        continue
    _, end = cli_window(d.day, si.station.tz)
    cutoff = end - timedelta(hours=37)
    base = datetime(d.day.year, d.day.month, d.day.day, tzinfo=UTC)
    ft = base + timedelta(days=1) if d.metric == 'high' else base + timedelta(hours=12)
    for model, tag in (('NBS', 'nbm'), ('GFS', 'mav')):
        runs = [x for x in table(si.station.icao, model).get(ft, []) if x[0] <= cutoff]
        if runs:
            d.fc[tag] = {'d1': max(runs)[1]}
            n[tag] += 1
pickle.dump(days, open(sys.argv[3], 'wb'))
print(len(days), 'days;', n)
