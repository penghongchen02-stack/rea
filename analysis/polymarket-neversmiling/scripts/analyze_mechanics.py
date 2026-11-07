import sys, datetime as dt, collections
import pandas as pd, numpy as np
pd.set_option("display.width", 220); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 300)
import os
S = os.environ.get("POLY_DATA_DIR", "./data")
tr = pd.read_pickle(f"{S}/trades_enriched.pkl"); df = pd.read_pickle(f"{S}/activity.pkl"); m = pd.read_pickle(f"{S}/markets.pkl")
lo, hi = dt.date.fromisoformat(os.environ.get("REPORT_LO", "2026-05-18")), dt.date.fromisoformat(os.environ.get("REPORT_HI", "2026-06-02"))

print("== P/L WATERFALL (lifetime and report window)")
def waterfall(d, t):
    buy_notional = t.notional.sum(); fees = t.fee.sum()
    redeem = d[d.type=="REDEEM"].usdc_size.sum(); merge = d[d.type=="MERGE"].usdc_size.sum(); sell = t[t.side=="SELL"].usdc_size.sum()
    mr = d[d.type=="MAKER_REBATE"].usdc_size.sum(); tk = d[d.type=="TAKER_REBATE"].usdc_size.sum()
    gross = redeem + merge + sell - buy_notional
    return pd.Series({"buy notional (ex fee)": -buy_notional, "settlement redeems": redeem, "complete-set merges": merge, "sells": sell,
        "= gross trading result": gross, "taker fees paid": -fees, "= net trading result": gross-fees, "maker rebates": mr, "taker rebates": tk,
        "= net incl. rebates": gross-fees+mr+tk, "fees as % of gross": 100*fees/gross if gross else np.nan})
print(pd.DataFrame({"lifetime": waterfall(df, tr), "window": waterfall(df[(df.date>=lo)&(df.date<=hi)], tr[(tr.date>=lo)&(tr.date<=hi)])}).round(1))

print("\n== CAPITAL AT WORK: cumulative (buys incl fee - redeems - merges - rebates), i.e. cash drawn from the wallet")
ev = df[df.type.isin(["TRADE","REDEEM","MERGE","MAKER_REBATE","TAKER_REBATE"])].copy()
ev["flow"] = np.where(ev.type=="TRADE", -ev.usdc_size, ev.usdc_size)
ev = ev.sort_values("timestamp"); ev["cum"] = ev.flow.cumsum()
daily = ev.groupby("date").agg(min_cash=("cum","min"), end_cash=("cum","last"))
print("deepest cash drawdown (capital needed) = $%.0f on %s ; final cumulative = $%.0f" % (-daily.min_cash.min(), daily.min_cash.idxmin(), ev.cum.iloc[-1]))
print("typical intraday cash outstanding, median of daily minima: $%.0f" % (-daily.min_cash.median()))

print("\n== ORDER SIZING LOGIC: the recurring exact share sizes vs price")
sz = tr["size"].round(2); top = sz.value_counts().head(10).index.tolist()
for s in top:
    sub = tr[sz==s]; print("size %6.2f  n=%6d  price p10/p50/p90 = %.3f/%.3f/%.3f  usdc p50 = %.2f  taker share %.2f" % (s, len(sub), sub.price.quantile(.1), sub.price.median(), sub.price.quantile(.9), sub.usdc_size.median(), sub.taker.mean()))
print("share of fills with size exactly in the top-10 set: %.1f%%" % (100*sz.isin(top).mean()))
print("usdc_size (incl fee) most common values:", tr.usdc_size.round(1).value_counts().head(8).to_dict())
print("notional (ex fee) most common values:", tr.notional.round(1).value_counts().head(8).to_dict())

