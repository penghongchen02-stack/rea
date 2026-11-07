"""Fair probability of 'Up' from the reference index, the price to beat and the 60-second settlement average."""
import math, time

def phi(z): return 0.5 * (1 + math.erf(z / math.sqrt(2)))

def p_up(market, index_feed, spot_feed, now=None):
    """Return (prob_up, details). Settlement value = mean of index over [end-60, end)."""
    now = now or time.time(); a = market.asset; K = market.p2b
    if K is None: return None, {"reason": "no price to beat yet"}
    its, S = index_feed.last(a)          # IndexFeed.last returns (timestamp, value)
    if S is None: return None, {"reason": "no index"}
    S_idx = S
    cb_now, cb_ts = spot_feed.last(a); cb_then = spot_feed.at(a, its) if cb_now else None
    if cb_now and cb_then and cb_ts and now - cb_ts < 10 and now - its < 30:
        S = S * (cb_now / cb_then)      # the index lags a few seconds; carry it forward with spot's move since then
    sig = index_feed.sigma(a) * S           # per-second price std
    tau = market.end - now                  # seconds to settlement instant
    if tau <= 0: tau = 0.0
    if tau > 60:
        mean = S; var = sig * sig * ((tau - 60) + 60 / 3.0)
    else:
        known_mean, n = index_feed.avg(a, market.end - 60, market.end)
        k = 60 - tau                        # seconds already inside the averaging window
        known_sum = (known_mean or S) * min(n, int(k)) if n else S * int(k)
        future = max(0.0, tau)
        mean = (known_sum + future * S) / 60.0
        var = sig * sig * (future ** 3) / (3 * 3600.0) if future > 0 else 1e-12
    sd = math.sqrt(max(var, 1e-12)); z = (mean - K) / sd
    p = phi(z)
    return p, {"S": round(S, 5), "S_index": S_idx, "K": K, "tau": round(tau, 1), "sd": round(sd, 2), "mean": round(mean, 2), "sigma_1s": round(sig, 3), "index_age": round(now - its, 1)}
