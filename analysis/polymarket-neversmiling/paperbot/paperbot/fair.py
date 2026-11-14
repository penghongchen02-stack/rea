"""Fair probability of 'Up' (or 'Above') from the settlement arithmetic of Polymarket US crypto markets.

Settlement rule (docs.polymarket.us, Crypto FAQs): Up/Down, Above/Below and Price Range markets settle on the simple
average of the 60 one-second BRTI prices in [T - 60 s, T). The gateway's 1-second "asset price" series is that
rolling 60-second average itself (its value at T - 1 equals the published settlement price in every window
checked), so:

    V = RM(T - 1)   where RM(t) = mean of the raw index over (t - 60, t]

and from the latest published RM at time `its`:

    RM(T - 1) = RM(its) + ( sum of raw over (its, T - 1]  -  sum of raw over (its - 60, its - 60 + r] ) / 60,
    r = (T - 1) - its  (seconds of the series still unpublished).

The raw index is proxied by Coinbase spot: its basis to BRTI is a few dollars and nearly constant over a minute, so it
cancels between the entering and leaving seconds. Spot seconds already elapsed are known; only the seconds still
to come are random (a random walk with per-second volatility measured from spot), which gives
    var = sigma^2 S^2 * f^3 / (3 * 3600),  f = seconds still to come.
More than a minute out, V is the average of a window entirely in the future: mean = current raw level, variance of a
random walk to the window plus the averaging term.
"""
import math, time, threading

def phi(z): return 0.5 * (1 + math.erf(z / math.sqrt(2)))

_CACHE = {}
_TL = threading.local()      # .res = model refreshes per second for this thread (1 unless a fast loop raises it)

def set_resolution(per_second): _TL.res = max(1, int(per_second))

def p_up(market, index_feed, spot_feed, now=None):
    """Return (prob_up, details), cached per (market, second): 60+ bots in 3 universes call this for the same
    market in the same loop pass."""
    now = now or time.time(); res = getattr(_TL, "res", 1); key = (market.slug, int(now * res), res)
    hit = _CACHE.get(key)
    if hit is not None: return hit
    if len(_CACHE) > 4000: _CACHE.clear()
    res = _p_up(market, index_feed, spot_feed, now); _CACHE[key] = res; return res


def settle_estimate(asset, T, index_feed, spot_feed, now, decimals=2):
    """Estimate of the settlement value at instant T: (mean, sd, n_known, det) or None.
    n_known = seconds of the 60 whose contribution is pinned (published or already observed in spot)."""
    its, RM = index_feed.last(asset)
    if RM is None: return None
    S = RM
    cb_now, cb_ts = spot_feed.last(asset)
    spot_ok = bool(cb_now and cb_ts and now - cb_ts < 10)
    sig = spot_feed.sigma(asset) if spot_ok else max(index_feed.sigma(asset) * 4, 1.5e-4)   # raw per-second volatility
    its = int(its); r = int(round(T - 1 - its))          # series seconds still unpublished up to the settlement second
    if r <= 0:
        return RM, 0.0, 60, {"mode": "published"}
    if r >= 60:
        # the whole settlement window lies ahead: V ~ raw level now, carried as a random walk
        if spot_ok:
            m60 = spot_feed.mean(asset, its - 59, its + 1)
            raw_now = RM + (cb_now - m60) if m60 else RM
        else: raw_now = RM
        f = max(0.0, (T - 60) - now)
        var = (sig * S) ** 2 * (f + 60 / 3.0)
        return raw_now, math.sqrt(var), 0, {"mode": "ahead", "f": round(f, 1), "sigma_raw": round(sig, 6)}
    # inside the final minute of publication: pin what spot already shows, leave only the seconds still to come random
    entering_known = 0.0; k_known = 0
    f = max(0.0, (T - 1) - max(now, its))               # seconds still to come
    if spot_ok:
        for t in range(its + 1, int(min(now, T - 1)) + 1):
            v = spot_feed.sec(asset, t)
            if v is None: v = cb_now
            entering_known += v; k_known += 1
        leaving = [spot_feed.sec(asset, t) for t in range(its - 59, its - 59 + r)]
        leaving_sum = sum(v if v is not None else (m if (m := spot_feed.mean(asset, its - 59, its + 1)) else cb_now) for v in leaving)
        entering = entering_known + max(0, r - k_known) * cb_now
        mean = RM + (entering - leaving_sum) / 60.0
    else:
        mean = RM
    var = (sig * S) ** 2 * (f ** 3) / (3 * 3600.0) if f > 0 else 1e-12
    n_known = int(max(0, min(60, 60 - f)))
    return mean, math.sqrt(var), n_known, {"mode": "final", "r": r, "f": round(f, 1), "sigma_raw": round(sig, 6)}


def _p_up(market, index_feed, spot_feed, now):
    a = market.asset; K = market.p2b if market.kind == "updown" else market.strike
    if K is None: return None, {"reason": "no price to beat yet"}
    est = settle_estimate(a, market.end, index_feed, spot_feed, now, getattr(market, "decimals", 2))
    if est is None: return None, {"reason": "no index"}
    mean, sd, n_known, info = est
    its, RM = index_feed.last(a); S = RM
    tau = max(0.0, market.end - now)
    # the published index is quantized (XRP to 1e-5, SOL to 1e-4): never claim more precision than two index ticks
    quant = 2 * 10 ** (-getattr(market, "decimals", 2))
    # basis and lag noise: the spot proxy for the raw index is not exact. 1 bp with nothing pinned, 0.2 bp when all is.
    noise = S * (2e-5 + 0.8e-4 * (1 - n_known / 60.0))
    sd = max(sd, quant, noise); z = (mean - K) / sd
    p = phi(z)
    return p, {"S": round(mean, 5), "S_index": RM, "K": K, "tau": round(tau, 1), "sd": round(sd, 5), "mean": round(mean, 5),
               "sigma_1s": round(info.get("sigma_raw", 0) or 0, 6), "index_age": round(now - its, 1), "n_known": n_known, "z": round(z, 2), "mode": info.get("mode")}
