"""Bonereaper lab: 50 variants of the Bonereaper replica (bot 45) trading side by side in their own universe
(port 8084, $1,000 each), to find which of its rules make or lose money.

Every variant keeps the core idea: buy the side of a BTC Up/Down window that the market prices cheaply when the
model says it is worth more than price + fee + an edge, and hold to settlement (with the usual protective exit).
Each variant changes one or a few knobs:

  band      price range of the side bought (original 7-70 cents)
  edge      model value minus price minus fee required (original 3 cents)
  window    when in the window it may trade, by seconds left (original: any time after the first 10 s)
  horizons  which windows: 5-minute, 15-minute, 1-hour (original all three)
  assets    BTC (original), BTC+ETH, ETH
  sizing    clip: 2 contracts, a 10-contract block 1 time in 10 (original)
            usd:  a random dollar amount, log-uniform between lo and hi
            pct:  a fixed share of the starting cash
  cap       most a single window may hold, in dollars (clip sizing: 14 contracts)
  period    seconds between entries in one window (original 6 s); once = one entry per window
  stop      stop trading for good after cash falls this fraction below its peak
"""
import math
from .strategies import LastCallHold
from .fair import p_up

CASH = 1000.0


class BoneVar(LastCallHold):
    def __init__(self, account, name, band=(0.07, 0.70), edge=0.03, window=(10, None), horizons=(300, 900, 3600),
                 assets=("btc",), sizing=("clip",), cap=None, period=6.0, once=False, stop=None, n_min=0, seed=0):
        super().__init__(account, seed=seed)
        self.name = name; self.lo, self.hi = band; self.edge = edge; self.t_min, self.t_max = window
        self.durs = set(horizons); self.assets = set(assets); self.sizing = sizing; self.period = period; self.once = once
        self.stop = stop; self.n_min = n_min; self.peak = None; self.stopped = False
        self.cap = cap if cap is not None else (None if sizing[0] == "clip" else 0.15 * CASH)
        self.max_pos = 14.0

    def contracts(self, price):
        kind = self.sizing[0]
        if kind == "clip": return 10.0 if self.rng.random() < 0.1 else 2.0
        if kind == "usd":
            lo, hi = self.sizing[1], self.sizing[2]
            return math.exp(self.rng.uniform(math.log(lo), math.log(hi))) / price
        return self.sizing[1] * self.acct.start_cash / price      # pct

    def on_tick(self, market, ctx, now):
        if market.asset not in self.assets or market.dur not in self.durs: return
        slug = market.slug; st = self.m(slug); left = market.remaining(now); acct = self.acct
        if left < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return
        self.manage(market, ctx, now)
        if self.stopped or now < st["next_take"] or (self.once and st.get("done")): return
        if self.stop is not None:
            self.peak = max(self.peak or acct.cash, acct.cash)
            if acct.cash < (1 - self.stop) * self.peak: self.stopped = True; return
        if left < self.t_min or (self.t_max is not None and left > self.t_max) or now - market.start < 10: return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None or det.get("n_known", 0) < self.n_min: return
        st["next_take"] = now + self.period; theta = acct.theta; q = self.pos(slug)
        if self.lo <= ba <= self.hi and p - ba - theta * ba * (1 - ba) > self.edge and q >= 0:
            side, px = "buy", ba
        elif self.lo <= 1 - bb <= self.hi and (1 - p) - (1 - bb) - theta * bb * (1 - bb) > self.edge and q <= 0:
            side, px = "sell", 1 - bb
        else: return
        n = self.contracts(px)
        if self.cap is None:
            if abs(q) + n > self.max_pos: return
        else:
            room = self.cap - abs(q) * px
            if room <= 0: return
            n = min(n, room / px)
        n = round(n, 2)
        if n < market.min_qty: return
        o = acct.take(market, side, n, limit=ba if side == "buy" else bb, tag=f"{self.name} {side} {px:.2f}", fixed=True)
        if o and o.filled > 0: st["done"] = True


