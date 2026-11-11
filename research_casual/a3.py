import pandas as pd, numpy as np, math, re, sys
L=pd.read_parquet('L.parquet')
m=pd.read_parquet('markets.parquet')[['ticker','series','title','volume','open_ts']]
L=L.merge(m,on='ticker')
def meq(d,col='mret'):
    pm=d.groupby(['event','ticker'])[col].mean().reset_index()
    ge=pm.groupby('event')[col].agg(['sum','size']); mu=ge['sum'].sum()/ge['size'].sum(); k=len(ge)
    se=math.sqrt(((ge['sum']-mu*ge['size'])**2).sum()*k/(k-1))/ge['size'].sum() if k>1 else float('nan')
    return mu,1.96*se,len(pm)
fmt=lambda r:f"{r[0]:+.1f}±{r[1]:.1f} ({r[2]})"
H=L[L.smc>=0.5]
print("Casual-heavy (50%+ of early contracts from <=$25 orders): which series (markets, share of that series' sampled markets)")
allm=L.groupby(['grp','series']).ticker.nunique()
hm=H.groupby(['grp','series']).ticker.nunique()
x=pd.DataFrame({'heavy':hm,'all':allm}).dropna(); x['share']=x.heavy/x['all']
print(x.sort_values('heavy',ascending=False).head(60).to_string())
# sports type
PERIOD=re.compile(r"(1H|2H|1P|2P|3P|1Q|2Q|3Q|4Q|HALF|1ST|F5|INNING|SET\d|MAP\d)")
PROP=re.compile(r"(PTS|REB|AST|3PT|PRA|PASS|RUSH|REC|TD|YDS|HR|HIT|KS|STRIKEOUT|SOG|GOAL|SCORER|SHOT|LADDER|ACE|SACK|RBI|SAVE|KILLS|BLK|STL|DD)")
FUT=re.compile(r"(CHAMP|MVP|WINS|PLAYOFF|SB$|WS$|CUP|AWARD|ROY|DRAFT|CONF|DIV|TITLE|ADVANCE|COACH|TRADE|JOIN|SEASON|LEADER|TOP)")
def st(s):
    s=s[2:] if s.startswith('KX') else s
    if PERIOD.search(s): return 'period'
    if 'SPREAD' in s: return 'spread'
    if 'TOTAL' in s: return 'total'
    if 'BTTS' in s: return 'btts'
    if FUT.search(s): return 'futures'
    if re.search(r"(GAME|MATCH|FIGHT|BOUT|RACE|WIN$)",s): return 'game'
    if PROP.search(s): return 'prop'
    return 'other'
S=L[L.grp=='Sports'].copy(); S['stype']=S.series.map(st)
S['bucket']=pd.cut(S.smc,[-1,.2,.5,1.01],labels=['<20%','20-50%','50%+'])
print("\nSports by type x casual share: FRONT maker c/ct (market-equal), IS | OOS; then BACK")
for sub,name in ((S,'FRONT'),(S[S.thr],'BACK')):
    rows=[]
    for (stp,b),d in sub.groupby(['stype','bucket'],observed=True):
        r=[stp,b]
        for oos in (False,True):
            dd=d[d.oos==oos]; r.append(fmt(meq(dd)) if dd.ticker.nunique()>=40 else '')
        rows.append(r)
    print(name); print(pd.DataFrame(rows,columns=['type','casual','IS','OOS']).to_string(index=False))
print("\nCasual-heavy 50%+ markets, maker by the price the MAKER pays (1-q), FRONT, IS | OOS")
H=L[L.smc>=0.5].copy(); H['mp']=pd.cut(1-H.q,[0,.1,.3,.5,.7,.9,1],labels=['0-10','10-30','30-50','50-70','70-90','90-100'])
for grp in ('Sports','Mentions','Entertainment'):
    for b,d in H[H.grp==grp].groupby('mp',observed=True):
        print(grp,b,' | '.join(fmt(meq(d[d.oos==o])) for o in (False,True)))
