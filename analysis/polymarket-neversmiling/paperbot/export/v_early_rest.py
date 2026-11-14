"""v_early_rest -- standalone copy of the paper bot, for plugging into another bot.

WHAT IT DOES
  Polymarket US 5-minute BTC / ETH Up-Down markets only. Between 90 and 30 seconds before the close it estimates
  the settlement value (the 60-second average BRTI) from the published index plus live Coinbase spot. When that
  estimate is at least 1.5 standard deviations away from the price to beat (z >= 1.5), it backs the likely side:
    * it RESTS a post-only limit one tick inside the touch (buy "Up" at best bid + 1 tick, or sell "Up" at
      best ask - 1 tick, which is the same as buying "Down"), capped at 95 cents;
    * in the last 10 seconds of that band (40-30 s left) it TAKES the ask/bid instead if it is still unfilled,
      but only if (1 - price - taker fee) > 1.5 cents;
    * it cancels the resting order if confidence fades (|z| < 0.5);
    * it holds to settlement, except a protective exit: if it holds a position and z flips below 1.0 against it
      (with at least 20 of the 60 settlement seconds pinned), it closes at the touch.

PAPER RESULTS (run 5, 2026-11-10 .. 11-13, about 226 settled markets)
  $100 account  (orders $0.10-$10):     +49%      $1,000 account (orders $0.10-$30):   +58%
  $30,000 account (orders $0.10-$1,000): +50%     200 of 226 markets won.
  Paper only. Each loss costs up to ~95 cents per contract against ~5-10 cents per win, and the simulator cannot
  model the market impact of large orders. Test it on paper first.

HOW TO PLUG IT IN
  Call   VEarlyRest.on_tick(market, index_feed, spot_feed, position_qty, resting_orders, now)   about once a
  second per open market. It returns a list of actions for your bot to execute:
      ("cancel_all",)                                   cancel this bot's orders in the market
      ("place", side, price, qty)                       post-only limit; side "buy" = buy Up, "sell" = sell Up
      ("take",  side, limit_price, qty)                 marketable order at the touch
  Objects it reads (duck-typed; adapt to your bot):
      market:      .slug .asset ("btc"/"eth") .dur (seconds, 300 for 5m) .end (unix close time)
                   .p2b (price to beat) .best_bid .best_ask .tick (0.01) .state (None or "MARKET_STATE_OPEN")
      index_feed:  .last(asset) -> (unix_ts, value)     latest published 1-s value of the 60-s average index
                   .sigma(asset) -> per-second volatility (fraction) of that series
      spot_feed:   .last(asset) -> (price, unix_ts)     latest Coinbase spot
                   .sec(asset, t) -> spot price at whole second t (or None)
                   .mean(asset, t0, t1) -> mean spot over [t0, t1) (or None)
                   .sigma(asset) -> per-second volatility (fraction) of spot
      position_qty:   contracts of "Up" held (negative = short Up = long Down)
      resting_orders: list of this bot's open orders in the market (anything with a .tag string)
"""
import math, time

# ---- parameters exactly as run on paper ----------------------------------------------------------
T_LO, T_HI = 30, 90          # seconds before close: active band
Z_MIN = 1.5                  # confidence needed to enter
MAX_PRICE = 0.95             # never pay more than this for the likely side
EDGE = 0.015                 # take only if 1 - price - taker fee > 1.5 cents
EXIT_Z = 1.0                 # protective exit threshold
CLIP = 2.0                   # contracts per shot (the paper dollar accounts re-size each order, see size_for)
MAX_POS = 8.0                # contracts per market (scaled x5 / x15 / x500 in the $100 / $1k / $30k accounts)
THETA = 0.095                # Polymarket US taker fee: THETA * qty * p * (1 - p)
ASSETS = ("btc", "eth"); DURS = (300,)


