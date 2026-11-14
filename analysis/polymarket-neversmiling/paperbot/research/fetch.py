import json,time,urllib.request,os,ssl,pickle,datetime
ctx=ssl.create_default_context(cafile="/root/.ccr/ca-bundle.crt")
end=int(time.time())//60*60; days=21; out={}
iso=lambda t: datetime.datetime.utcfromtimestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ")
for prod in ("BTC-USD","ETH-USD","SOL-USD","XRP-USD"):
    rows={}; t1=end
    while t1>end-days*86400:
        t0=t1-300*60
        url=f"https://api.exchange.coinbase.com/products/{prod}/candles?granularity=60&start={iso(t0)}&end={iso(t1)}"
        for k in range(4):
            try:
                r=urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"research-script"}),timeout=20,context=ctx); d=json.loads(r.read()); break
            except Exception as e: d=None; time.sleep(1.5*(k+1))
        if d is None: print("fail",prod,t0); 
        else:
            for c in d: rows[c[0]]=(c[3],c[2],c[1],c[4],c[5])  # open, high, low, close, vol
        t1=t0; time.sleep(0.12)
    out[prod[:3].lower()]=rows; print(prod,len(rows),flush=True)
pickle.dump(out,open("candles.pkl","wb"))
