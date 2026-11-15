"""Why the money was lost, fill by fill: an exact split of the net result, what-if checks, and bet size against bank."""
import sqlite3,glob,re,calendar,pickle,math,statistics,collections,json
rx=re.compile(r"updown-(5m|15m|1h)-(\d{4})-(\d\d)-(\d\d)-(\d\d)(\d\d)z"); DUR={"5m":300,"15m":900,"1h":3600}
skip=("bak","archive","stopflip","br133")
cost=lambda s,p:(p if s=="buy" else 1-p)
TB=[.1,.25,.5,.75,.9,1.01]; PB=[.1,.2,.3,.4,.5,.6,.7,.8,.9,1.01]
def bi(x,e):
    for i,v in enumerate(e):
        if x<v: return i
    return len(e)-1
BANK={"dollar1000":30000,"dollar30":1000,"dollar":100,"std":100,"top20":5000,"bonelab":1000,"evolve":1000,"tryout":1000,"champion":1000,"rep1000":1000,"fast20":1000,"sureshot":1000}
trades=[]; calib=collections.defaultdict(dict)   # (hz,tb,pb) -> slug -> [n,wins]
for p in glob.glob("paperbot-data/*/paper.db")+["paperbot-data/paper.db"]:
    dash=p.split("/")[-2]; dash="std" if dash=="paperbot-data" else dash
    if any(s in dash for s in skip): continue
    c=sqlite3.connect(p); F=collections.defaultdict(list)
    for ts,bot,slug,side,price,qty,fee,liq in c.execute("select ts,bot,slug,side,price,qty,fee,liq from fills order by ts"): F[(bot,slug)].append((ts,side,price,qty,fee or 0,liq))
    seen=set()
    for ts,bot,slug,asset,hz,up,fq,pt,fees in c.execute("select ts,bot,slug,asset,horizon,up,final_qty,pnl_total,fees from settlements where fills>0 and up is not null"):
        m=rx.search(slug)
        if (bot,slug) in seen or not m: continue
        seen.add((bot,slug)); f=F.get((bot,slug))
        if not f: continue
        st=calendar.timegm((int(m[2]),int(m[3]),int(m[4]),int(m[5]),int(m[6]),0)); du=DUR[m[1]]; side0=f[0][1]; win=1 if (up==1)==(side0=="buy") else 0
        ent=[x for x in f if x[1]==side0]; opp=[x for x in f if x[1]!=side0]; c0=cost(side0,ent[0][2])
        fl=[]
        for i,x in enumerate(ent):
            cc=cost(x[1],x[2]); fr=min(max((x[0]-st)/du,0),0.999)
            fl.append(dict(i=i,c=cc,q=x[3],fee=x[4],mk=x[5]=="maker",fr=fr,rel=cc-c0,dt=x[0]-ent[0][0]))
            if x[5]=="taker":
                d=calib[(hz,bi(fr,TB),bi(cc,PB))].setdefault(slug+side0,[0,win]); d[0]+=1
        trades.append(dict(dash=dash,bot=bot,slug=slug,hz=hz,asset=asset,win=win,fl=fl,net=(pt or 0)-(fees or 0),gross=pt or 0,fees=fees or 0,held=len(opp)==0,soldall=abs(fq or 0)<1e-6 and len(opp)>0,
                           stake=sum(x["c"]*x["q"] for x in fl),qty=sum(x["q"] for x in fl),ts=ts,
                           exitv=sum(cost(side0,x[2])*x[3] for x in opp),exitq=sum(x[3] for x in opp),exitfr=((opp[0][0]-st)/du) if opp else None))
WF={k:sum(v[1] for v in d.values())/len(d) for k,d in calib.items() if len(d)>=25}
print("trades",len(trades),"calibration cells",len(WF))
def wf(t,x): return WF.get((t["hz"],bi(x["fr"],TB),bi(x["c"],PB)))
# ---------- 1. exact split
tot=collections.Counter()
for t in trades:
    hold=sum(x["q"]*(t["win"]-x["c"]) for x in t["fl"])      # what the entries were worth if every one was held to the close
    price=sel=0.0; ok=True
    for x in t["fl"]:
        w=wf(t,x)
        if w is None: ok=False; break
        price+=x["q"]*(w-x["c"]); sel+=x["q"]*(t["win"]-w)
    t["hold"]=hold; t["exit_eff"]=t["gross"]-hold
    tot["net"]+=t["net"]; tot["fees"]+=t["fees"]; tot["gross"]+=t["gross"]; tot["hold"]+=hold; tot["exit"]+=t["gross"]-hold; tot["stake"]+=t["stake"]
    if ok: tot["price"]+=price; tot["sel"]+=sel; tot["covered"]+=hold
    t["price"]=price if ok else None; t["sel"]=sel if ok else None
