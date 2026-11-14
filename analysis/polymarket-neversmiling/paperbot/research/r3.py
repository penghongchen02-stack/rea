import pickle,math,statistics,collections
C=pickle.load(open("candles.pkl","rb"))
phi=lambda z:0.5*(1+math.erf(z/math.sqrt(2)))
def series(a):
    rows=C[a]; ts=sorted(rows); close={}; 
    return rows
def build(dur):
    recs=[]
    for a in C:
        rows=C[a]; t_all=sorted(rows); t0=t_all[0]; t1=t_all[-1]
        # 1-min log returns
        ret={}
        for t in t_all:
            if t-60 in rows: ret[t]=math.log(rows[t][3]/rows[t-60][3])
        st=(t0//dur+2)*dur+3600
        while st+dur<=t1:
            mins=[st+60*i for i in range(dur//60)]
            if all(m in rows for m in mins) and all((st-60*i) in ret for i in range(1,61)):
                op=rows[st][0]; cl=rows[mins[-1]][3]; up=1 if cl>=op else 0
                sig=statistics.pstdev([ret[st-60*i] for i in range(1,61)]) or 1e-6
                sig5=statistics.pstdev([ret[st-60*i] for i in range(1,6)]) or 1e-6
                prev=math.log(rows[st-60][3]/rows[st-dur][0]) if (st-dur) in rows else 0.0
                prev15=math.log(rows[st-60][3]/rows[st-900][0]) if (st-900) in rows else 0.0
                for k in range(1,dur//60):          # decision after k minutes
                    px=rows[mins[k-1]][3]; left=dur//60-k
                    z=math.log(px/op)/(sig*math.sqrt(left)); 
                    hi=max(rows[m][1] for m in mins[:k]); lo=min(rows[m][2] for m in mins[:k])
                    lastret=ret.get(mins[k-1],0.0)
                    ups=sum(1 for m in mins[:k] if ret.get(m,0)>0); dns=sum(1 for m in mins[:k] if ret.get(m,0)<0)
                    recs.append(dict(a=a,st=st,k=k,left=left,z=z,p=phi(z),up=up,prev=prev/ (sig*math.sqrt(dur/60)),prev15=prev15/(sig*math.sqrt(15)),volr=sig5/sig,
                                     oneway=(lo>=op) if px>op else (hi<=op),lastz=lastret/sig,ups=ups,dns=dns,hour=(st//3600)%24,px=px,op=op,hi=hi,lo=lo,sig=sig))
            st+=dur
    return recs
def lift(recs,cond,label,minn=300):
    """mean(outcome - model prob) for records where cond is True, signed toward the favoured side given by side(r) (+1 up)"""
    xs=[]; 
    half={0:[],1:[]}
    mid=statistics.median(r["st"] for r in recs)
    for r in recs:
        s=cond(r)
        if not s: continue
        e=(r["up"]-r["p"])*s; xs.append(e); half[r["st"]>mid].append(e)
    if len(xs)<minn: print(f"{label:58s} n={len(xs)} (too few)"); return
    m=statistics.mean(xs); se=statistics.pstdev(xs)/math.sqrt(len(set(xs)) if False else len(xs)/2)   # /2: crude allowance for overlap across k and coins
    h=[statistics.mean(v)*100 if v else float('nan') for v in (half[0],half[1])]
    print(f"{label:58s} n={len(xs):6d} lift={m*100:+5.2f}c se~{se*100:.2f} t={m/se:+.1f} halves {h[0]:+.2f}/{h[1]:+.2f}")
for dur in (300,900):
    R=build(dur); print(f"\n=== {dur//60}-minute windows: {len(set((r['a'],r['st']) for r in R))} windows; base up rate {statistics.mean(r['up'] for r in R if r['k']==1):.3f}")
    print("calibration of the plain model (actual - model), by model P(up) of the LEADER:")
    for lo,hi in ((.5,.6),(.6,.7),(.7,.8),(.8,.9),(.9,.97),(.97,1.01)):
        lift(R,lambda r:(1 if r["p"]>=.5 else -1) if lo<=max(r["p"],1-r["p"])<hi else 0,f"  leader model prob {lo:.2f}-{hi:.2f}")
    for k in sorted(set(r["k"] for r in R)):
        lift(R,lambda r:(1 if r["p"]>=.5 else -1) if r["k"]==k and 0.55<=max(r["p"],1-r["p"])<0.9 else 0,f"  leader 55-90%, after minute {k}")
    L=lambda r:(1 if r["p"]>=.5 else -1)
    mid=lambda r:0.55<=max(r["p"],1-r["p"])<0.92
    print("signals (lift toward the stated side, on top of the model):")
    lift(R,lambda r:(1 if r["prev"]>0 else -1) if abs(r["prev"])>1 else 0,"prev window moved >1sd: bet SAME way again")
    lift(R,lambda r:(1 if r["prev"]>0 else -1) if abs(r["prev"])>2 else 0,"prev window moved >2sd: bet SAME way again")
    lift(R,lambda r:(1 if r["prev"]>0 else -1) if abs(r["prev"])>1 and r["k"]==1 else 0,"  same, decided after minute 1 only")
    lift(R,lambda r:(1 if r["prev15"]>0 else -1) if abs(r["prev15"])>1.5 else 0,"prior 15 min moved >1.5sd: SAME way")
    lift(R,lambda r:L(r) if mid(r) and r["volr"]>1.5 else 0,"leader, when last-5-min vol is >1.5x the hour's")
    lift(R,lambda r:L(r) if mid(r) and r["volr"]<0.6 else 0,"leader, when last-5-min vol is <0.6x the hour's")
    lift(R,lambda r:L(r) if mid(r) and r["oneway"] and r["k"]>=2 else 0,"leader, coin never back across the line (k>=2)")
    lift(R,lambda r:L(r) if mid(r) and not r["oneway"] and r["k"]>=2 else 0,"leader, coin HAS crossed the line (k>=2)")
    lift(R,lambda r:L(r) if mid(r) and r["lastz"]*L(r)>1 else 0,"leader, last minute moved >1sd WITH the lead")
    lift(R,lambda r:L(r) if mid(r) and r["lastz"]*L(r)<-1 else 0,"leader, last minute moved >1sd AGAINST the lead")
    lift(R,lambda r:(1 if r["lastz"]>0 else -1) if abs(r["lastz"])>2 else 0,"last minute moved >2sd: bet it continues")
    lift(R,lambda r:(1 if r["ups"]>r["dns"] else -1) if r["k"]>=3 and (r["ups"]==0 or r["dns"]==0) and r["ups"]+r["dns"]>=3 else 0,"every minute so far went one way (k>=3): bet that way")
    lift(R,lambda r:L(r) if mid(r) and r["prev"]*L(r)>1 else 0,"leader, and prev window went the same way >1sd")
    lift(R,lambda r:L(r) if mid(r) and r["prev"]*L(r)<-1 else 0,"leader, and prev window went the opposite way >1sd")
    lift(R,lambda r:-L(r) if max(r["p"],1-r["p"])>=0.9 and r["k"]<=(2 if dur==300 else 5) else 0,"UNDERDOG when leader >=90% in the first part")
    lift(R,lambda r:L(r) if max(r["p"],1-r["p"])>=0.9 and r["left"]<=(1 if dur==300 else 3) else 0,"leader >=90% in the last part")
    # BTC lead: btc z vs own z
    B={(r["st"],r["k"]):r for r in R if r["a"]=="btc"}
    def btclead(r):
        b=B.get((r["st"],r["k"]))
        if r["a"]=="btc" or b is None: return 0
        if abs(b["z"])>1 and abs(r["z"])<0.3: return 1 if b["z"]>0 else -1
        return 0
    lift(R,btclead,"other coin flat (|z|<0.3), Bitcoin |z|>1: bet Bitcoin's way")
    def odd(r):
        zs=[x["z"] for a in C if a!=r["a"] for x in [next((y for y in [None]),None)]]
        return 0
    G=collections.defaultdict(dict)
    for r in R: G[(r["st"],r["k"])][r["a"]]=r
    def oddone(r):
        g=G[(r["st"],r["k"])]; o=[g[a]["z"] for a in g if a!=r["a"]]
        if len(o)<3 or abs(r["z"])>0.3: return 0
        if all(z>0.8 for z in o): return 1
        if all(z<-0.8 for z in o): return -1
        return 0
    lift(R,oddone,"three coins |z|>0.8 one way, this one flat: bet it follows")
    def outlier(r):
        g=G[(r["st"],r["k"])]; o=[g[a]["z"] for a in g if a!=r["a"]]
        if len(o)<3: return 0
        d=r["z"]-statistics.mean(o)
        return (-1 if d>1.5 else 1 if d<-1.5 else 0)
    lift(R,outlier,"coin's z is 1.5 away from the other three: bet it comes BACK")
    lift(R,lambda r:-outlier(r),"coin's z is 1.5 away from the other three: bet it STAYS")
    for h0,h1,name in ((0,7,"00-07 UTC"),(7,13,"07-13 UTC"),(13,17,"13-17 UTC"),(17,21,"17-21 UTC"),(21,24,"21-24 UTC")):
        lift(R,lambda r:L(r) if mid(r) and h0<=r["hour"]<h1 else 0,f"leader 55-92%, hours {name}")
    for a in C: lift(R,lambda r:L(r) if mid(r) and r["a"]==a else 0,f"leader 55-92%, {a}")
    pickle.dump(R,open(f"recs{dur}.pkl","wb"))
