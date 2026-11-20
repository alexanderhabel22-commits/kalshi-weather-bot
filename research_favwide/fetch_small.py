import sys, os, gzip, csv, pickle
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
S=sys.argv[1]
sys.argv=['x', S+'/ent', 'none']
sys.path.insert(0, S+'/code')
import fetch_ent as F
import pandas as pd
m=pd.read_csv(S+'/ent/favorites_markets.csv.gz')
old=set(pd.read_csv(S+'/d/runs/20261112-0547/favorites_markets.csv.gz').ticker)
m=m[~m.ticker.isin(old)].sort_values('close_ts',ascending=False)
ms=m.to_dict('records')
for r in ms:
    for k in ('occ_ts','close_ts','open_ts','hist'): r[k]=int(r[k]) if r[k]==r[k] else 0
out=Path(S+'/chunks'); out.mkdir(exist_ok=True)
F._stop[0]=1e18
print('new markets',len(ms),flush=True)
CH=500  # keep chunk names stable
for ci in range(0,len(ms),CH):
    fn=out/f'c{ci:06d}.pkl'
    if fn.exists(): continue
    part=ms[ci:ci+CH]
    with ThreadPoolExecutor(8) as ex:
        res=list(ex.map(lambda r:(F.candles(r,2),F.trades(r,2,[(0.02,0.19),(0.81,0.98)])),part))
    pickle.dump(res,open(str(fn)+'.tmp','wb')); os.rename(str(fn)+'.tmp',fn)
    print('chunk',ci,flush=True)
print('ALL DONE',flush=True)
