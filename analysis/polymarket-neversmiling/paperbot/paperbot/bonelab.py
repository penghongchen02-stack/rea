"""Bonereaper lab: 140 variants of the Bonereaper replica (bot 45) trading side by side in their own universe
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
  shrink    weight on the model when estimating the true price, the rest on the market mid (1.0 = model only)
  max_pos   contracts a window may hold with clip sizing (original 14)
  tp / sl   take-profit / stop-loss on an open position, checked every tick: in cents of the side's price
            (tp_c, sl_c) or as a multiple of the entry price (tp_x: sell at entry x (1 + tp_x); sl_x: sell at
            entry x (1 - sl_x)); after an exit the variant does not re-enter that window
"""
import math
from .strategies import LastCallHold
from .fair import p_up

CASH = 1000.0


class BoneVar(LastCallHold):
    def __init__(self, account, name, band=(0.07, 0.70), edge=0.03, window=(10, None), horizons=(300, 900, 3600),
                 assets=("btc",), sizing=("clip",), cap=None, period=6.0, once=False, stop=None, n_min=0, seed=0,
                 tp_c=None, sl_c=None, tp_x=None, sl_x=None, max_pos=14.0, shrink=1.0):
        super().__init__(account, seed=seed)
        self.name = name; self.lo, self.hi = band; self.edge = edge; self.t_min, self.t_max = window
        self.durs = set(horizons); self.assets = set(assets); self.sizing = sizing; self.period = period; self.once = once
        self.stop = stop; self.n_min = n_min; self.peak = None; self.stopped = False
        self.cap = cap if cap is not None else (None if sizing[0] == "clip" else 0.15 * CASH)
        self.max_pos = max_pos; self.shrink = shrink; self.tp_c = tp_c; self.sl_c = sl_c; self.tp_x = tp_x; self.sl_x = sl_x

    def exits(self, market, st):
        """Take-profit / stop-loss on the open position (long YES = bought Up, short YES = bought Down)."""
        if not any(v is not None for v in (self.tp_c, self.sl_c, self.tp_x, self.sl_x)): return False
        pos = self.acct.positions.get(market.slug)
        if not pos or pos.closed or abs(pos.qty) < 1e-9: return False
        if pos.qty > 0: entry, now_px, side, lim = pos.avg, market.best_bid, "sell", market.best_bid
        else: entry, now_px, side, lim = 1 - pos.avg, (1 - market.best_ask) if market.best_ask is not None else None, "buy", market.best_ask
        if now_px is None: return False
        hit = None
        if self.tp_c is not None and now_px >= entry + self.tp_c: hit = "tp"
        if self.tp_x is not None and now_px >= entry * (1 + self.tp_x): hit = "tp"
        if self.sl_c is not None and now_px <= entry - self.sl_c: hit = "sl"
        if self.sl_x is not None and now_px <= entry * (1 - self.sl_x): hit = "sl"
        if hit is None: return False
        o = self.acct.take(market, side, round(abs(pos.qty), 2), limit=lim, tag=f"{self.name} {hit} {now_px:.2f} from {entry:.2f}", fixed=True)
        if o and o.filled > 0: st["exited"] = True
        return True

    def contracts(self, price):
        kind = self.sizing[0]
        if kind == "clip": return float(self.sizing[1]) if len(self.sizing) > 1 else (10.0 if self.rng.random() < 0.1 else 2.0)
        if kind == "usd":
            lo, hi = self.sizing[1], self.sizing[2]
            return math.exp(self.rng.uniform(math.log(lo), math.log(hi))) / price
        return self.sizing[1] * self.acct.start_cash / price      # pct

    def on_tick(self, market, ctx, now):
        if market.asset not in self.assets or market.dur not in self.durs: return
        slug = market.slug; st = self.m(slug); left = market.remaining(now); acct = self.acct
        if left < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return
        self.manage(market, ctx, now)
        if self.exits(market, st) or st.get("exited"): return
        if self.stopped or now < st["next_take"] or (self.once and st.get("done")): return
        if self.stop is not None:
            self.peak = max(self.peak or acct.cash, acct.cash)
            if acct.cash < (1 - self.stop) * self.peak: self.stopped = True; return
        if left < self.t_min or (self.t_max is not None and left > self.t_max) or now - market.start < 10: return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None or det.get("n_known", 0) < self.n_min: return
        if self.shrink < 1.0:      # blend the model with the market's own price: the lab showed the model overrates long shots
            p = self.shrink * p + (1 - self.shrink) * (bb + ba) / 2
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
    # K. take-profit / stop-loss (original rules otherwise)
    "br51": ("Take profit +10c", {"tp_c": 0.10}),
    "br52": ("Take profit +20c", {"tp_c": 0.20}),
    "br53": ("Take profit when the price doubles", {"tp_x": 1.0}),
    "br54": ("Stop loss -10c", {"sl_c": 0.10}),
    "br55": ("Stop loss when the price halves", {"sl_x": 0.5}),
    "br56": ("Take profit +15c, stop loss -10c", {"tp_c": 0.15, "sl_c": 0.10}),
    "br57": ("Take profit at 2x, stop loss at half", {"tp_x": 1.0, "sl_x": 0.5}),
    "br58": ("30-40c, take profit +15c, stop loss -10c", {"band": (0.30, 0.40), "tp_c": 0.15, "sl_c": 0.10}),
    "br59": ("$0.10-$100, take profit +20c, stop loss -15c", {"sizing": (M, 0.1, 100.0), "tp_c": 0.20, "sl_c": 0.15}),
    "br60": ("Edge 5c, 2% orders, take profit at 2x, stop loss at half", {"edge": 0.05, "sizing": ("pct", 0.02), "cap": 40.0, "tp_x": 1.0, "sl_x": 0.5}),
}