print("\n== 1. EXACT SPLIT of the net result")
print(f"  stake ${tot['stake']:,.0f}   net ${tot['net']:,.0f}")
print(f"  fees                                                   ${-tot['fees']:>12,.0f}")
print(f"  selling before the close (actual minus hold-to-close)  ${tot['exit']:>12,.0f}")
print(f"  entries, if all held to the close                      ${tot['hold']:>12,.0f}   (of which ${tot['covered']:,.0f} is in cells with enough data:)")
print(f"     paid more than that price wins on average           ${tot['price']:>12,.0f}")
print(f"     bought at moments worse than average for that price ${tot['sel']:>12,.0f}")
# ---------- 2. per-fill: first fill vs add-ons (held-to-close and all)
def agg(rows): 
    st=sum(c*q for c,q,_,_ in rows); pl=sum(q*(w-c)-f for c,q,w,f in rows); return len(rows),st,pl
print("\n== 2. EACH ENTRY FILL valued as if held to the close, after its own fee")
G=collections.defaultdict(list)
for t in trades:
    for x in t["fl"]:
        r=(x["c"],x["q"],t["win"],x["fee"])
        G[("order","1 first fill of the window" if x["i"]==0 else "2 second or third" if x["i"]<3 else "3 fourth to ninth" if x["i"]<9 else "4 tenth or later")].append(r)
        if x["i"]>0: G[("add","1 add 10c+ cheaper than the first" if x["rel"]<=-.10 else "2 add 3-10c cheaper" if x["rel"]<=-.03 else "3 add within 3c" if x["rel"]<.03 else "4 add 3-10c dearer" if x["rel"]<.10 else "5 add 10c+ dearer")].append(r)
        G[("liq","resting fill" if x["mk"] else "taker fill")].append(r)
        G[("when",["1 first 10%","2 10-25%","3 25-50%","4 50-75%","5 75-90%","6 last 10%"][bi(x["fr"],TB)])].append(r)
        G[("price",["1 under 10c","2 10-20c","3 20-30c","4 30-40c","5 40-50c","6 50-60c","7 60-70c","8 70-80c","9 80-90c","90c+"][bi(x["c"],PB)])].append(r)
for grp in ("order","add","liq","when","price"):
    print(" ",grp)
    for k in sorted(k for k in G if k[0]==grp):
        n,st,pl=agg(G[k]); print(f"    {k[1]:36s} fills {n:7d}  stake ${st:11,.0f}  result ${pl:10,.0f}  {pl/st*100:+6.1f}%")
