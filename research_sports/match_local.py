import csv,gzip,re,collections,datetime as dt,sys
D='/tmp/claude-0/d3/'
games=[r for r in csv.DictReader(gzip.open(D+'games.csv.gz','rt')) if r['provider_id'] in ('100','')]
ks=list(csv.DictReader(gzip.open(D+'kalshi.csv.gz','rt')))
MON={m:i+1 for i,m in enumerate('JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC'.split())}
def norm(s): return re.sub(r'[^a-z0-9 ]','',(s or '').lower().replace('&','').replace('st.','state').replace(' st ',' state ')).strip()
bykey=collections.defaultdict(list)
for g in games:
    st=dt.datetime.fromisoformat(g['start'].replace('Z','+00:00'))
    et=(st-dt.timedelta(hours=5)).date()  # ET-ish (EST); DST errors ok with +-1 search
    g['_st']=st
    bykey[(g['league'],et)].append(g)
def names(g,side):
    return {norm(g[side+'_'+k]) for k in ('abbr','loc','short','name')}
def tm_match(team,g,side):
    t=norm(team); n=names(g,side)
    if t in n: return 3
    p=t.split(' ')
    if len(p)==2 and (p[0] in n and p[1] in n): return 3
    if any(t and (t==x.split(' ')[0] or x.startswith(t+' ')) for x in n): return 1
    return 0
out=[];um=collections.Counter();tot=collections.Counter()
ev=collections.defaultdict(list)
for k in ks: ev[(k['league'],k['event'])].append(k)
for (lg,e),ms in ev.items():
    tot[lg]+=1
    m=re.search(r'-(\d\d)([A-Z]{3})(\d\d)',e)
    if not m: um[lg]+=1; continue
    d=dt.date(2000+int(m[1]),MON[m[2]],int(m[3]))
    best=None
    for dd in (d,d-dt.timedelta(1),d+dt.timedelta(1)):
        for g in bykey.get((lg,dd),[]):
            sc=0;assign={}
            for mk in ms:
                hs,as_=tm_match(mk['team'],g,'h'),tm_match(mk['team'],g,'a')
                if max(hs,as_)==0: continue
                assign[mk['ticker']]='h' if hs>=as_ else 'a'; sc+=max(hs,as_)
            if len(set(assign.values()))==len(assign) and assign and (best is None or sc>best[0]) and (dd==d or sc>=4):
                best=(sc,g,assign)
        if best and best[0]>=4: break
    if not best: um[lg]+=1; continue
    sc,g,assign=best
    for mk in ms:
        if mk['ticker'] in assign:
            out.append({**mk,'side':assign[mk['ticker']],'event_id':g['event_id'],'start_ts':int(g['_st'].timestamp())})
for lg in tot: print(lg,'events',tot[lg],'unmatched',um[lg])
print(len(out))
import pickle;pickle.dump(out,open('/tmp/claude-0/work/matched.pkl','wb'))
with gzip.open('/home/claude/kalshi-weather-bot/research_sports/matched.csv.gz','wt',newline='') as fh:
    w=csv.writer(fh);w.writerow(['ticker','series','hist','start_ts'])
    for o in out: w.writerow([o['ticker'],o['ticker'].split('-')[0],o['hist'],o['start_ts']])
