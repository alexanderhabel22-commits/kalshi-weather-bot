import sys,pandas as pd,numpy as np
m=pd.read_csv(sys.argv[1]); sp=pd.read_csv(sys.argv[2]); sp=sp.drop_duplicates('t').set_index('t').close
g=lambda t: sp.get(t)
m['S1']=m.close_ts.map(lambda c: g(c-60)); m['S0']=m.open_ts.map(lambda c: g(c-60))
m=m.dropna(subset=['S1','S0']); y=m.result=='yes'
print(sys.argv[1], 'n',len(m),'agree(rel open->close)',((m.S1>=m.S0)==y).mean().round(3),'agree(vs floor)',((m.S1>=m.floor)==y).mean().round(3), 'median floor/S0-1 (bps)', round(1e4*np.median(m.floor/m.S0-1),1))
