import sys, json, re, math, collections, datetime as dt
from zoneinfo import ZoneInfo
import pandas as pd, numpy as np
pd.set_option("display.width", 200); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 200)
import os
S = os.environ.get("POLY_DATA_DIR", "./data")
src = sys.argv[1] if len(sys.argv) > 1 else f"{S}/activity.jsonl"
rows = [json.loads(l) for l in open(src)]
df = pd.DataFrame(rows)
df["ts"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
df["date"] = df["ts"].dt.date
print("rows", len(df), "span", df.ts.min(), "->", df.ts.max())
print(df["type"].value_counts().to_dict())

# ---------- window parsing ----------
ET = ZoneInfo("America/New_York")
MON = {m.lower(): i for i, m in enumerate(["January","February","March","April","May","June","July","August","September","October","November","December"], 1)}
def parse_slug(s):
    m = re.match(r"^(btc|eth|sol|xrp|doge|bnb)-updown-(\d+)m-(\d+)$", s)
    if m: return m.group(1).upper(), int(m.group(2)) * 60, int(m.group(3))
    m = re.match(r"^(bitcoin|ethereum|solana|xrp|dogecoin|bnb)-up-or-down-([a-z]+)-(\d+)-(\d{4})-(\d+)(am|pm)-et$", s)
    if m:
        asset = {"bitcoin":"BTC","ethereum":"ETH","solana":"SOL","xrp":"XRP","dogecoin":"DOGE","bnb":"BNB"}[m.group(1)]
        h = int(m.group(5)) % 12 + (12 if m.group(6) == "pm" else 0)
        t = dt.datetime(int(m.group(4)), MON[m.group(2)], int(m.group(3)), h, tzinfo=ET)
        return asset, 3600, int(t.timestamp())
    return None, None, None
slugs = {s: parse_slug(s) for s in df.loc[df.slug != "", "slug"].unique()}
unparsed = [s for s, v in slugs.items() if v[0] is None]
print("unparsed slugs", len(unparsed), unparsed[:10])
df["asset"] = df.slug.map(lambda s: slugs.get(s, (None,None,None))[0])
df["dur"] = df.slug.map(lambda s: slugs.get(s, (None,None,None))[1])
df["wstart"] = df.slug.map(lambda s: slugs.get(s, (None,None,None))[2])

tr = df[df.type == "TRADE"].copy()
tr["notional"] = tr.size_ if "size_" in tr else tr["size"] * tr["price"]
tr["fee"] = np.where(tr.side == "BUY", tr.usdc_size - tr.notional, tr.notional - tr.usdc_size)
tr["fee_expected"] = tr["size"] * 0.07 * tr.price * (1 - tr.price)
tr["taker"] = tr.fee > 1e-5
tr["unclassifiable"] = (tr.fee_expected < 2e-5) & (~tr.taker)
tr["taker_fee"] = tr.taker
tfile = f"{S}/trades.jsonl"   # the user trades feed lists taker fills only; use it when present (covers fee-free April)
if os.path.exists(tfile):
    tx = set(json.loads(l)["transaction_hash"] for l in open(tfile))
    tr["taker"] = tr.transaction_hash.isin(tx)
    era = tr.ts >= pd.Timestamp("2026-05-01", tz="UTC")   # taker fees were not charged on these markets in April 2026
    print("taker flag from trades feed; agreement with fee-based flag from May 2026 on: %.4f" % tr.loc[era, "taker"].eq(tr.loc[era, "taker_fee"]).mean())
tr["toff"] = tr.timestamp - tr.wstart
tr["frac"] = tr.toff / tr.dur
tr["hour"] = tr.ts.dt.hour
print("\n== SIDE", tr.side.value_counts().to_dict())
print("taker share of fills %.1f%%  of notional %.1f%%  unclassifiable %.1f%%" % (100*tr.taker.mean(), 100*tr.loc[tr.taker,"notional"].sum()/tr.notional.sum(), 100*tr.unclassifiable.mean()))
print("fee match check (taker): median |fee-expected|/expected =", ((tr.loc[tr.taker,"fee"]-tr.loc[tr.taker,"fee_expected"]).abs()/tr.loc[tr.taker,"fee_expected"]).median())

# ---------- lifetime cash flow ----------
def cash(d):
    b = d[(d.type=="TRADE")&(d.side=="BUY")].usdc_size.sum(); s = d[(d.type=="TRADE")&(d.side=="SELL")].usdc_size.sum()
    out = dict(buy_usdc=b, sell_usdc=s, redeem=d[d.type=="REDEEM"].usdc_size.sum(), merge=d[d.type=="MERGE"].usdc_size.sum(),
               maker_rebate=d[d.type=="MAKER_REBATE"].usdc_size.sum(), taker_rebate=d[d.type=="TAKER_REBATE"].usdc_size.sum(),
               split=d[d.type=="SPLIT"].usdc_size.sum() if "SPLIT" in set(d.type) else 0.0)
    out["fees_paid"] = tr.loc[tr.index.isin(d.index), "fee"].sum()
    out["pnl_ex_rebates"] = out["redeem"] + out["merge"] + s - b - out["split"]
    out["pnl_incl_rebates"] = out["pnl_ex_rebates"] + out["maker_rebate"] + out["taker_rebate"]
    return out
print("\n== LIFETIME CASH FLOW"); print(pd.Series(cash(df)).round(2))
LO, HI = dt.date.fromisoformat(os.environ.get("REPORT_LO", "2026-05-18")), dt.date.fromisoformat(os.environ.get("REPORT_HI", "2026-06-02"))
w = df[(df.date >= LO) & (df.date <= HI)]
print(f"\n== REPORT WINDOW {LO}..{HI} (UTC days) cash flow"); print(pd.Series(cash(w)).round(2))
tw = tr[(tr.date >= LO) & (tr.date <= HI)]
print("window trades", len(tw), "markets", tw.condition_id.nunique(), "buy notional ex-fee %.0f incl-fee %.0f" % (tw.notional.sum(), tw.usdc_size.sum()),
      "median clip %.2f p99 %.2f max %.2f" % (tw.usdc_size.median(), tw.usdc_size.quantile(.99), tw.usdc_size.max()))

# ---------- regime over time (weekly) ----------
tr["week"] = tr.ts.dt.tz_convert(None).dt.to_period("W").astype(str)
g = tr.groupby("week")
wk = pd.DataFrame({"fills": g.size(), "markets": g.condition_id.nunique(), "notional": g.notional.sum().round(0),
                   "taker_fill_share": g.taker.mean().round(3), "fees": g.fee.sum().round(0),
                   "median_clip": g.usdc_size.median().round(2), "mean_clip": g.usdc_size.mean().round(2),
                   "assets": g.asset.agg(lambda x: ",".join(sorted(set(x.dropna())))),
                   "durs": g.dur.agg(lambda x: ",".join(str(int(v//60)) for v in sorted(set(x.dropna()))))})
reb = df[df.type.isin(["MAKER_REBATE","TAKER_REBATE","MERGE","REDEEM"])].copy(); reb["week"] = reb.ts.dt.tz_convert(None).dt.to_period("W").astype(str)
for t in ["MAKER_REBATE","TAKER_REBATE","MERGE","REDEEM"]:
    wk["sum_"+t.lower()] = reb[reb.type==t].groupby("week").usdc_size.sum().round(0)
wk = wk.fillna(0)
wk["pnl_ex_reb"] = (wk.sum_redeem + wk.sum_merge - g.usdc_size.sum()).round(0)
print("\n== WEEKLY REGIME"); print(wk)

# ---------- daily taker tier inference ----------
daily_fee = tr.groupby("date").fee.sum()
tk = df[df.type=="TAKER_REBATE"].groupby("date").usdc_size.sum()
mk = df[df.type=="MAKER_REBATE"].groupby("date").usdc_size.sum()
tier = pd.DataFrame({"fees_paid": daily_fee, "taker_rebate_paid_that_day": tk, "maker_rebate_paid_that_day": mk}).fillna(0)
tier["rebate_vs_prev_day_fees"] = (tier.taker_rebate_paid_that_day / tier.fees_paid.shift(1)).round(3)
print("\n== DAILY FEES vs REBATES (rebates are paid next day)"); print(tier.round(2).to_string())

tr.to_pickle(f"{S}/trades_enriched.pkl"); df.to_pickle(f"{S}/activity.pkl")
