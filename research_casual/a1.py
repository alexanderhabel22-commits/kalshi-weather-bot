import pandas as pd, numpy as np, math
m=pd.read_parquet('markets.parquet'); o=pd.read_parquet('orders.parquet')
m=m[m.result.isin(['yes','no'])]
o=o.merge(m[['ticker','event','category','result','close_ts','fee_multiplier']],on='ticker')
o['win']=(o.yes==(o.result=='yes')).astype(float)
o['fee']=np.ceil(np.round(0.07*o.fee_multiplier.fillna(1)*10*o.q*(1-o.q)*100,6))/1000
o['ret']=(o.win-o.q-o.fee)*100      # taker c/contract after fee
o['mret']=(o.q-o.win)*100           # maker gross (other side), no maker fee
o['oos']=o.close_ts>=1782864000
o['size']=pd.cut(o.cost,[0,5,25,100,1000,1e9],labels=['<=5','5-25','25-100','100-1k','1k+'])
o['band']=pd.cut(o.q,[0,.1,.3,.5,.7,.9,1.0],labels=['0-10','10-30','30-50','50-70','70-90','90-100'])
o.to_parquet('o2.parquet')
def ci(d,col,w='n'):
    # contract-weighted mean, event-clustered CI
    g=d.groupby('event').apply(lambda x: pd.Series({'s':(x[col]*x[w]).sum(),'w':x[w].sum()}))
    mu=g.s.sum()/g.w.sum(); k=len(g)
    se=math.sqrt(((g.s-mu*g.w)**2).sum()*k/(k-1))/g.w.sum() if k>1 else float('nan')
    return f"{mu:+.1f}±{1.96*se:.1f}"
def ord_eq(d,col):
    # order-equal mean, event-clustered
    g=d.groupby('event')[col].agg(['sum','size']); mu=g['sum'].sum()/g['size'].sum(); k=len(g)
    se=math.sqrt(((g['sum']-mu*g['size'])**2).sum()*k/(k-1))/g['size'].sum() if k>1 else float('nan')
    return f"{mu:+.1f}±{1.96*se:.1f}"
print("TAKER return c/contract after fee, by order size ($ cost), ORDER-EQUAL, event-clustered 95% CI")
for per,d0 in (('IS',o[~o.oos]),('OOS',o[o.oos])):
    rows=[]
    for cat,d in d0.groupby('category'):
        if len(d)<20000: continue
        rows.append([cat]+[ord_eq(x,'ret') if len(x)>500 else '' for _,x in d.groupby('size',observed=False)]+[len(d)])
    print(per); print(pd.DataFrame(rows,columns=['cat','<=5','5-25','25-100','100-1k','1k+','orders']).to_string(index=False))
print("\nSmall orders (<=$25): share buying YES, avg price, by category")
s=o[o.cost<=25]
print(s.groupby('category').agg(n=('n','size'),yes_share=('yes','mean'),avg_q=('q','mean'),win=('win','mean'),ret=('ret','mean')).round(3))
print("\nSmall orders (<=$25) taker ret by side x band, non-sports vs sports, OOS / IS")
o['grp']=np.where(o.category=='Sports','Sports',np.where(o.category.isin(['Crypto','Commodities','Financials']),'Fin/Crypto','Other'))
s=o[o.cost<=25]
for (grp,oos),d in s.groupby(['grp','oos']):
    print(grp,'OOS' if oos else 'IS')
    print(pd.DataFrame({('YES' if y else 'NO'):{b:ord_eq(x,'ret') for b,x in dd.groupby('band',observed=False)} for y,dd in d.groupby('yes')}).to_string())