print("\n== QUOTE STRUCTURE: maker fills per market, both sides?")
mk = tr[~tr.taker]
mm = mk.groupby(["condition_id","outcome_index"]).agg(n=("size","size"), vwap=("notional","sum"), sh=("size","sum"))
mm["vwap"] = mm.vwap/mm.sh; mp = mm.unstack("outcome_index")
both_maker = mp[("n",0)].notna() & mp[("n",1)].notna()
print("markets with maker fills: %d ; with maker fills on BOTH sides: %.1f%%" % (len(mp), 100*both_maker.mean()))
qs = (mp[("vwap",0)] + mp[("vwap",1)])[both_maker]
print("sum of maker VWAPs (Up+Down) on both-maker markets: median %.3f  p25 %.3f  p75 %.3f  share<1.00 %.1f%%" % (qs.median(), qs.quantile(.25), qs.quantile(.75), 100*(qs<1).mean()))
tk = tr[tr.taker]
tt = tk.groupby(["condition_id","outcome_index"]).agg(vwap=("notional","sum"), sh=("size","sum")); tt["vwap"]=tt.vwap/tt.sh; tp = tt.unstack("outcome_index")
bt = tp[("vwap",0)].notna() & tp[("vwap",1)].notna(); ts_ = (tp[("vwap",0)]+tp[("vwap",1)])[bt]
print("sum of TAKER VWAPs (Up+Down) on both-taker markets: median %.3f  share<1.00 %.1f%%  (n=%d)" % (ts_.median(), 100*(ts_<1).mean(), len(ts_)))

print("\n== HEDGE SEQUENCING on both-sided markets: price of first fill on side A vs first fill on side B")
b = m[m.both].copy()
first = tr.sort_values("timestamp").groupby(["condition_id","outcome_index"]).first()[["price","timestamp"]].unstack("outcome_index")
first.columns = [f"{a}_{b_}" for a,b_ in first.columns]
b = b.join(first)
b["first_side"] = np.where(b["timestamp_0"] <= b["timestamp_1"], 0, 1)
b["p_first"] = np.where(b.first_side==0, b["price_0"], b["price_1"]); b["p_second"] = np.where(b.first_side==0, b["price_1"], b["price_0"])
b["sum_first_fills"] = b.p_first + b.p_second
print("first-fill price: median %.3f ; second-side first-fill price: median %.3f ; sum median %.3f ; share sum<1.00 %.1f%%" % (b.p_first.median(), b.p_second.median(), b.sum_first_fills.median(), 100*(b.sum_first_fills<1).mean()))
print("lag to second side: p25/p50/p75 = %.0f/%.0f/%.0f s ; share <10s %.1f%%" % (b.lag2.quantile(.25), b.lag2.median(), b.lag2.quantile(.75), 100*(b.lag2<10).mean()))
print("dominant side == side bought first: %.1f%%" % (100*(b.first_side==b.dom_idx).mean()))
print("one-sided markets: median usdc %.1f, median fills %.1f, dom_vwap median %.3f, win rate %.3f" % (m[~m.both].usdc.median(), (m[~m.both].fills0+m[~m.both].fills1).median(), m[~m.both].dom_vwap.median(), m[~m.both].dom_won.mean()))

print("\n== WHERE DOES THE GROSS TRADING PROFIT COME FROM (per-market pnl incl fees, split)")
print("both-sided markets: n=%d usdc=%.0f pnl=%.0f (%.2f%%) | one-sided: n=%d usdc=%.0f pnl=%.0f (%.2f%%)" % (
    m.both.sum(), m[m.both].usdc.sum(), m[m.both].pnl.sum(), 100*m[m.both].pnl.sum()/m[m.both].usdc.sum(), (~m.both).sum(), m[~m.both].usdc.sum(), m[~m.both].pnl.sum(), 100*m[~m.both].pnl.sum()/max(m[~m.both].usdc.sum(),1)))
pc = m[m.both].copy(); pc["pcbin"] = pd.cut(pc.paired_cost, [0,0.9,0.95,0.97,1.0,1.03,1.1,5])
print(pc.groupby("pcbin", observed=True).agg(markets=("pnl","size"), usdc=("usdc","sum"), pnl=("pnl","sum"), merged=("merge_usdc","sum"), dom_won=("dom_won","mean")).round(2))

