import sys,pickle,time; sys.path.insert(0,'.')
from pathlib import Path
from datetime import date
from wxbot import backtest as bt
t=time.time()
days=bt.load(Path(sys.argv[1]), since=date(2025,9,28))
print(len(days), days[0].day, days[-1].day, time.time()-t)
import collections; print(collections.Counter(d.series for d in days).most_common(30))
pickle.dump(days, open(sys.argv[2],'wb'))
