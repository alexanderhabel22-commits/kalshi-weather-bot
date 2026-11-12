import pandas as pd, glob
R='hist/runs/'
m=pd.concat([pd.read_csv(f) for f in glob.glob(R+'*-s*/sample.csv.gz')]).drop_duplicates('ticker')
s=pd.concat([pd.read_csv(f) for f in glob.glob(R+'*-s*/series.csv')]).drop_duplicates('series')
m=m.merge(s[['series','category','fee_type','fee_multiplier','frequency']],on='series',how='left')
t=pd.concat([pd.read_csv(f,dtype={'ticker':'category','taker_side':'category'}) for f in sorted(glob.glob(R+'*-s*/trades_*.csv.gz'))])
t['ticker']=t.ticker.astype(str)
t=t.sort_values(['ticker','t'],kind='stable').reset_index(drop=True)
m.to_parquet('markets.parquet'); t.to_parquet('trades.parquet')
print(len(m),len(t)); print(m.category.value_counts()); print(m.result.value_counts()); print(m.truncated.sum())
print(pd.to_datetime(m.close_ts,unit='s').dt.to_period('Q').value_counts().sort_index())