def phi(z): return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def settle_estimate(asset, T, index_feed, spot_feed, now):
    """(mean, sd, n_known) of the settlement value: the simple average of the 60 one-second BRTI prices in [T-60, T)."""
    its, RM = index_feed.last(asset)
    if RM is None: return None
    S = RM
    cb_now, cb_ts = spot_feed.last(asset)
    spot_ok = bool(cb_now and cb_ts and now - cb_ts < 10)
    sig = spot_feed.sigma(asset) if spot_ok else max(index_feed.sigma(asset) * 4, 1.5e-4)
    its = int(its); r = int(round(T - 1 - its))
    if r <= 0: return RM, 0.0, 60
    if r >= 60:
        raw_now = RM
        if spot_ok:
            m60 = spot_feed.mean(asset, its - 59, its + 1)
            if m60: raw_now = RM + (cb_now - m60)
        f = max(0.0, (T - 60) - now)
        return raw_now, math.sqrt((sig * S) ** 2 * (f + 20.0)), 0
    f = max(0.0, (T - 1) - max(now, its))
    mean = RM
    if spot_ok:
        entering_known = 0.0; k_known = 0
        for t in range(its + 1, int(min(now, T - 1)) + 1):
            v = spot_feed.sec(asset, t); entering_known += v if v is not None else cb_now; k_known += 1
        m60 = spot_feed.mean(asset, its - 59, its + 1)
        leaving_sum = 0.0
        for t in range(its - 59, its - 59 + r):
            v = spot_feed.sec(asset, t); leaving_sum += v if v is not None else (m60 if m60 else cb_now)
        entering = entering_known + max(0, r - k_known) * cb_now
        mean = RM + (entering - leaving_sum) / 60.0
    var = (sig * S) ** 2 * (f ** 3) / (3 * 3600.0) if f > 0 else 1e-12
    return mean, math.sqrt(var), int(max(0, min(60, 60 - f)))


def model_z(market, index_feed, spot_feed, now):
    """(z, n_known, index_age) or None. z > 0 favours Up."""
    if market.p2b is None: return None
    est = settle_estimate(market.asset, market.end, index_feed, spot_feed, now)
    if est is None: return None
    mean, sd, n_known = est
    its, RM = index_feed.last(market.asset)
    noise = RM * (2e-5 + 0.8e-4 * (1 - n_known / 60.0))      # spot-vs-BRTI basis and lag noise
    sd = max(sd, 2 * 10 ** -2, noise)
    return (mean - market.p2b) / sd, n_known, now - its


def shots_for(z):
    z = abs(z); return 1 if z < 4 else (2 if z < 6 else 3)


class VEarlyRest:
    def __init__(self, clip=CLIP, max_pos=MAX_POS):
        self.clip = clip; self.max_pos = max_pos; self.next_try = {}

    def on_tick(self, market, index_feed, spot_feed, q, resting, now=None):
        now = now or time.time(); acts = []
        if market.asset not in ASSETS or market.dur not in DURS: return acts
        left = market.end - now
        if left < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return acts
        bb, ba, tick = market.best_bid, market.best_ask, market.tick

        # protective exit (runs every tick while holding)
        m = model_z(market, index_feed, spot_feed, now)
        if abs(q) > 1e-9 and m and m[1] >= 20:
            z = m[0]
            if q > 0 and z < EXIT_Z and bb is not None: return [("cancel_all",), ("take", "sell", bb, q)]
            if q < 0 and z > -EXIT_Z and ba is not None: return [("cancel_all",), ("take", "buy", ba, -q)]

        # entries, at most every 2 seconds, only inside the band
        if left > T_HI or left < T_LO or now < self.next_try.get(market.slug, 0): return acts
        if bb is None or ba is None or not m or m[2] > 8: return acts
        z, n_known, _ = m; self.next_try[market.slug] = now + 2
        n = shots_for(z) * self.clip
        rest = [o for o in resting if str(getattr(o, "tag", "")).startswith("v-rest")]
        late = left <= T_LO + 10; mid = (bb + ba) / 2; sure = n_known >= 50

        if z >= Z_MIN and (mid > 0.5 or sure) and q < self.max_pos:            # back Up
            if late:
                if ba <= MAX_PRICE and 1.0 - ba - THETA * ba * (1 - ba) > EDGE:
                    acts += [("cancel_all",), ("take", "buy", ba, min(n, self.max_pos - q))]
            elif not rest:
                want = min(round(bb + tick, 2), MAX_PRICE, round(ba - tick, 2))
                if want >= 0.5: acts.append(("place", "buy", want, min(n, self.max_pos - q)))
        elif z <= -Z_MIN and (mid < 0.5 or sure) and -q < self.max_pos:         # back Down (sell Up)
            if late:
                if bb >= 1 - MAX_PRICE and bb - THETA * bb * (1 - bb) > EDGE:
                    acts += [("cancel_all",), ("take", "sell", bb, min(n, self.max_pos + q))]
            elif not rest:
                want = max(round(ba - tick, 2), 1 - MAX_PRICE, round(bb + tick, 2))
                if want <= 0.5: acts.append(("place", "sell", want, min(n, self.max_pos + q)))
        elif rest and abs(z) < Z_MIN - 1:
            acts.append(("cancel_all",))
        return acts


def size_for(price, usd_min=0.10, usd_max=1000.0, rng=None):
    """How the paper dollar accounts sized each order: a log-uniform dollar amount, converted to contracts."""
    import random
    rng = rng or random
    d = math.exp(rng.uniform(math.log(usd_min), math.log(usd_max)))
    return round(d / max(price, 0.01), 2)
