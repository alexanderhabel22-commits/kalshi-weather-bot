import pandas as pd, numpy as np, math, re
o=pd.read_parquet('o2.parquet'); m=pd.read_parquet('markets.parquet')[['ticker','series','volume']]
o=o.merge(m,on='ticker')
sets={'Year-end lists (Spotify top artist, TIME, Google search, Xmas song)':r'^KX(TOPARTIST|TIME$|RANKLISTGOOGLESEARCH|RANKLISTTIMEMAG|SPOTIFYXMAS|TOPALBUMSPOTIFY|RANKLISTAIWFCIS|TOPMONTHLY)',
'Super Bowl novelty (halftime song, guests, performers, setlist)':r'^KX(FIRSTSUPERBOWLSONG|SBGUESTS|PERFORMSUPERBOWL|SBSETLISTS|SB[A-Z]*MENTION|SUPERBOWL)',
'Awards (Oscars, Grammys, Globes, Game Awards, Emmys)':r'^KX(OSCAR|GRAM|GG|GAMEAWARDS|EMMY|SAG|BAFTA)',
'Reality TV winners (DWTS, Survivor, Traitors, Beast Games)':r'^KX(DANCINGWITHTHESTARS|SURVIVOR$|TRAITORS|MRBEASTGAMES|BACHELOR|LOVEISLAND|BIGBROTHER)',
'Sports-broadcast Mentions (NFL, NBA, NCAA, SNF, TNF)':r'^KX(NFL|NBA|NCAA|NCAAB|SNF|TNF|MNF|CFB|WO|FIGHT)MENTION',
'Trump / politics Mentions':r'^KX(TRUMPMENTION|TRUMPSAY|PRESMENTION|VANCEMENTION|SECPRESSMENTION|MAMDANIMENTION)',
'Late night / TV Mentions':r'^KX(LATENIGHT|COLBERT|KIMMEL|SNL|FTN|LASTWORD|FOXNEWS|SURVIVOR)MENTION'}
def st(d,col):
    if len(d)<40: return ''
    ge=d.groupby('event')[col].agg(['sum','size']); mu=ge['sum'].sum()/ge['size'].sum(); k=len(ge)
    se=math.sqrt(((ge['sum']-mu*ge['size'])**2).sum()*k/(k-1))/ge['size'].sum() if k>1 else float('nan')
    return f"{mu:+.1f}±{1.96*se:.1f}"
for nm,rx in sets.items():
    d=o[o.series.str.match(rx)]
    if not len(d): print(nm,'none'); continue
    sm=d[d.cost<=25]
    print(f"\n{nm}: {d.ticker.nunique()} mkts, {d.event.nunique()} events, {len(d)} orders; small share of contracts {sm.n.sum()/d.n.sum():.2f}, small orders buying YES {sm.yes.mean():.2f}")
    print("  small-order takers c/ct:", st(sm,'ret'), "| big (100+):", st(d[d.cost>100],'ret'))
    for lo,hi in ((0,.1),(.1,.3),(.3,.5),(.5,.9),(.9,1)):
        x=sm[sm.yes&(sm.q>=lo)&(sm.q<hi)]
        print(f"  small YES buyers at {int(lo*100)}-{int(hi*100)}c: {st(x,'ret')} ({len(x)} orders, {x.event.nunique()} events)")
