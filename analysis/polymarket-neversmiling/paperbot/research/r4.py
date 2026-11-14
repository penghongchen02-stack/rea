import pickle,math,statistics,collections
W,S,samp=pickle.load(open("base.pkl","rb")); C=pickle.load(open("candles.pkl","rb"))
fee=lambda p:0.095*p*(1-p)
RET={}
for a in C:
    rows=C[a]; RET[a]={t:math.log(rows[t][3]/rows[t-60][3]) for t in rows if t-60 in rows}
SIG={}
def sig(a,t):
    k=(a,t//300)
    if k not in SIG:
        v=[RET[a][t//300*300-60*i] for i in range(1,61) if (t//300*300-60*i) in RET[a]]
        SIG[k]=statistics.pstdev(v) if len(v)>=30 else None
    return SIG[k]
recs=[]
for (slug,b,side),(ts,price) in samp.items():
    a,h,up,p2b,sp=W[slug]; st,d=S[slug]
    if a not in C or h not in ("5m","15m") or not p2b: continue
    rows=C[a]; m=int(ts//60*60); left=st+d-ts
    if left<3 or left>d or m not in rows or st-900 not in rows or st-60 not in rows or (st-d) not in rows: continue
    s=sig(a,st)
    if not s: continue
    px=rows[m][0]; cost=price if side=="buy" else 1-price; win=(up==1) if side=="buy" else (up==0); sgn=1 if side=="buy" else -1
    lead=math.log(px/p2b)/(s*math.sqrt(max(left,5)/60))
    prev=math.log(rows[st-60][3]/rows[st-d][0])/(s*math.sqrt(d/60)); prev15=math.log(rows[st-60][3]/rows[st-900][0])/(s*math.sqrt(15))
    v5=[RET[a].get(m-60*i) for i in range(1,6)]; volr=(statistics.pstdev(v5)/s) if all(x is not None for x in v5) else None
    lastz=RET[a].get(m-60,0)/s if m-60>=st else None
    recs.append(dict(slug=slug,ts=ts,h=h,a=a,sgn=sgn,cost=cost,ev=win-cost-fee(cost),raw=win-cost,lead=lead,prev=prev,prev15=prev15,volr=volr,lastz=lastz,frac=(ts-st)/d,left=left,hour=int(ts//3600)%24))
print("samples with features",len(recs))
tmid=statistics.median(r["ts"] for r in recs)
def show(label,cond,hz="5m"):
    by=collections.defaultdict(list)
    for r in recs:
        if r["h"]==hz and cond(r): by[r["slug"]].append(r)
    if len(by)<60: print(f"{label:66s} windows={len(by)} (too few)"); return
    ev=[statistics.mean(x["ev"] for x in v) for v in by.values()]; raw=[statistics.mean(x["raw"] for x in v) for v in by.values()]
    h0=[statistics.mean(x["ev"] for x in v) for v in by.values() if v[0]["ts"]<tmid]; h1=[statistics.mean(x["ev"] for x in v) for v in by.values() if v[0]["ts"]>=tmid]
    m=statistics.mean(ev); se=statistics.pstdev(ev)/math.sqrt(len(ev))
    print(f"{label:66s} windows={len(by):5d} EV after fee={m*100:+5.1f}c (se {se*100:.1f}, t {m/se:+.1f}) before fee={statistics.mean(raw)*100:+5.1f}c halves {statistics.mean(h0)*100 if h0 else 0:+.1f}/{statistics.mean(h1)*100 if h1 else 0:+.1f}")
for hz in ("5m","15m"):
    print("\n=====",hz)
    show("ALL samples (baseline)",lambda r:True,hz)
    ld=lambda r:r["sgn"]*r["lead"]
    show("side = leader (lead>0.3sd), cost 0.55-0.90",lambda r:ld(r)>0.3 and .55<=r["cost"]<.9,hz)
    show("side = trailer, cost 0.10-0.45",lambda r:ld(r)<-0.3 and .10<=r["cost"]<.45,hz)
    show("side AGAINST prior-15-min move (>1.5sd)",lambda r:r["sgn"]*r["prev15"]<-1.5,hz)
    show("side WITH prior-15-min move (>1.5sd)",lambda r:r["sgn"]*r["prev15"]>1.5,hz)
    show("side AGAINST prior-15-min move (>1.0sd)",lambda r:r["sgn"]*r["prev15"]<-1.0,hz)
    show("side WITH prior-15-min move (>1.0sd)",lambda r:r["sgn"]*r["prev15"]>1.0,hz)
    show("side AGAINST previous window's move (>1sd)",lambda r:r["sgn"]*r["prev"]<-1,hz)
    show("side WITH previous window's move (>1sd)",lambda r:r["sgn"]*r["prev"]>1,hz)
    show("leader 0.55-0.90 AND against prior-15 (>1sd)",lambda r:ld(r)>0.3 and .55<=r["cost"]<.9 and r["sgn"]*r["prev15"]<-1,hz)
    show("leader 0.55-0.90 AND with prior-15 (>1sd)",lambda r:ld(r)>0.3 and .55<=r["cost"]<.9 and r["sgn"]*r["prev15"]>1,hz)
    show("leader 0.55-0.90, calm (5-min vol <0.6x hour)",lambda r:ld(r)>0.3 and .55<=r["cost"]<.9 and r["volr"] is not None and r["volr"]<0.6,hz)
    show("leader 0.55-0.90, stormy (5-min vol >1.5x hour)",lambda r:ld(r)>0.3 and .55<=r["cost"]<.9 and r["volr"] is not None and r["volr"]>1.5,hz)
    show("trailer <=0.30, stormy",lambda r:ld(r)<-0.3 and r["cost"]<=.30 and r["volr"] is not None and r["volr"]>1.5,hz)
    show("side AGAINST a >2sd last-minute spike",lambda r:r["lastz"] is not None and r["sgn"]*r["lastz"]<-2,hz)
    show("side WITH a >2sd last-minute spike",lambda r:r["lastz"] is not None and r["sgn"]*r["lastz"]>2,hz)
    show("underdog at <=0.15 in first 40% of window",lambda r:r["cost"]<=.15 and r["frac"]<=.4,hz)
    show("underdog at <=0.25 in first 30% of window",lambda r:r["cost"]<=.25 and r["frac"]<=.3,hz)
    show("favourite at >=0.85 in first 40% of window",lambda r:r["cost"]>=.85 and r["frac"]<=.4,hz)
    show("favourite >=0.90, last 20% of window",lambda r:r["cost"]>=.90 and r["frac"]>=.8,hz)
    show("favourite 0.80-0.90, last 20% of window",lambda r:.8<=r["cost"]<.9 and r["frac"]>=.8,hz)
    for lo,hi in ((0,.2),(.2,.4),(.4,.6),(.6,.8),(.8,1.01)):
        show(f"leader 0.55-0.90, window {int(lo*100)}-{int(min(hi,1)*100)}% elapsed",lambda r:ld(r)>0.3 and .55<=r["cost"]<.9 and lo<=r["frac"]<hi,hz)
    for a in ("btc","eth","sol","xrp"): show(f"all, {a}",lambda r:r["a"]==a,hz)
    for h0,h1 in ((0,7),(7,13),(13,17),(17,21),(21,24)): show(f"all, hours {h0:02d}-{h1:02d} UTC",lambda r:h0<=r["hour"]<h1,hz)
    show("cost where fee<0.6c (<=0.07 or >=0.93)",lambda r:r["cost"]<=.07 or r["cost"]>=.93,hz)
pickle.dump(recs,open("samp_feat.pkl","wb"))
