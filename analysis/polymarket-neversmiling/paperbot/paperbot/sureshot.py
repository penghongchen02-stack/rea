"""Sure-shot bots (user, 11-13 12:30Z; they replace the stop-and-flip dashboard on port 8092): fifteen versions of one
idea, $1,000 each, on the fast loop.

The idea: Coinbase and the benchmark index show a coin's price before Polymarket's Up/Down prices follow. Near the end
of a 5-minute window, when the settlement is strongly decided on those prices and the winning side is still cheap on
Polymarket, buy it at once, one bet a window, and hold to the close. No stop, no flip.

"Decided" is the model's z (fair.p_up): how far the projected settlement is from the price to beat, in units of how
much it can still move. FAIR / STRONG / VERY are the levels the versions use. "Cheap" is the highest price a version
pays. A "fresh" version fires only within that many seconds of the result becoming decided, when Polymarket is most
likely still behind. The bet is 0.5% of the settled bank at a 2c profit per contract up to 3% at 17c, once per window.

What the records said before this was built (about 2,750 late bets of the older once-a-second bots, which entered at
z >= 1.5 to 2): the side won slightly less often than its price in every price range, and worst when it was cheap
(85-90c: won 81%; 90-93c: 89%; 93-95c: 93%). So a cheap price late in a window has usually meant the market knew
something, not that it was behind. These bots test whether reacting within a pass of the fast loop, and the fresh
versions in particular, finds the moments where it really is behind. Paper fills at the price seen are the best case:
real orders race other fast traders for the same quote.
"""
from .strategies import Base
from .fair import p_up

FAIR = 1.5; STRONG = 2.0; VERY = 2.5; MIN_PROFIT = 0.02      # z levels; MIN_PROFIT per contract after the fee, if it wins


class SureShot(Base):
    def __init__(self, account, name, last=60, z_min=FAIR, max_price=0.90, fresh=None, agree=False, assets=("btc", "eth", "sol", "xrp")):
        super().__init__(account)
        self.name = name; self.last = last; self.z_min = z_min; self.max_price = max_price; self.fresh = fresh; self.agree = agree; self.assets = set(assets)

    def on_tick(self, market, ctx, now):
        if market.asset not in self.assets or market.dur != 300: return
        left = market.remaining(now)
        if left > self.last or left < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return
        st = self.mem.setdefault(market.slug, {"done": False, "since": None, "want": None})
        if st["done"]: return
        acct = self.acct
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None or det.get("index_age", 99) > 8: return
        z = det["z"]
        if abs(z) < self.z_min: st["since"] = None; return
        if st["since"] is None: st["since"] = now                      # the moment the result became decided
        if self.fresh is not None and now - st["since"] > self.fresh: return      # Polymarket has had time to catch up
        up = z > 0; price = ba if up else 1 - bb                       # what the winning side costs now
        if price > self.max_price or price < 0.50: return
        if self.agree:                                                 # the published benchmark must be on the same side
            its, v = ctx["index"].last(market.asset)
            if v is None or market.p2b is None or (v > market.p2b) != up: return
        profit = 1 - price - acct.theta * price * (1 - price)          # per contract, if it wins
        if profit < MIN_PROFIT: return
        if st["want"] is None:      # the bet is sized once; thin books may fill it in several pieces, on the same side only
            st["want"] = round(acct.bank() * (0.005 + 0.025 * min(1.0, (profit - MIN_PROFIT) / 0.15)) / price, 2); st["up"] = up
        if up != st["up"]: st["done"] = True; return
        pos = acct.positions.get(market.slug); qty = round(st["want"] - (abs(pos.qty) if pos else 0.0), 2)
        if qty < max(market.min_qty, 0.01): st["done"] = True; return
        acct.take(market, "buy" if up else "sell", qty, limit=ba if up else bb, tag=f"ss-{'up' if up else 'down'} z{z:.1f} {price:.2f} {left:.0f}s", fixed=True)


# Loosened twice on 11-13 at the user's request ("so it can fire"). Measured over four window endings: whenever z was
# 2.5 or more there was no seller of the decided side at any price, and at z 1-2 the side already cost 92-99c (z 1.9:
# 99c, where the model's own figure is 97%). Polymarket is not behind the model; it is at or ahead of it. So the levels
# are now z 1.5 (FAIR), 2.0 (STRONG) and 2.5 (VERY), which is where the older late bots traded: on record about
# break-even before the taker fee (z 1.5-2.5: -4.8c a contract at 90c or less, -0.5c at 90-93c, -0.9c at 93-95c, +1.1c
# above 95c). These versions fire; the records do not say they pay.
VERSIONS = {
    # name: (what it does, settings)
    "ss01_last_90s_up_to_93c": ("last 90 s, fairly decided, pays up to 93c", {"last": 90, "max_price": 0.93}),
    "ss02_last_90s_strong_up_to_95c": ("last 90 s, strongly decided, up to 95c", {"last": 90, "z_min": STRONG, "max_price": 0.95}),
    "ss03_last_60s_up_to_90c": ("last 60 s, fairly decided, up to 90c", {}),
    "ss04_last_60s_up_to_95c": ("last 60 s, fairly decided, up to 95c", {"max_price": 0.95}),
    "ss05_last_30s_up_to_95c": ("last 30 s, fairly decided, up to 95c", {"last": 30, "max_price": 0.95}),
    "ss06_last_120s_up_to_93c": ("last 2 minutes, fairly decided, up to 93c", {"last": 120, "max_price": 0.93}),
    "ss07_coinbase_and_benchmark_agree": ("last 60 s, fairly decided, up to 95c, and the benchmark is on the same side", {"agree": True, "max_price": 0.95}),
    "ss08_bitcoin_only": ("Bitcoin only; last 60 s, fairly decided, up to 95c", {"assets": ("btc",), "max_price": 0.95}),
    "ss09_fire_within_1s_of_being_decided": ("last 90 s, up to 95c, only in the first second after it becomes fairly decided", {"last": 90, "fresh": 1.0, "max_price": 0.95}),
    "ss10_fire_within_2s_up_to_97c": ("last 90 s, up to 97c, only in the first 2 s after it becomes fairly decided", {"last": 90, "fresh": 2.0, "max_price": 0.97}),
    "ss11_last_15s_up_to_97c": ("last 15 s, fairly decided, up to 97c", {"last": 15, "max_price": 0.97}),
    "ss12_last_45s_strong_up_to_95c": ("last 45 s, strongly decided, up to 95c", {"last": 45, "z_min": STRONG, "max_price": 0.95}),
    "ss13_very_strong_up_to_97c": ("last 60 s, very strongly decided, up to 97c (rarely has a seller)", {"z_min": VERY, "max_price": 0.97}),
    "ss14_discount_up_to_85c": ("last 90 s, fairly decided, only at 85c or less", {"last": 90, "max_price": 0.85}),
    "ss15_bitcoin_and_ether_only": ("Bitcoin and Ether only; last 60 s, fairly decided, up to 95c", {"assets": ("btc", "eth"), "max_price": 0.95}),
}
LABELS = {k: v[0] for k, v in VERSIONS.items()}


def build_sureshot_registry():
    return {k: (lambda acct, k=k, kw=kw: SureShot(acct, k, **kw)) for k, (_, kw) in VERSIONS.items()}
