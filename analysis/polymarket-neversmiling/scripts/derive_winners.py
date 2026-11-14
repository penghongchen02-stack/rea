import os, pandas as pd, numpy as np
S = os.environ.get("POLY_DATA_DIR", "./data")
tr = pd.read_pickle(f"{S}/trades_enriched.pkl"); df = pd.read_pickle(f"{S}/activity.pkl")
sh = tr.groupby(["condition_id", "outcome_index"])["size"].sum().unstack(fill_value=0.0)
red = df[df.type == "REDEEM"].groupby("condition_id").usdc_size.sum().rename("redeem")
mer = df[df.type == "MERGE"].groupby("condition_id").usdc_size.sum().rename("merge")
x = sh.join(red).join(mer).fillna({"redeem": 0.0, "merge": 0.0})
res0 = (x[0] - x["merge"]).clip(lower=0); res1 = (x[1] - x["merge"]).clip(lower=0)
d0 = (x.redeem - res0).abs(); d1 = (x.redeem - res1).abs(); tol = np.maximum(0.02, 0.01 * x.redeem)
w = pd.Series(np.nan, index=x.index)
w[(d0 <= tol) & (d1 > tol)] = 0; w[(d1 <= tol) & (d0 > tol)] = 1
w[(x[1] == 0) & (x[0] > 0) & (x.redeem == 0)] = 1; w[(x[0] == 0) & (x[1] > 0) & (x.redeem == 0)] = 0
print("markets", len(x), "winner determined %.1f%%" % (100 * w.notna().mean()))
pd.DataFrame({"winner_amt": w}).to_pickle(f"{S}/winners.pkl")
