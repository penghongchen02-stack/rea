import pickle,collections,statistics,math
R=""
T=pickle.load(open(R+"trades.pkl","rb"))
U=[t for t in T if t["kind"]=="updown" and t["stake"]>0]
print("all trades",len(T),"net",round(sum(t["net"] for t in T)),"| updown",len(U),"net",round(sum(t["net"] for t in U)),"| ladder net",round(sum(t["net"] for t in T if t["kind"]!="updown")))
L=[t for t in U if t["net"]<0]; Wn=[t for t in U if t["net"]>0]
print("losing trades",len(L),"sum",round(sum(t["net"] for t in L)),"| winning",len(Wn),"sum",round(sum(t["net"] for t in Wn)),"| fees paid",round(sum(t["fees"] for t in U)),"| stake",round(sum(t["stake"] for t in U)))
print("\n== A. HOW each losing trade lost (share of the dollars lost)")
cat=collections.Counter(); catn=collections.Counter()
for t in L:
    if t["gross"]>=0: k="fee only: right or flat before the fee"
    elif abs(t["fq"])>1e-6 and t["settle"]<0 and t["realized"]>=-0.25*abs(t["gross"]): k="held the wrong side to the close"
    elif abs(t["fq"])<=1e-6: k="sold out before the close at a loss"
    elif t["settle"]>=0: k="right side at the close, but sold part lower / paid more than it won"
    else: k="both: sold some at a loss and held the rest wrong"
    cat[k]+=t["net"]; catn[k]+=1
tl=sum(cat.values())
for k,v in sorted(cat.items(),key=lambda x:x[1]): print(f"  {k:70s} {catn[k]:6d} trades  ${v:10.0f}  {v/tl*100:5.1f}%")
def table(title,keyf,order=None,data=U,minn=150):
    g=collections.defaultdict(list)
    for t in data:
        k=keyf(t)
        if k is not None: g[k].append(t)
    print(f"\n== {title}")
    print(f"  {'group':38s} {'trades':>7s} {'lose%':>6s} {'net $':>9s} {'stake $':>10s} {'net/stake':>9s} {'fees $':>8s} {'win% need':>9s} {'win% got':>8s}")
    for k in (order or sorted(g,key=lambda k:sum(t['net'] for t in g[k]))):
        v=g.get(k)
        if not v or len(v)<minn: continue
        st=sum(t["stake"] for t in v); net=sum(t["net"] for t in v)
        single=[t for t in v if "won" in t]
        need=sum((t["avgc"]+0.095*t["avgc"]*(1-t["avgc"])*(1-t["maker_share"]))*t["stake"] for t in single)/max(1e-9,sum(t["stake"] for t in single))
        got=sum(t["won"]*t["stake"] for t in single)/max(1e-9,sum(t["stake"] for t in single))
        print(f"  {str(k):38s} {len(v):7d} {sum(1 for t in v if t['net']<0)/len(v)*100:6.1f} {net:9.0f} {st:10.0f} {net/st*100:8.1f}% {sum(t['fees'] for t in v):8.0f} {need*100:8.1f}% {got*100:7.1f}%")
def b(x,edges,labels):
    for e,l in zip(edges,labels):
        if x<e: return l
    return labels[-1]
