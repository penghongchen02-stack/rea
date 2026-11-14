"""Bot 81: Bonereaper safe. Same idea as the Bonereaper replica (bot 47): buy the cheaper side of the BTC 5-minute,
15-minute and 1-hour windows when the model says it is worth more than its price. Three changes for safety:

  * bigger edge: the model must value the side at least 7 cents above price plus fee (the replica asks 3 cents);
  * only in the last 2 minutes of a window, when at least 20 of the 60 settlement seconds are pinned and the model
    is most accurate (the replica trades from 10 s after the open);
  * small size relative to the account in every universe: each order is 1% of the starting cash and a window
    never holds more than 2% (the replica uses fixed 2- and 10-contract clips, which the dollar universes resize).

Held to settlement with the same protective exit as the other last-minute bots.
"""
from paperbot.strategies import LastCallHold
from paperbot.fair import p_up


class BonereaperSafe(LastCallHold):
    name = "bonereaper_safe"
    EDGE = 0.07; ORDER_FRAC = 0.01; WINDOW_FRAC = 0.02; T_MAX = 120; T_MIN = 8; N_MIN = 20

    def __init__(self, account, seed=81):
        super().__init__(account, seed=seed)

    def on_tick(self, market, ctx, now):
        if market.asset != "btc" or market.dur not in (300, 900, 3600): return
        slug = market.slug; st = self.m(slug); left = market.remaining(now); acct = self.acct
        if left < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return
        self.manage(market, ctx, now)
        if left > self.T_MAX or left < self.T_MIN or now < st["next_take"]: return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None or det.get("n_known", 0) < self.N_MIN or det.get("index_age", 99) > 8: return
        st["next_take"] = now + 5; theta = acct.theta
        q = self.pos(slug); cap = acct.start_cash * self.WINDOW_FRAC
        held = abs(q) * (ba if q > 0 else 1 - bb)
        if held >= cap: return
        if 0.07 <= ba <= 0.70 and p - ba - theta * ba * (1 - ba) > self.EDGE and q >= 0:
            n = round(min(acct.start_cash * self.ORDER_FRAC, cap - held) / ba, 2)
            if n >= market.min_qty: acct.take(market, "buy", n, limit=ba, tag=f"bsafe-up {ba:.2f} p{p:.2f}", fixed=True)
        elif 0.07 <= 1 - bb <= 0.70 and (1 - p) - (1 - bb) - theta * bb * (1 - bb) > self.EDGE and q <= 0:
            n = round(min(acct.start_cash * self.ORDER_FRAC, cap - held) / (1 - bb), 2)
            if n >= market.min_qty: acct.take(market, "sell", n, limit=bb, tag=f"bsafe-down {1-bb:.2f} p{p:.2f}", fixed=True)


REGISTRY = {"bonereaper_safe": lambda acct: BonereaperSafe(acct)}
