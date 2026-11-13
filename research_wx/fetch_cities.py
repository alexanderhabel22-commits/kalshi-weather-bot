import sys,time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,".")
from wxbot import backtest_fetch as bf
from wxbot.stations import default_catalog
from wxbot.util import Http
UTC=timezone.utc
OUT=Path(sys.argv[1]); mode=sys.argv[2]
new=sys.argv[3].split(",")
cat={s.ticker:s for s in default_catalog()}
UA="kalshi-weather-research"
if mode=="kalshi":
    since=datetime(2026,8,20,tzinfo=UTC)
    with ThreadPoolExecutor(4) as ex:
        for r in ex.map(lambda t: bf.fetch_series(t,OUT,since,UA), new): print(r,flush=True)
else:
    http=Http(UA,timeout=180)
    sts={cat[t].station.icao:cat[t].station for t in new}
    for st in sts.values():
        print(bf.fetch_readings(http,st,OUT,datetime(2026,8,20,tzinfo=UTC),datetime(2026,11,14,tzinfo=UTC)),flush=True)