table("B. by PRICE PAID for the first fill",lambda t:b(t["c0"],[.1,.2,.35,.5,.65,.8,.9],["1 under 10c","2 10-20c","3 20-35c","4 35-50c","5 50-65c","6 65-80c","7 80-90c","8 90c+"]),order=["1 under 10c","2 10-20c","3 20-35c","4 35-50c","5 50-65c","6 65-80c","7 80-90c","8 90c+"])
table("C. by WHEN in the window the first fill came",lambda t:b(t["frac"],[.1,.25,.5,.75,.9],["1 first 10%","2 10-25%","3 25-50%","4 50-75%","5 75-90%","6 last 10%"]),order=["1 first 10%","2 10-25%","3 25-50%","4 50-75%","5 75-90%","6 last 10%"])
table("D. by HOW it got in (first fill)",lambda t:"resting order (maker)" if t["liq0"]=="maker" else "took the offer (taker)")
table("E. by NUMBER of entry fills in the window",lambda t:b(t["nent"],[2,4,10,30],["1 one fill","2 2-3","3 4-9","4 10-29","5 30+"]),order=["1 one fill","2 2-3","3 4-9","4 10-29","5 30+"])
table("F. ADDING: later entry fills 3c+ cheaper than the first (averaging down) or dearer (chasing)",lambda t:"averaged down" if t["worse"] and not t["better"] else "chased up" if t["better"] and not t["worse"] else "both" if t["worse"] and t["better"] else "no adds beyond 3c" if t["nent"]>1 else "single fill")
table("G. by DOLLARS put into the window",lambda t:b(t["stake"],[5,20,100,500,2000],["1 under $5","2 $5-20","3 $20-100","4 $100-500","5 $500-2000","6 $2000+"]),order=["1 under $5","2 $5-20","3 $20-100","4 $100-500","5 $500-2000","6 $2000+"],minn=30)
table("H. bought the side that was AHEAD or BEHIND at that moment",lambda t:None if "lead" not in t else b(t["lead"],[-1,-0.3,0.3,1],["1 far behind (>1sd)","2 behind","3 level","4 ahead","5 far ahead (>1sd)"]),order=["1 far behind (>1sd)","2 behind","3 level","4 ahead","5 far ahead (>1sd)"])
table("I. side versus the coin's move in the 15 minutes BEFORE the window",lambda t:None if "prev15" not in t else b(t["prev15"],[-1.5,-0.5,0.5,1.5],["1 against a big move","2 against","3 flat","4 with","5 with a big move"]),order=["1 against a big move","2 against","3 flat","4 with","5 with a big move"])
table("J. side versus the coin's LAST MINUTE before the fill",lambda t:None if "lastz" not in t else b(t["lastz"],[-2,-0.7,0.7,2],["1 against a spike","2 against","3 flat","4 with","5 with a spike (chasing)"]),order=["1 against a spike","2 against","3 flat","4 with","5 with a spike (chasing)"])
table("K. what the coin did AFTER the fill (in its own usual-move units)",lambda t:None if "move_after" not in t else b(t["move_after"],[-1,-0.3,0.3,1],["1 went hard against","2 against","3 flat","4 for","5 hard for"]),order=["1 went hard against","2 against","3 flat","4 for","5 hard for"])
table("L. window length",lambda t:t["hz"]); table("M. coin",lambda t:t["asset"]); table("N. side",lambda t:"Up" if t["side"]=="buy" else "Down")
table("O. exits: got out before the close?",lambda t:"held everything to the close" if t["nopp"]==0 else "sold all before the close" if abs(t["fq"])<1e-6 else "sold part")
table("P. dashboard",lambda t:t["dash"],minn=1)
# concentration
Ls=sorted(L,key=lambda t:t["net"]); tot=sum(t["net"] for t in L)
for q in (0.001,0.01,0.05,0.10): 
    n=max(1,int(len(Ls)*q)); print(f"\nworst {q*100:.1f}% of losing trades ({n}) = {sum(t['net'] for t in Ls[:n])/tot*100:.1f}% of all dollars lost" ,end="")
print()
bw=collections.defaultdict(float); 
for t in U: bw[t["slug"]]+=t["net"]
ws=sorted(bw.values()); lw=[x for x in ws if x<0]; print(f"windows: {len(bw)}; losing windows {len(lw)}; worst 1% of windows = {sum(ws[:max(1,len(ws)//100)])/sum(lw)*100:.1f}% of window losses; worst 5% = {sum(ws[:len(ws)//20])/sum(lw)*100:.1f}%")
# herd: per (dash,slug): share of stake on the losing side
h=collections.defaultdict(lambda:[0.0,0.0,0.0,0])
for t in U:
    if "won" not in t: continue
    k=(t["dash"],t["slug"]); h[k][0]+=t["stake"]; h[k][1]+=t["stake"]*(not t["won"]); h[k][2]+=t["net"]; h[k][3]+=1
buck=collections.defaultdict(lambda:[0,0.0,0.0])
for k,(st,ls,net,n) in h.items():
    if n<5: continue
    s=ls/st; kk="5+ bots, 90%+ of money on the losing side" if s>=.9 else "5+ bots, 60-90% on the losing side" if s>=.6 else "5+ bots, split 40-60" if s>=.4 else "5+ bots, most money on the winning side"
    buck[kk][0]+=1; buck[kk][1]+=net; buck[kk][2]+=st
print("\n== Q. HERDING inside a dashboard (windows with 5+ of its bots in)")
for k,v in sorted(buck.items(),key=lambda x:x[1][1]): print(f"  {k:48s} windows {v[0]:5d} net ${v[1]:10.0f} stake ${v[2]:10.0f}")
bb=collections.defaultdict(lambda:[0,0.0,0.0])
for t in U: x=bb[(t["dash"],t["bot"])]; x[0]+=1; x[1]+=t["net"]; x[2]+=t["stake"]
print("\n== R. the ten bots that lost the most"); 
for k,v in sorted(bb.items(),key=lambda x:x[1][1])[:10]: print(f"  {k[0]:11s} {k[1]:34s} trades {v[0]:5d} net ${v[1]:9.0f} stake ${v[2]:10.0f} ({v[1]/v[2]*100:+.1f}%)")
print("bots total",len(bb),"losing bots",sum(1 for v in bb.values() if v[1]<0))