print("\n== TAKER TIER INFERENCE: taker rebate paid on day D vs fees paid on day D-1")
daily_fee = tr.groupby("date").fee.sum(); tkr = df[df.type=="TAKER_REBATE"].groupby("date").usdc_size.sum()
t = pd.DataFrame({"fees": daily_fee, "taker_rebate": tkr}).fillna(0); t["ratio"] = (t.taker_rebate / t.fees.shift(1)).round(3)
t = t[t.taker_rebate>0]; print(t.head(4).round(2)); print("..."); print(t.tail(4).round(2)); print("ratio by month:", t.groupby(pd.to_datetime(t.index).month).ratio.median().round(3).to_dict())
mkr = df[df.type=="MAKER_REBATE"].groupby("date").usdc_size.sum()
print("maker rebate per day: median $%.0f, total $%.0f over %d days; taker rebate total $%.0f" % (mkr.median(), mkr.sum(), len(mkr), tkr.sum()))

print("\n== VALIDATION vs REPORT (window %s..%s, UTC days)" % (lo, hi))
tw = tr[(tr.date>=lo)&(tr.date<=hi)]; mw = m[(m.date>=lo)&(m.date<=hi)]
print("fills %d (report 173,640) ; markets %d (6,360) ; notional ex-fee $%.0f incl-fee $%.0f (1,022,056) ; both-sided %.1f%% (79.2) ; median paired %.4f (0.9836) ; median clip $%.2f (3.51) ; p99 $%.2f (28.82) ; max $%.2f (267.09)" % (
    len(tw), tw.condition_id.nunique(), tw.notional.sum(), tw.usdc_size.sum(), 100*mw.both.mean(), mw[mw.both].paired_cost.median(), tw.usdc_size.median(), tw.usdc_size.quantile(.99), tw.usdc_size.max()))
bw = mw[mw.both & mw.winner_known].copy(); bw["dbin"] = pd.cut(bw.dominance,[1,1.5,2,3,1e9])
print(bw.groupby("dbin", observed=True).agg(markets=("pnl","size"), dom_win_rate=("dom_won","mean"), dom_vwap=("dom_vwap","mean"), paired_cost=("paired_cost","mean")).round(3))
print("window taker fill share %.1f%% ; window maker rebates $%.0f ; taker rebates $%.0f ; fees $%.0f ; merges $%.0f" % (100*tw.taker.mean(),
    df[(df.date>=lo)&(df.date<=hi)&(df.type=="MAKER_REBATE")].usdc_size.sum(), df[(df.date>=lo)&(df.date<=hi)&(df.type=="TAKER_REBATE")].usdc_size.sum(), tw.fee.sum(), df[(df.date>=lo)&(df.date<=hi)&(df.type=="MERGE")].usdc_size.sum()))

print("\n== MONTHLY EVOLUTION")
tr["month"] = tr.ts.dt.strftime("%Y-%m")
g = tr.groupby("month")
mo = pd.DataFrame({"fills": g.size(), "markets": g.condition_id.nunique(), "notional": g.notional.sum().round(0), "taker_share": g.taker.mean().round(3),
    "fees": g.fee.sum().round(0), "median_clip": g.usdc_size.median().round(2), "max_clip": g.usdc_size.max().round(0),
    "assets": g.asset.agg(lambda x: ",".join(sorted(set(x.dropna())))), "durs_min": g.dur.agg(lambda x: ",".join(str(int(v//60)) for v in sorted(set(x.dropna()))))})
mm_ = m.copy(); mm_["month"] = pd.to_datetime(mm_.date).dt.strftime("%Y-%m")
mo["both_sided"] = mm_.groupby("month").both.mean().round(3); mo["dom_win"] = mm_.groupby("month").dom_won.mean().round(3); mo["mkt_pnl_ex_reb"] = mm_.groupby("month").pnl.sum().round(0)
for t_ in ["MAKER_REBATE","TAKER_REBATE","MERGE"]:
    x = df[df.type==t_].copy(); x["month"] = x.ts.dt.strftime("%Y-%m"); mo[t_.lower()] = x.groupby("month").usdc_size.sum().round(0)
print(mo.fillna(0))
print("\n== HOUR OF DAY (UTC) fill share:", (tr.groupby("hour").size()/len(tr)).round(3).to_dict())
