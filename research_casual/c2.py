import pandas as pd, numpy as np, glob
R='hist/runs/'
v=pd.concat([pd.read_csv(f) for f in glob.glob(R+'*-s*/volume.csv.gz')]).drop_duplicates(['series','month'])
s=pd.concat([pd.read_csv(f) for f in glob.glob(R+'*-s*/series.csv')]).drop_duplicates('series')
v=v.merge(s[['series','category','title']],on='series')
pd.set_option('display.width',250); pd.set_option('display.max_colwidth',70)
for cat in ('Entertainment','Mentions'):
    for mon in ('2025-11','2025-12','2026-01','2026-02','2026-03'):
        d=v[(v.category==cat)&(v.month==mon)].sort_values('volume',ascending=False).head(12)
        print(cat,mon); print(d[['series','title','markets','volume']].to_string(index=False))
