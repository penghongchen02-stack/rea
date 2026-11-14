import sqlite3,glob,calendar,re,collections,math,pickle,time
dbs=[p for p in glob.glob("paperbot-data/*/paper.db")+["paperbot-data/paper.db"]]
W={}  # slug -> (asset,horizon,up,p2b,settle)
for p in dbs:
    c=sqlite3.connect(p)
    for slug,a,h,up,p2b,sp in c.execute("select slug,asset,horizon,up,p2b,settle_px from settlements where up is not null"):
        W.setdefault(slug,(a,h,up,p2b,sp))
rx=re.compile(r"updown-(5m|15m|1h)-(\d{4})-(\d\d)-(\d\d)-(\d\d)(\d\d)z")
def start(slug):
    m=rx.search(slug)
    if not m: return None
    return calendar.timegm((int(m[2]),int(m[3]),int(m[4]),int(m[5]),int(m[6]),0)), {"5m":300,"15m":900,"1h":3600}[m[1]]
S={}
for s in W:
    x=start(s)
    if x: S[s]=x
print("windows",len(W),"updown parsed",len(S), collections.Counter(W[s][1] for s in S))
# calibration samples from taker fills
samp={}  # (slug, 5s bucket, side) -> price
for p in dbs:
    c=sqlite3.connect(p)
    for ts,slug,side,price,liq in c.execute("select ts,slug,side,price,liq from fills where liq='taker'"):
        if slug not in S: continue
        samp[(slug,int(ts//5),side)]=(ts,price)
print("samples",len(samp))
pickle.dump((W,S,samp),open("base.pkl","wb"))
def fee(p): return 0.095*p*(1-p)
# EV table for 5m: buying the side at cost c (ask for up; 1-bid for down) by time-left bucket
tb=[(300,240),(240,180),(180,120),(120,60),(60,30),(30,0)]
pb=[(0,.1),(.1,.2),(.2,.3),(.3,.4),(.4,.5),(.5,.6),(.6,.7),(.7,.8),(.8,.9),(.9,1.01)]
for hz,dur in (("5m",300),("15m",900)):
    agg=collections.defaultdict(lambda:[0,0.0,0.0,set()])
    for (slug,b,side),(ts,price) in samp.items():
        a,h,up,_,_=W[slug]; st,d=S[slug]
        if h!=hz: continue
        left=st+d-ts
        if left<0 or left>d: continue
        fr=left/d*300
        cost=price if side=="buy" else 1-price; win=(up==1) if side=="buy" else (up==0)
        ti=next((i for i,(hi,lo) in enumerate(tb) if lo<=fr<=hi),None); pi=next((i for i,(lo,hi) in enumerate(pb) if lo<=cost<hi),None)
        if ti is None or pi is None: continue
        g=agg[(ti,pi)]; g[0]+=1; g[1]+=win; g[2]+=cost; g[3].add(slug)
    print("\n",hz,"EV per contract of TAKING a side at cost c (win rate - cost - fee), cents; [samples/windows]")
    print("left\\cost  "+"  ".join(f"{lo:.1f}-{min(hi,1):.1f}" for lo,hi in pb))
    for ti,(hi,lo) in enumerate(tb):
        row=[]
        for pi in range(len(pb)):
            g=agg.get((ti,pi))
            if not g or len(g[3])<40: row.append("    .   "); continue
            wr=g[1]/g[0]; c=g[2]/g[0]; ev=wr-c-fee(c); se=math.sqrt(wr*(1-wr)/len(g[3]))
            row.append(f"{ev*100:+5.1f}/{se*100:.0f}")
        print(f"{hi:3d}-{lo:3d}   "+" ".join(f"{x:>8}" for x in row))
