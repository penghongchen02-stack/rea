import json, datetime as dt, collections
import pandas as pd, numpy as np
pd.set_option("display.width", 220); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 300)
import os
S = os.environ.get("POLY_DATA_DIR", "./data")
tr = pd.read_pickle(f"{S}/trades_enriched.pkl"); df = pd.read_pickle(f"{S}/activity.pkl"); m = pd.read_pickle(f"{S}/markets.pkl")
lo, hi = dt.date(2026,5,18), dt.date(2026,6,2)
win = m.winner.to_dict()
tr["won"] = tr.outcome_index == tr.condition_id.map(win)
tr["payout"] = np.where(tr.won, tr["size"], 0.0)
tr["pnl_hold"] = tr.payout - tr.usdc_size   # per-fill P/L if held to settlement (ignores merges)
pb = pd.cut(tr.price, [0,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.0])
for label, t in [("LIFETIME", tr), ("WINDOW", tr[(tr.date>=lo)&(tr.date<=hi)])]:
    print(f"\n== PER-FILL WIN RATE BY PRICE BAND x MAKER/TAKER ({label}; P/L assumes hold-to-settlement, fees included in cost)")
    g = t.groupby([pb.loc[t.index], t.taker], observed=True)
    out = pd.DataFrame({"fills": g.size(), "win_rate": g.won.mean().round(3), "mean_price": g.price.mean().round(3), "usdc": g.usdc_size.sum().round(0), "pnl_hold": g.pnl_hold.sum().round(0)})
    out["roi"] = (out.pnl_hold/out.usdc).round(3); out["win_minus_price"] = (out.win_rate - out.mean_price).round(3)
    print(out.unstack("taker").rename(columns={False:"maker",True:"taker"}, level=1).to_string())

print("\n== ALTERNATIVE 'DOMINANCE' DEFINITIONS IN THE WINDOW (trying to reproduce the report's 78.8%)")
mw = m[(m.date>=lo)&(m.date<=hi)&m.both].copy()
for name, a, b in [("usdc", "usdc0", "usdc1"), ("shares", "shares0", "shares1"), ("fills", "fills0", "fills1")]:
    dom = np.where(mw[a]>=mw[b], 0, 1); ratio = mw[[a,b]].max(axis=1)/mw[[a,b]].min(axis=1)
    x = pd.DataFrame({"dbin": pd.cut(ratio,[1,1.5,2,3,1e9]), "won": dom==mw.winner.values})
    print(name, x.groupby("dbin", observed=True).won.agg(["size","mean"]).round(3).to_dict())
# what if "win" = market pnl>0 ?
ratio = mw.dom_usdc/mw.min_usdc; x = pd.DataFrame({"dbin": pd.cut(ratio,[1,1.5,2,3,1e9]), "pos": mw.pnl>0}); print("market pnl>0 by usdc-dominance:", x.groupby("dbin", observed=True).pos.agg(["size","mean"]).round(3).to_dict())
# payout-only (ignoring merges) definition of market win
mw["payout_no_merge"] = np.where(mw.winner==0, mw.shares0, mw.shares1) - mw.usdc
x = pd.DataFrame({"dbin": pd.cut(ratio,[1,1.5,2,3,1e9]), "pos": mw.payout_no_merge>0}); print("hold-to-settlement pnl>0 by usdc-dominance:", x.groupby("dbin", observed=True).pos.agg(["size","mean"]).round(3).to_dict())

print("\n== FIRST FILL LATENCY after window start (seconds), by duration")
ff = tr.groupby("condition_id").agg(toff=("toff","min"), dur=("dur","first"))
print(ff.groupby("dur").toff.quantile([.1,.5,.9]).unstack().round(0))
print("last fill before window end (seconds before end), by duration:")
lf = tr.groupby("condition_id").agg(toff=("toff","max"), dur=("dur","first")); lf["before_end"] = lf.dur - lf.toff
print(lf.groupby("dur").before_end.quantile([.1,.5,.9]).unstack().round(0))

print("\n== TAKER TIER PLAUSIBILITY: 30-day weighted volume estimate = sum(taker notional x (1-price) x 2.3)")
tk = tr[tr.taker].copy(); tk["wv"] = tk.notional * (1 - tk.price) * 2.3
daily_wv = tk.groupby("date").wv.sum(); r30 = daily_wv.rolling(30, min_periods=1).sum()
print("30-day wV on", [(str(d), round(v)) for d, v in r30.loc[[dt.date(2026,6,19), dt.date(2026,6,30), dt.date(2026,7,8), dt.date(2026,8,6)]].items()])
print("tier thresholds: Gold 200k (18%), Platinum 1M (32%), Diamond 4M (44%)")

print("\n== DAILY SERIES AROUND THE TAKER-REBATE LAUNCH (May 28) — window days")
t = tr[(tr.date>=dt.date(2026,5,18))&(tr.date<=dt.date(2026,6,5))]
g = t.groupby("date")
dm = m[(m.date>=dt.date(2026,5,18))&(m.date<=dt.date(2026,6,5))].groupby("date")
d = pd.DataFrame({"fills": g.size(), "markets": g.condition_id.nunique(), "notional": g.notional.sum().round(0), "taker_share": g.taker.mean().round(2), "fees": g.fee.sum().round(0),
                  "assets": g.asset.agg(lambda x: ",".join(sorted(set(x.dropna())))), "durs": g.dur.agg(lambda x: ",".join(str(int(v//60)) for v in sorted(set(x.dropna())))),
                  "mkt_pnl_net": dm.pnl.sum().round(0), "both": dm.both.mean().round(2)})
d["gross_before_fees"] = d.mkt_pnl_net + d.fees
print(d.to_string())

print("\n== ACTIVITY vs TRADES FEED ROW COUNTS")
act_tr = df[df.type=="TRADE"]
per_tx = act_tr.groupby("transaction_hash").size()
print("activity TRADE rows", len(act_tr), "unique tx", per_tx.size, "rows per tx:", per_tx.value_counts().head(6).to_dict())
multi = act_tr[act_tr.transaction_hash.isin(per_tx[per_tx>1].index)]
print("multi-row txs: same token within tx? share of txs with >1 distinct token:", (multi.groupby("transaction_hash").token_id.nunique()>1).mean().round(3))
print("example multi-row tx:"); h = per_tx[per_tx==3].index[0]; print(act_tr[act_tr.transaction_hash==h][["timestamp","outcome","size","price","usdc_size","taker"]] if "taker" in act_tr else act_tr[act_tr.transaction_hash==h][["timestamp","outcome","size","price","usdc_size"]])
ntw = 0
for l in open(f"{S}/trades.jsonl"):
    x = json.loads(l); d_ = dt.datetime.fromtimestamp(x["timestamp"], dt.UTC).date()
    if lo <= d_ <= hi: ntw += 1
print("trades-feed rows in window:", ntw, "(activity rows in window:", len(tr[(tr.date>=lo)&(tr.date<=hi)]), "; report: 173,640)")

print("\n== OPEN POSITIONS / INACTIVITY: last trade", tr.ts.max(), "; last redeem", df[df.type=='REDEEM'].ts.max())
print("active days:", tr.date.nunique(), "; gap days between", tr[tr.date<dt.date(2026,5,1)].date.max(), "and", tr[tr.date>dt.date(2026,5,1)].date.min())
