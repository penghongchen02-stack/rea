import sys, datetime as dt, collections
import pandas as pd, numpy as np
pd.set_option("display.width", 220); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 300)
import os
S = os.environ.get("POLY_DATA_DIR", "./data")
tr = pd.read_pickle(f"{S}/trades_enriched.pkl"); df = pd.read_pickle(f"{S}/activity.pkl")
lo, hi = dt.date(2026,5,18), dt.date(2026,6,2)
if len(sys.argv) > 1 and sys.argv[1] == "window":
    tr = tr[(tr.date >= lo) & (tr.date <= hi)]; print("### REPORT WINDOW ONLY")
else: print("### FULL HISTORY")
tr = tr.copy()
tr["maker_usdc"] = np.where(~tr.taker, tr.usdc_size, 0.0); tr["taker_usdc"] = np.where(tr.taker, tr.usdc_size, 0.0)
tr["maker_sh"] = np.where(~tr.taker, tr["size"], 0.0); tr["taker_sh"] = np.where(tr.taker, tr["size"], 0.0)

# ---------- fill timing inside the window ----------
print("\n== WHEN IN THE WINDOW DO FILLS HAPPEN (frac = seconds since window start / duration)")
print("fills before window start: %.1f%%   after window end: %.1f%%" % (100*(tr.frac<0).mean(), 100*(tr.frac>1).mean()))
bins = [-9,-0.0001,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,0.95,1.0,9]
tr["fbin"] = pd.cut(tr.frac, bins)
tm = tr.groupby(["fbin"], observed=True).agg(fills=("size","size"), usdc=("usdc_size","sum"), taker_fill_share=("taker","mean"),
      taker_usdc=("taker_usdc","sum"), maker_usdc=("maker_usdc","sum"), mean_price=("price","mean"))
tm["usdc_share"] = (tm.usdc/tm.usdc.sum()).round(3); print(tm.round(3))
pb = pd.cut(tr.price, [0,0.1,0.2,0.4,0.6,0.8,0.9,1.0])
print("\n== PRICE BAND x MAKER/TAKER (usdc)")
print(tr.pivot_table(index=pb, columns="taker", values="usdc_size", aggfunc="sum", observed=True).round(0).rename(columns={False:"maker_usdc",True:"taker_usdc"}))
print("\n== PRICE BAND x TIME-IN-WINDOW (usdc share within band)")
pt = tr.pivot_table(index=pb, columns=pd.cut(tr.frac,[-9,0.25,0.5,0.75,0.9,1.0,9]), values="usdc_size", aggfunc="sum", observed=True)
print((pt.div(pt.sum(axis=1), axis=0)).round(2))

# ---------- per market reconstruction ----------
red = df[df.type=="REDEEM"].groupby("condition_id").agg(redeem_usdc=("usdc_size","sum"), win_idx=("outcome_index","first"), n_win_idx=("outcome_index","nunique"))
mer = df[df.type=="MERGE"].groupby("condition_id").usdc_size.sum().rename("merge_usdc")
sd = tr.groupby(["condition_id","outcome_index"]).agg(usdc=("usdc_size","sum"), notional=("notional","sum"), shares=("size","sum"), fills=("size","size"),
        maker_usdc=("maker_usdc","sum"), maker_sh=("maker_sh","sum"), first_ts=("timestamp","min"), last_ts=("timestamp","max"),
        first_frac=("frac","min"), last_frac=("frac","max")).reset_index()
sd["vwap"] = sd.notional / sd.shares
p = sd.pivot(index="condition_id", columns="outcome_index")
m = pd.DataFrame(index=p.index)
for c in ["usdc","shares","fills","vwap","maker_usdc","maker_sh","first_ts","last_ts","first_frac"]:
    for i in (0,1): m[f"{c}{i}"] = p[(c,i)] if (c,i) in p.columns else np.nan
m = m.fillna({"usdc0":0,"usdc1":0,"shares0":0,"shares1":0,"fills0":0,"fills1":0,"maker_usdc0":0,"maker_usdc1":0,"maker_sh0":0,"maker_sh1":0})
meta = tr.groupby("condition_id").agg(asset=("asset","first"), dur=("dur","first"), wstart=("wstart","first"), slug=("slug","first"), date=("date","first"))
m = m.join(meta).join(red).join(mer); m["merge_usdc"] = m.merge_usdc.fillna(0); m["redeem_usdc"] = m.redeem_usdc.fillna(0)
m["both"] = (m.usdc0>0)&(m.usdc1>0)
m["usdc"] = m.usdc0+m.usdc1
m["dom_idx"] = np.where(m.usdc0>=m.usdc1,0,1)
m["dom_usdc"] = m[["usdc0","usdc1"]].max(axis=1); m["min_usdc"] = m[["usdc0","usdc1"]].min(axis=1)
m["dominance"] = m.dom_usdc / m.min_usdc.replace(0,np.nan)
m["dom_vwap"] = np.where(m.dom_idx==0, m.vwap0, m.vwap1)
m["paired_sh"] = m[["shares0","shares1"]].min(axis=1)
m["paired_cost"] = m.vwap0 + m.vwap1
# winner: redeem tells; else one-sided -> lost; else unknown
m["winner"] = pd.read_pickle(f"{S}/winners.pkl").winner_amt.reindex(m.index)
m["winner_known"] = m.winner.notna()
m["dom_won"] = np.where(m.winner_known, (m.dom_idx == m.winner), np.nan)
m["pnl"] = m.redeem_usdc + m.merge_usdc - m.usdc
m["lag2"] = (m.first_ts1 - m.first_ts0).abs()
print("\n== MARKETS", len(m), " both-sided %.1f%%" % (100*m.both.mean()), " winner known %.1f%%" % (100*m.winner_known.mean()),
      " merge used on %.1f%% of both-sided markets" % (100*((m.merge_usdc>0)&m.both).sum()/m.both.sum()))