# ---- round 2 (br61-br110), built from the first 4.5 hours of the lab -------------------------------------------
# Lesson from the losers: below 40c the side bought won far less often than its price implied (20-40c returned
# 47-63 cents per dollar), while 40-60c returned $1.36-$1.45. Large or percentage orders on the original rules went
# broke within hours. Round 2 centres on 40-60c, trades more often (entries every 2-3 s, bigger per-window limits),
# keeps orders small, varies the edge and the minute rules, and tests blending the model with the market price.
F = dict(period=3.0, max_pos=40.0)          # "trade more" defaults for round 2
def v(**kw): return {**F, **kw}
VARIANTS.update({
    # L. price bands around coin-flip
    "br61": ("40-60c, edge 3c, every 3 s", v(band=(0.40, 0.60), edge=0.03)),
    "br62": ("40-60c, edge 2c, every 3 s", v(band=(0.40, 0.60), edge=0.02)),
    "br63": ("40-60c, edge 1c, every 2 s", v(band=(0.40, 0.60), edge=0.01, period=2.0)),
    "br64": ("40-60c, edge 5c, every 3 s", v(band=(0.40, 0.60), edge=0.05)),
    "br65": ("45-55c, edge 2c, every 2 s", v(band=(0.45, 0.55), edge=0.02, period=2.0)),
    "br66": ("45-55c, edge 3c, every 3 s", v(band=(0.45, 0.55), edge=0.03)),
    "br67": ("50-60c, edge 2c", v(band=(0.50, 0.60), edge=0.02)),
    "br68": ("40-50c, edge 2c", v(band=(0.40, 0.50), edge=0.02)),
    "br69": ("35-65c, edge 3c", v(band=(0.35, 0.65), edge=0.03)),
    "br70": ("45-65c, edge 3c", v(band=(0.45, 0.65), edge=0.03)),
    "br71": ("40-70c, edge 3c", v(band=(0.40, 0.70), edge=0.03)),
    # M. minute rules (40-60c, edge 2c)
    "br72": ("40-60c, edge 2c, last 3 minutes", v(band=(0.40, 0.60), edge=0.02, window=(10, 180))),
    "br73": ("40-60c, edge 2c, last 2 minutes", v(band=(0.40, 0.60), edge=0.02, window=(10, 120))),
    "br74": ("40-60c, edge 2c, last 60 seconds", v(band=(0.40, 0.60), edge=0.02, window=(5, 60), period=2.0)),
    "br75": ("40-60c, edge 2c, first half (150 s+ left)", v(band=(0.40, 0.60), edge=0.02, window=(150, None))),
    "br76": ("40-60c, edge 2c, middle (60-180 s left)", v(band=(0.40, 0.60), edge=0.02, window=(60, 180))),
    "br77": ("40-60c, edge 2c, any time but the last 20 s", v(band=(0.40, 0.60), edge=0.02, window=(20, None))),
    # N. horizons and coins
    "br78": ("40-60c, edge 2c, 5-minute windows", v(band=(0.40, 0.60), edge=0.02, horizons=(300,))),
    "br79": ("40-60c, edge 2c, 15-minute windows", v(band=(0.40, 0.60), edge=0.02, horizons=(900,))),
    "br80": ("40-60c, edge 2c, 1-hour windows", v(band=(0.40, 0.60), edge=0.02, horizons=(3600,))),
    "br81": ("40-60c, edge 2c, BTC + ETH", v(band=(0.40, 0.60), edge=0.02, assets=("btc", "eth"))),
    "br82": ("40-60c, edge 2c, all four coins", v(band=(0.40, 0.60), edge=0.02, assets=("btc", "eth", "sol", "xrp"))),
    "br83": ("45-55c, edge 2c, all four coins, every 2 s", v(band=(0.45, 0.55), edge=0.02, assets=("btc", "eth", "sol", "xrp"), period=2.0)),
    # O. small sizing, many trades
    "br84": ("40-60c, edge 2c, orders $1-$5", v(band=(0.40, 0.60), edge=0.02, sizing=(M, 1.0, 5.0), cap=30.0)),
    "br85": ("40-60c, edge 2c, 0.5% orders, 3% per window", v(band=(0.40, 0.60), edge=0.02, sizing=("pct", 0.005), cap=30.0)),
    "br86": ("40-60c, edge 2c, 1% orders, 4% per window", v(band=(0.40, 0.60), edge=0.02, sizing=("pct", 0.01), cap=40.0)),
    "br87": ("40-60c, edge 2c, orders $0.10-$10", v(band=(0.40, 0.60), edge=0.02, sizing=(M, 0.1, 10.0), cap=30.0)),
    "br88": ("40-60c, edge 2c, 5-contract clips, 60 per window", v(band=(0.40, 0.60), edge=0.02, sizing=("clip", 5.0), max_pos=60.0)),
    # P. model blended with the market (fix suggested by the losers)
    "br89": ("40-60c, edge 2c, model 50/50 with market", v(band=(0.40, 0.60), edge=0.02, shrink=0.5)),
    "br90": ("30-70c, edge 2c, model 50/50 with market", v(band=(0.30, 0.70), edge=0.02, shrink=0.5)),
    "br91": ("Original band 7-70c, model 50/50 with market", v(band=(0.07, 0.70), edge=0.03, shrink=0.5)),
    "br92": ("Original band 7-70c, model 30/70 with market", v(band=(0.07, 0.70), edge=0.03, shrink=0.3)),
    "br93": ("25-75c, edge 2c, model 50/50 with market", v(band=(0.25, 0.75), edge=0.02, shrink=0.5)),
    # Q. favourite side
    "br94": ("Favourite 50-70c, edge 2c", v(band=(0.50, 0.70), edge=0.02)),
    "br95": ("Favourite 55-75c, edge 2c", v(band=(0.55, 0.75), edge=0.02)),
    # R. model-confirmed final minute
    "br96": ("40-60c, edge 2c, 20+ settlement seconds known, every 2 s", v(band=(0.40, 0.60), edge=0.02, n_min=20, period=2.0)),
    "br97": ("40-60c, edge 1c, 40+ settlement seconds known, every 2 s", v(band=(0.40, 0.60), edge=0.01, n_min=40, period=2.0)),
    # S. combinations
    "br98": ("40-60c, 2c, last 3 min, 1%, BTC+ETH, every 2 s", v(band=(0.40, 0.60), edge=0.02, window=(10, 180), sizing=("pct", 0.01), cap=40.0, assets=("btc", "eth"), period=2.0)),
    "br99": ("45-55c, 2c, all coins, $1-$5, every 2 s", v(band=(0.45, 0.55), edge=0.02, assets=("btc", "eth", "sol", "xrp"), sizing=(M, 1.0, 5.0), cap=30.0, period=2.0)),
    "br100": ("40-60c, 3c, 5m+15m, BTC+ETH, 0.5%", v(band=(0.40, 0.60), edge=0.03, horizons=(300, 900), assets=("btc", "eth"), sizing=("pct", 0.005), cap=30.0)),
    "br101": ("40-60c, 2c, blended model, last 2 min, 1%", v(band=(0.40, 0.60), edge=0.02, shrink=0.5, window=(10, 120), sizing=("pct", 0.01), cap=40.0)),
    # T. exits and controls on 40-60c
    "br102": ("40-60c, edge 2c, take profit +10c", v(band=(0.40, 0.60), edge=0.02, tp_c=0.10)),
    "br103": ("40-60c, edge 2c, stop loss -10c", v(band=(0.40, 0.60), edge=0.02, sl_c=0.10)),
    "br104": ("40-60c, edge 2c, TP +15c / SL -10c", v(band=(0.40, 0.60), edge=0.02, tp_c=0.15, sl_c=0.10)),
    "br105": ("40-60c, edge 2c, stop after 30% drawdown", v(band=(0.40, 0.60), edge=0.02, stop=0.30)),
    "br106": ("40-60c, edge 2c, one entry per window", v(band=(0.40, 0.60), edge=0.02, once=True)),
    "br107": ("40-60c, edge 3c, original pace (every 6 s, 14 max)", {"band": (0.40, 0.60), "edge": 0.03}),
    "br108": ("35-50c, edge 2c", v(band=(0.35, 0.50), edge=0.02)),
    "br109": ("50-65c, edge 2c", v(band=(0.50, 0.65), edge=0.02)),
    "br110": ("40-60c, no edge required (band only)", v(band=(0.40, 0.60), edge=-1.0)),
})
# ---- round 3 (br111-br140): 40-60c only, from the first ~6 hours of 40-60c entries --------------------------------
# What the 40-60c entries showed: 5-minute BTC windows with 2-5 minutes left paid back $1.77 per $1 (the bulk of the
# sample); 15-minute windows entered with more than 5 minutes left paid back $0.55; the band without the model (br110)
# lost everything; other coins (br82, br99) and early take-profit/stop-loss exits (br56) were below $1; larger edges
# (7-10c) and 5-minute-only (br35) were the best. So the default here: BTC 5-minute windows, 2-5 minutes left, model
# required, 5c edge, $0.10-$100 orders capped at $100 per window, held to settlement.
G = dict(band=(0.40, 0.60), horizons=(300,), window=(120, 290), edge=0.05, sizing=(M, 0.1, 100.0), cap=100.0, period=3.0)
def g(**kw): return {**G, **kw}
VARIANTS.update({
    # U. edge, on the default (5m, 2-5 min left)
    "br111": ("R3 40-60c 5m, 2-5 min left, edge 3c", g(edge=0.03)),
    "br112": ("R3 40-60c 5m, 2-5 min left, edge 5c (round-3 default)", g()),
    "br113": ("R3 40-60c 5m, 2-5 min left, edge 7c", g(edge=0.07)),
    "br114": ("R3 40-60c 5m, 2-5 min left, edge 10c", g(edge=0.10)),
    # V. narrower bands inside 40-60c
    "br115": ("R3 40-50c, 5m, 2-5 min left, edge 5c", g(band=(0.40, 0.50))),
    "br116": ("R3 50-60c, 5m, 2-5 min left, edge 5c", g(band=(0.50, 0.60))),
    "br117": ("R3 45-55c, 5m, 2-5 min left, edge 5c", g(band=(0.45, 0.55))),
    "br118": ("R3 42-58c, 5m, 2-5 min left, edge 7c", g(band=(0.42, 0.58), edge=0.07)),
    # W. minute rules
    "br119": ("R3 40-60c 5m, first 2 min only (3-5 min left)", g(window=(180, 290))),
    "br120": ("R3 40-60c 5m, 1-4 min left", g(window=(60, 240))),
    "br121": ("R3 40-60c 5m, any time except last 60 s", g(window=(60, 290))),
    "br122": ("R3 40-60c 15m, last 5 min only (not last 30 s)", g(horizons=(900,), window=(30, 300))),
    "br123": ("R3 40-60c 15m, 2-5 min left, edge 7c", g(horizons=(900,), window=(120, 300), edge=0.07)),
    "br124": ("R3 40-60c 5m 2-5 min + 15m last 5 min", g(horizons=(300, 900), window=(30, 300))),
    # X. sizing
    "br125": ("R3 default, 1% orders", g(sizing=("pct", 0.01), cap=40.0)),
    "br126": ("R3 default, 2% orders", g(sizing=("pct", 0.02), cap=60.0)),
    "br127": ("R3 default, $1-$50 orders", g(sizing=(M, 1.0, 50.0))),
    "br128": ("R3 default, 5-contract clips, 60 per window", g(sizing=("clip", 5.0), cap=None, max_pos=60.0)),
    "br129": ("R3 default, $0.10-$100, cap $50 per window", g(cap=50.0)),
    # Y. pace
    "br130": ("R3 edge 7c, entry every 2 s", g(edge=0.07, period=2.0)),
    "br131": ("R3 edge 7c, entry every 6 s", g(edge=0.07, period=6.0)),
    "br132": ("R3 edge 7c, entry every 10 s", g(edge=0.07, period=10.0)),
    "br133": ("R3 edge 5c, one entry per window", g(once=True)),
    # Z. model blend and brakes
    "br134": ("R3 default, model 70/30 with market", g(shrink=0.7, edge=0.03)),
    "br135": ("R3 default, model 50/50 with market", g(shrink=0.5, edge=0.02)),
    "br136": ("R3 default, stop after 30% drawdown", g(stop=0.30)),
    "br137": ("R3 edge 7c, stop after 20% drawdown, 1% orders", g(edge=0.07, stop=0.20, sizing=("pct", 0.01), cap=40.0)),
    # AA. checks
    "br138": ("R3 default on ETH instead of BTC", g(assets=("eth",), edge=0.07)),
    "br139": ("R3 default with take profit +25c", g(tp_c=0.25)),
    "br140": ("R3 control: 40-60c, 5m+15m, any time, edge 5c", g(horizons=(300, 900), window=(10, None))),
})
LAB_LABELS = {k: v[0] for k, v in VARIANTS.items()}


def build_lab_registry():
    return {k: (lambda acct, k=k, kw=kw, i=i: BoneVar(acct, k, seed=100 + i, **kw)) for i, (k, (_, kw)) in enumerate(VARIANTS.items())}
