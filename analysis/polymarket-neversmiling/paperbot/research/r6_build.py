"""Every trade (one bot in one window) on every dashboard, with how it was entered and how it ended."""
import sqlite3,glob,re,calendar,pickle,math,statistics,collections
R=""
C=pickle.load(open(R+"candles.pkl","rb"))
RET={a:{t:math.log(C[a][t][3]/C[a][t-60][3]) for t in C[a] if t-60 in C[a]} for a in C}
SIG={}
def sig(a,t):
    k=(a,t//300)
    if k not in SIG:
        v=[RET[a][t//300*300-60*i] for i in range(1,61) if (t//300*300-60*i) in RET[a]]
        SIG[k]=statistics.pstdev(v) if len(v)>=30 else None
    return SIG[k]
rx=re.compile(r"updown-(5m|15m|1h)-(\d{4})-(\d\d)-(\d\d)-(\d\d)(\d\d)z"); DUR={"5m":300,"15m":900,"1h":3600}
skip=("bak","archive","stopflip","br133")
T=[]
for p in glob.glob("paperbot-data/*/paper.db")+["paperbot-data/paper.db"]:
    dash=p.split("/")[-2] if p.count("/")==2 else "std"
    if dash=="paperbot-data": dash="std"
    if any(s in dash for s in skip): continue
    c=sqlite3.connect(p)
    F=collections.defaultdict(list)
    for ts,bot,slug,side,price,qty,fee,liq,tag,realized in c.execute("select ts,bot,slug,side,price,qty,fee,liq,tag,realized from fills order by ts"):
        F[(bot,slug)].append((ts,side,price,qty,fee or 0,liq,realized or 0))
    seen=set()
    for ts,bot,slug,asset,hz,up,p2b,spx,fq,ps,pt,fees,nf in c.execute("select ts,bot,slug,asset,horizon,up,p2b,settle_px,final_qty,pnl_settle,pnl_total,fees,fills from settlements where fills>0"):
        if (bot,slug) in seen: continue
        seen.add((bot,slug)); f=F.get((bot,slug))
        if not f: continue
        m=rx.search(slug); t0,side0,px0,q0,_,liq0,_=f[0]
        ent=[x for x in f if x[1]==side0]; opp=[x for x in f if x[1]!=side0]
        cost=lambda s,p:(p if s=="buy" else 1-p)
        stake=sum(cost(x[1],x[2])*x[3] for x in ent); eq=sum(x[3] for x in ent)
        c0=cost(side0,px0); avgc=stake/eq if eq else c0
        worse=sum(1 for x in ent[1:] if cost(x[1],x[2])<=c0-0.03); better=sum(1 for x in ent[1:] if cost(x[1],x[2])>=c0+0.03)
        d=dict(dash=dash,bot=bot,slug=slug,asset=asset,hz=hz,up=up,net=(pt or 0)-(fees or 0),gross=pt or 0,fees=fees or 0,settle=ps or 0,fq=fq or 0,
               side=side0,c0=c0,avgc=avgc,stake=stake,nent=len(ent),nopp=len(opp),liq0=liq0,maker_share=sum(x[3] for x in ent if x[5]=="maker")/eq if eq else 0,
               worse=worse,better=better,realized=sum(x[6] for x in f),t0=t0,span=ent[-1][0]-ent[0][0],kind="updown" if m else "ladder",
               exit_ts=opp[0][0] if opp else None, exit_cost=cost(side0,opp[0][2]) if opp else None)
        if m:
            st=calendar.timegm((int(m[2]),int(m[3]),int(m[4]),int(m[5]),int(m[6]),0)); du=DUR[m[1]]; d.update(st=st,dur=du,frac=(t0-st)/du,left=st+du-t0)
            rows=C.get(asset); mnt=int(t0//60*60); s=sig(asset,st) if rows else None
            if rows and s and p2b and mnt in rows and (st-900) in rows and (st-60) in rows:
                sg=1 if side0=="buy" else -1
                d["lead"]=sg*math.log(rows[mnt][0]/p2b)/(s*math.sqrt(max(d["left"],5)/60))
                d["prev15"]=sg*math.log(rows[st-60][3]/rows[st-900][0])/(s*math.sqrt(15))
                if mnt-60 in RET[asset] and mnt-60>=st-60: d["lastz"]=sg*RET[asset][mnt-60]/s
                v5=[RET[asset].get(mnt-60*i) for i in range(1,6)]
                if all(x is not None for x in v5): d["volr"]=statistics.pstdev(v5)/s
                # did the side lead at the end? how far did the coin move against after entry
                endm=(st+du)//60*60-60
                if endm in rows: d["move_after"]=sg*math.log(rows[endm][3]/rows[mnt][0])/(s*math.sqrt(max(d["left"],5)/60))
            d["won"]=(up==1)==(side0=="buy")
        T.append(d)
    print(dash,len(seen),flush=True)
pickle.dump(T,open(R+"trades.pkl","wb")); print("trades",len(T))