print("per-market usdc: median %.0f mean %.0f p90 %.0f max %.0f" % (m.usdc.median(), m.usdc.mean(), m.usdc.quantile(.9), m.usdc.max()))
b = m[m.both]
print("both-sided: median paired cost %.4f  mean %.4f  share<1.00 %.1f%%  share<0.97 %.1f%%  median 2nd-side lag %.0fs" %
      (b.paired_cost.median(), b.paired_cost.mean(), 100*(b.paired_cost<1).mean(), 100*(b.paired_cost<0.97).mean(), b.lag2.median()))
print("paired shares merged: %.1f%% of paired shares (merge usdc / paired shares)" % (100*b.merge_usdc.sum()/b.paired_sh.sum()))
print("pnl total %.0f ; both-sided %.0f ; one-sided %.0f" % (m.pnl.sum(), b.pnl.sum(), m[~m.both].pnl.sum()))
print("\n== PnL by asset/duration (ex rebates)")
print(m.groupby(["asset","dur"]).agg(markets=("pnl","size"), usdc=("usdc","sum"), pnl=("pnl","sum"), both=("both","mean"), dom_won=("dom_won","mean")).round(3))

print("\n== DOMINANCE BUCKETS: is 'dominant side wins' just price calibration?")
bb = b[b.winner_known].copy()
bb["dbin"] = pd.cut(bb.dominance, [1,1.5,2,3,5,10,1e9])
out = bb.groupby("dbin", observed=True).agg(markets=("pnl","size"), dom_win_rate=("dom_won","mean"), dom_vwap=("dom_vwap","mean"),
        paired_cost=("paired_cost","mean"), pnl=("pnl","sum"), usdc=("usdc","sum"))
out["roi"] = out.pnl/out.usdc; print(out.round(3))
print("\n== DOMINANT SIDE VWAP BUCKETS (win rate vs implied probability)")
bb["vbin"] = pd.cut(bb.dom_vwap, [0,0.4,0.5,0.6,0.7,0.8,0.9,1.0])
out = bb.groupby("vbin", observed=True).agg(markets=("pnl","size"), dom_win_rate=("dom_won","mean"), dom_vwap=("dom_vwap","mean"), pnl=("pnl","sum"), usdc=("usdc","sum"))
out["roi"] = out.pnl/out.usdc; print(out.round(3))

print("\n== ORDER OF ENTRY: which side is bought first, and when")
print("first fill frac: median %.2f ; p10 %.2f ; p90 %.2f" % (m[["first_frac0","first_frac1"]].min(axis=1).median(), m[["first_frac0","first_frac1"]].min(axis=1).quantile(.1), m[["first_frac0","first_frac1"]].min(axis=1).quantile(.9)))
print("maker fills share of usdc on dominant side %.1f%% ; on minor side %.1f%%" % (
    100*np.where(b.dom_idx==0,b.maker_usdc0,b.maker_usdc1).sum()/b.dom_usdc.sum(), 100*np.where(b.dom_idx==0,b.maker_usdc1,b.maker_usdc0).sum()/b.min_usdc.sum()))

# ---------- maker quoting structure ----------
mk = tr[~tr.taker]; tk = tr[tr.taker]
print("\n== MAKER FILLS: price distribution (usdc share)"); print((mk.groupby(pd.cut(mk.price,[0,0.1,0.2,0.3,0.4,0.45,0.5,0.55,0.6,0.7,0.8,0.9,1.0]),observed=True).usdc_size.sum()/mk.usdc_size.sum()).round(3))
print("== TAKER FILLS: price distribution (usdc share)"); print((tk.groupby(pd.cut(tk.price,[0,0.1,0.2,0.3,0.4,0.45,0.5,0.55,0.6,0.7,0.8,0.9,1.0]),observed=True).usdc_size.sum()/tk.usdc_size.sum()).round(3))
print("\n== FILL SIZE: most common exact share sizes (maker)"); print(mk["size"].round(2).value_counts().head(12))
print("== FILL SIZE: most common exact share sizes (taker)"); print(tk["size"].round(2).value_counts().head(12))
print("== USDC per fill quantiles maker:", mk.usdc_size.quantile([.1,.5,.9,.99]).round(2).to_dict(), " taker:", tk.usdc_size.quantile([.1,.5,.9,.99]).round(2).to_dict())
print("== taker fills: price decimals>3 share (multi-level sweep) %.1f%%" % (100*(tk.price.map(lambda p: len(repr(p).split('.')[1])>3)).mean()))
print("== maker fills: price decimals>3 share %.1f%%" % (100*(mk.price.map(lambda p: len(repr(p).split('.')[1])>3)).mean()))

# same-second bursts and per-tx
print("\n== BURSTS: fills per (market, second) distribution", tr.groupby(["condition_id","timestamp"]).size().value_counts().head(6).to_dict())
print("== concurrent markets: distinct markets traded per minute, median/p90:", tr.groupby(tr.ts.dt.floor("min")).condition_id.nunique().quantile([.5,.9]).to_dict())
m.to_pickle(f"{S}/markets.pkl")