A, M = (300, 900, 3600), "usd"
# key: (label, kwargs)
VARIANTS = {
    # A. the original and price bands
    "br01": ("Original replica (7-70c, 3c edge, any time, 2/10 clips)", {}),
    "br02": ("Band 30-40c only", {"band": (0.30, 0.40)}),
    "br03": ("Band 20-40c", {"band": (0.20, 0.40)}),
    "br04": ("Band 40-60c (near coin-flip)", {"band": (0.40, 0.60)}),
    "br05": ("Band 10-30c", {"band": (0.10, 0.30)}),
    "br06": ("Band 50-70c", {"band": (0.50, 0.70)}),
    "br07": ("Band 7-20c (longshots only)", {"band": (0.07, 0.20)}),
    "br08": ("Band 55-70c", {"band": (0.55, 0.70)}),
    # B. edge
    "br09": ("Edge 5c", {"edge": 0.05}),
    "br10": ("Edge 7c", {"edge": 0.07}),
    "br11": ("Edge 10c", {"edge": 0.10}),
    "br12": ("30-40c, edge 5c", {"band": (0.30, 0.40), "edge": 0.05}),
    "br13": ("30-40c, edge 7c", {"band": (0.30, 0.40), "edge": 0.07}),
    # C. timing
    "br14": ("Last 2 minutes only", {"window": (10, 120)}),
    "br15": ("Last 60 seconds only", {"window": (5, 60)}),
    "br16": ("Last 30 seconds only", {"window": (3, 30)}),
    "br17": ("Early only (more than 3 minutes left)", {"window": (180, None)}),
    "br18": ("30-40c, last 2 minutes", {"band": (0.30, 0.40), "window": (10, 120)}),
    # D. horizons
    "br19": ("5-minute windows only", {"horizons": (300,)}),
    "br20": ("15-minute windows only", {"horizons": (900,)}),
    "br21": ("1-hour windows only", {"horizons": (3600,)}),
    "br22": ("5m + 15m only", {"horizons": (300, 900)}),
    # E. sizing (original rules otherwise)
    "br23": ("Orders $0.10-$100", {"sizing": (M, 0.1, 100.0)}),
    "br24": ("Orders $0.10-$10", {"sizing": (M, 0.1, 10.0)}),
    "br25": ("Orders $0.10-$30", {"sizing": (M, 0.1, 30.0)}),
    "br26": ("Orders $1-$5", {"sizing": (M, 1.0, 5.0)}),
    "br27": ("Orders $10-$50", {"sizing": (M, 10.0, 50.0)}),
    "br28": ("Orders 1% of account", {"sizing": ("pct", 0.01)}),
    "br29": ("Orders 2% of account", {"sizing": ("pct", 0.02)}),
    "br30": ("Orders 5% of account", {"sizing": ("pct", 0.05)}),
    "br31": ("Orders flat $20", {"sizing": (M, 20.0, 20.0)}),
    # F. $0.10-$100 orders with other rules
    "br32": ("$0.10-$100, 30-40c", {"sizing": (M, 0.1, 100.0), "band": (0.30, 0.40)}),
    "br33": ("$0.10-$100, edge 7c", {"sizing": (M, 0.1, 100.0), "edge": 0.07}),
    "br34": ("$0.10-$100, last 2 minutes", {"sizing": (M, 0.1, 100.0), "window": (10, 120)}),
    "br35": ("$0.10-$100, 5m only", {"sizing": (M, 0.1, 100.0), "horizons": (300,)}),
    "br36": ("$0.10-$100, 30-40c, edge 5c, last 2 min", {"sizing": (M, 0.1, 100.0), "band": (0.30, 0.40), "edge": 0.05, "window": (10, 120)}),
    # G. stop rules
    "br37": ("Stop after 30% drawdown", {"stop": 0.30}),
    "br38": ("Stop after 50% drawdown", {"stop": 0.50}),
    "br39": ("$0.10-$100, stop after 50% drawdown", {"sizing": (M, 0.1, 100.0), "stop": 0.50}),
    "br40": ("1% orders, stop after 30% drawdown", {"sizing": ("pct", 0.01), "stop": 0.30}),
    # H. assets
    "br41": ("BTC + ETH", {"assets": ("btc", "eth")}),
    "br42": ("ETH only", {"assets": ("eth",)}),
    # I. frequency
    "br43": ("One entry per window", {"once": True}),
    "br44": ("One entry every 30 s", {"period": 30.0}),
    # J. combinations in the spirit of Bonereaper safe
    "br45": ("Safe-like: edge 7c, last 2 min, 1% orders", {"edge": 0.07, "window": (10, 120), "sizing": ("pct", 0.01), "cap": 20.0}),
    "br46": ("Edge 5c, last 2 min, 2% orders", {"edge": 0.05, "window": (10, 120), "sizing": ("pct", 0.02), "cap": 40.0}),
    "br47": ("30-40c, edge 5c, 2% orders", {"band": (0.30, 0.40), "edge": 0.05, "sizing": ("pct", 0.02), "cap": 40.0}),
    "br48": ("20-50c, edge 5c, last 60 s, 1% orders", {"band": (0.20, 0.50), "edge": 0.05, "window": (5, 60), "sizing": ("pct", 0.01), "cap": 20.0}),
    "br49": ("Model-confirmed (20+ settlement s known), 2% orders", {"n_min": 20, "sizing": ("pct", 0.02), "cap": 40.0}),
    "br50": ("40-60c, edge 5c, $0.10-$100", {"band": (0.40, 0.60), "edge": 0.05, "sizing": (M, 0.1, 100.0)}),
}

LAB_LABELS = {k: v[0] for k, v in VARIANTS.items()}


def build_lab_registry():
    return {k: (lambda acct, k=k, kw=kw, i=i: BoneVar(acct, k, seed=100 + i, **kw)) for i, (k, (_, kw)) in enumerate(VARIANTS.items())}