# heat map when x price
print("\n  result per $ staked, WHEN (rows) x PRICE (columns), taker and resting fills together; blank = under $20,000 staked")
H=collections.defaultdict(list)
for t in trades:
    for x in t["fl"]: H[(bi(x["fr"],TB),min(bi(x["c"],PB)//2,4))].append((x["c"],x["q"],t["win"],x["fee"]))
print("                 under20c   20-40c   40-60c   60-80c    80c+")
for i,l in enumerate(["first 10%","10-25%","25-50%","50-75%","75-90%","last 10%"]):
    row=[]
    for j in range(5):
        n,st,pl=agg(H[(i,j)]) if H.get((i,j)) else (0,0,0)
        row.append(f"{pl/st*100:+7.1f}%" if st>=20000 else "      . ")
    print(f"    {l:10s} "+" ".join(row))
# ---------- 3. what-ifs
print("\n== 3. WHAT IF")
first=sum(t["fl"][0]["q"]*(t["win"]-t["fl"][0]["c"])-t["fl"][0]["fee"] for t in trades); fs=sum(t["fl"][0]["q"]*t["fl"][0]["c"] for t in trades)
print(f"  every bot had made only its FIRST fill in each window and held it: ${first:,.0f} on ${fs:,.0f} staked ({first/fs*100:+.1f}%)   actual: ${tot['net']:,.0f}")
sold=[t for t in trades if not t["held"]]
print(f"  trades that sold something before the close: {len(sold)}; actual gross ${sum(t['gross'] for t in sold):,.0f}; if they had held everything ${sum(t['hold'] for t in sold):,.0f}; so selling changed the result by ${sum(t['exit_eff'] for t in sold):,.0f} (plus exit fees)")
sa=[t for t in sold if t["soldall"]]; won_after=sum(1 for t in sa if t["win"])/len(sa)
print(f"    of {len(sa)} trades sold out completely, the side they sold went on to WIN in {won_after*100:.1f}%; they sold at an average of {sum(t['exitv'] for t in sa)/sum(t['exitq'] for t in sa)*100:.0f}c having paid {sum(t['stake'] for t in sa)/sum(t['qty'] for t in sa)*100:.0f}c")
# equal-size what-if per bot: same trades, every window the same dollars
bb=collections.defaultdict(list)
for t in trades: bb[(t["dash"],t["bot"])].append(t)
eq=act=0.0
for k,v in bb.items():
    S=sum(t["stake"] for t in v); avg=S/len(v)
    eq+=sum((t["net"]/t["stake"])*avg for t in v if t["stake"]>0); act+=sum(t["net"] for t in v)
print(f"  every bot had staked the SAME dollars in each of its windows (its own average): ${eq:,.0f}   actual ${act:,.0f}   so uneven sizing cost ${act-eq:,.0f}")
# ---------- 4. size against bank, ruin
print("\n== 4. BET SIZE against the bank (bots that lost $5,000+ or their whole bank)")
print(f"  {'dashboard':11s} {'bot':28s} {'windows':>7s} {'net $':>9s} {'avg stake':>9s} {'% of bank':>9s} {'biggest':>9s} {'return/stake':>12s} {'fees $':>8s} {'hours':>6s}")
rows=[]
for (d,b),v in bb.items():
    net=sum(t["net"] for t in v); bank=BANK.get(d,1000)
    if net>-5000 and net>-0.97*bank: continue
    S=sum(t["stake"] for t in v); rows.append((net,d,b,len(v),S/len(v),S/len(v)/bank*100,max(t["stake"] for t in v)/bank*100,net/S*100,sum(t["fees"] for t in v),(max(t["ts"] for t in v)-min(t["ts"] for t in v))/3600))
for r in sorted(rows)[:22]: print(f"  {r[1]:11s} {r[2]:28s} {r[3]:7d} {r[0]:9.0f} {r[4]:9.0f} {r[5]:8.1f}% {r[6]:8.0f}% {r[7]:11.1f}% {r[8]:8.0f} {r[9]:6.1f}")
print("  bots that lost at least 97% of their bank:",sum(1 for (d,b),v in bb.items() if sum(t['net'] for t in v)<=-0.97*BANK.get(d,1000)),"of",len(bb))
# ---------- 5. families
def fam(b):
    if b.startswith("br") and b[2:3].isdigit(): return "br### Bonelab copies"
    for p,n in (("xarb","xarb cross-market"),("rep_","rep_ trader copies"),("new_rep","new_rep trader copies"),("new_arb","new_arb"),("new2_","new2"),("new3_","new3"),("new4_","new4"),("new_","new (first 20)"),("ss","sureshot ss"),("rnd","rnd random controls"),("v_","v_ model variants"),("fast_pickoff","fast_pickoff"),("bonereaper","bonereaper"),("whiskas","whiskas"),("all_horizons","all_horizons"),("consensus","consensus"),("stairstep","stairstep")):
        if b.startswith(p): return n
    return re.sub(r"(_[a-z]?\d+)+$","",b)
ff=collections.defaultdict(lambda:[0,0.0,0.0,0.0,0.0,0.0,0.0,0,0])
for t in trades:
    x=ff[fam(t["bot"])]; x[0]+=1; x[1]+=t["net"]; x[2]+=t["stake"]; x[3]+=t["fees"]; x[4]+=t["exit_eff"]
    x[5]+=t["price"] or 0; x[6]+=t["sel"] or 0; x[7]+=len(t["fl"]); x[8]+=1 if t["fl"][0]["fr"]<.25 else 0
print("\n== 5. BY FAMILY of bot (worst 16 and best 6)")
print(f"  {'family':28s} {'trades':>7s} {'net $':>9s} {'stake $':>10s} {'ret':>6s} {'fees $':>8s} {'early exits $':>12s} {'price $':>9s} {'timing $':>9s} {'fills/win':>9s} {'early%':>6s}")
o=sorted(ff.items(),key=lambda kv:kv[1][1])
for k,x in o[:16]+o[-6:]: print(f"  {k[:28]:28s} {x[0]:7d} {x[1]:9.0f} {x[2]:10.0f} {x[1]/x[2]*100:5.1f}% {-x[3]:8.0f} {x[4]:12.0f} {x[5]:9.0f} {x[6]:9.0f} {x[7]/x[0]:9.1f} {x[8]/x[0]*100:5.0f}%")
# ---------- 6. by day
dd=collections.defaultdict(lambda:[0,0.0,0.0,0.0])
import time
for t in trades: x=dd[time.strftime("%m-%d",time.gmtime(t["ts"]))]; x[0]+=1; x[1]+=t["net"]; x[2]+=t["stake"]; x[3]+=t["fees"]
print("\n== 6. BY DAY"); 
for k in sorted(dd): x=dd[k]; print(f"  {k}  trades {x[0]:6d}  net ${x[1]:10,.0f}  stake ${x[2]:11,.0f}  fees ${x[3]:8,.0f}  {x[1]/x[2]*100:+.1f}%")
# ---------- 7. herding: agreement
print("\n== 7. HOW ALIKE the bots on one dashboard bet (windows with 5+ of its bots)")
hh=collections.defaultdict(lambda:collections.defaultdict(lambda:[0.0,0.0,0]))
for t in trades:
    x=hh[t["dash"]][t["slug"]]; up=(t["win"]==1)   # majority measured on the winning/losing split
    x[0]+=t["stake"] if t["win"] else 0; x[1]+=t["stake"]; x[2]+=1
for d,ws in hh.items():
    v=[(max(a,b-a)/b,n,b) for a,b,n in ws.values() if n>=5 and b>0]
    if len(v)<30: continue
    print(f"  {d:11s} windows {len(v):5d}  average share of the money on ONE side {statistics.mean(x[0] for x in v)*100:5.1f}%   bots a window {statistics.mean(x[1] for x in v):5.1f}   windows with 90%+ on one side {sum(1 for x in v if x[0]>=.9)/len(v)*100:4.0f}%")
pickle.dump(trades,open("trades7.pkl","wb"))
