"""Two strategies on the same simulator.

OldModel  : behavioural replica of NeverSmiling as measured in the analysis (join the touch on both sides,
            periodic taker clips on both sides at any price, late top-ups of the leading side and cheap buys of the
            trailing side, no pair-cost cap, never pulls quotes, holds to settlement).
Improved  : the same universe and clip size with the fixes from the report: quotes around a model fair value and
            pulls them when the index moves, never averages down, takes liquidity only when the modelled edge beats
            the taker fee (mostly at the extremes and in the final minute where the 60-second average is largely known),
            rests take-profit orders instead of crossing the spread, and stops quoting in the pick-off zone.
"""
import random, time, math, logging
from .fair import p_up

log = logging.getLogger("strat")
LADDER = [0.8, 1.0, 1.25, 1.6, 1.75]


class Base:
    name = "base"
    def __init__(self, account, clip=1.0, max_pos=10.0, seed=1):
        self.acct = account; self.clip = clip; self.max_pos = max_pos; self.rng = random.Random(seed); self.mem = {}
    def m(self, slug): return self.mem.setdefault(slug, {"next_take": 0.0, "last_quote": (None, None)})
    def size(self): return round(self.clip * self.rng.choice(LADDER), 2)
    def pos(self, slug):
        p = self.acct.positions.get(slug); return p.qty if p else 0.0
    def on_close(self, market): self.acct.cancel_all(market.slug)


class OldModel(Base):
    name = "old"
    def on_tick(self, market, ctx, now):
        slug = market.slug; st = self.m(slug); rem = market.remaining(now); el = now - market.start
        if el < 20 or rem < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        q = self.pos(slug)
        # --- maker: join the touch on both sides, refresh when the touch moves (never pulled on index moves)
        if st["last_quote"] != (bb, ba):
            self.acct.cancel_all(slug)
            if q < self.max_pos: self.acct.place(market, "buy", bb, self.size(), post_only=True, tag="join-bid")
            if q > -self.max_pos: self.acct.place(market, "sell", ba, self.size(), post_only=True, tag="join-ask")
            st["last_quote"] = (bb, ba)
        # --- taker clips on both sides, any price; late window: top up the leader, buy the trailer cheap
        if now >= st["next_take"]:
            st["next_take"] = now + self.rng.uniform(8, 25)
            late = rem < 0.25 * market.dur
            if late and self.rng.random() < 0.6:
                side = "buy" if ba >= 0.5 else "sell"; tag = "late-leader"        # near-certainty top-up at 0.9+
            elif late:
                side = "sell" if ba >= 0.5 else "buy"; tag = "late-hedge"         # cheap trailing side
            else:
                side = self.rng.choice(["buy", "sell"]); tag = "take"
            # the original traded 1-cent books; do not cross spreads it never saw (thin windows here can quote 0.31 / 0.90)
            if ba - bb <= (0.10 if late else 0.05) and ((side == "buy" and q < self.max_pos) or (side == "sell" and q > -self.max_pos)):
                self.acct.take(market, side, self.size(), tag=tag)


class Improved(Base):
    name = "improved"
    EDGE_MAKER = 0.02
    def __init__(self, account, clip=1.0, max_pos=5.0, seed=2):
        super().__init__(account, clip, max_pos, seed)

    def on_tick(self, market, ctx, now):
        slug = market.slug; st = self.m(slug); rem = market.remaining(now); el = now - market.start
        if el < 15 or rem < 2 or market.state not in (None, "MARKET_STATE_OPEN"): return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        p_model, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p_model is None: return
        mid = (bb + ba) / 2
        p = 0.5 * p_model + 0.5 * mid                 # the market is a strong prior; the model alone was under-confident and faded winners
        q = self.pos(slug); acct = self.acct; theta = acct.theta
        resting_buy = sum(o.remaining for o in acct.open_orders(slug) if o.side == "buy"); resting_sell = sum(o.remaining for o in acct.open_orders(slug) if o.side == "sell")
        def fee(px): return theta * px * (1 - px)
        tick = market.tick
        # --- quoting around fair value; pulled whenever fair moves through a quote; off in the last 20%
        want_bid = math.floor((p - self.EDGE_MAKER) / tick) * tick; want_ask = math.ceil((p + self.EDGE_MAKER) / tick) * tick
        want_bid = min(want_bid, bb); want_ask = max(want_ask, ba)        # never cross, never improve past the touch
        pickoff_zone = rem < 0.2 * market.dur
        for o in list(acct.open_orders(slug)):
            if o.tag == "quote-bid" and (pickoff_zone or o.price > p - self.EDGE_MAKER + 1e-9 or o.price != want_bid): acct.cancel(o.id)
            if o.tag == "quote-ask" and (pickoff_zone or o.price < p + self.EDGE_MAKER - 1e-9 or o.price != want_ask): acct.cancel(o.id)
            if o.tag.startswith("tp-") and abs(q) < 1e-9: acct.cancel(o.id)
        tags = {o.tag for o in acct.open_orders(slug)}
        if not pickoff_zone and want_ask - want_bid >= 2 * tick - 1e-9:
            if "quote-bid" not in tags and q <= 0 + 1e-9 and q > -self.max_pos and 0.03 <= want_bid <= 0.97 and abs(p_model - mid) < 0.15:
                acct.place(market, "buy", want_bid, self.size(), post_only=True, tag="quote-bid")
            if "quote-ask" not in tags and q >= 0 - 1e-9 and q < self.max_pos and 0.03 <= want_ask <= 0.97 and abs(p_model - mid) < 0.15:
                acct.place(market, "sell", want_ask, self.size(), post_only=True, tag="quote-ask")
        # --- resting take-profit on inventory (maker, no fee), never averaging down
        pos = acct.positions.get(slug)
        if pos and abs(pos.qty) > 1e-9:
            if pos.qty > 0 and "tp-sell" not in tags:
                tp = max(math.ceil((pos.avg + 0.03) / tick) * tick, ba)
                if tp <= 0.99: acct.place(market, "sell", tp, round(pos.qty, 2), post_only=True, tag="tp-sell")
            if pos.qty < 0 and "tp-buy" not in tags:
                tp = min(math.floor((pos.avg - 0.03) / tick) * tick, bb)
                if tp >= 0.01: acct.place(market, "buy", tp, round(-pos.qty, 2), post_only=True, tag="tp-buy")
        # --- taking: only when modelled edge beats fee + margin; strongest in the final minute (average mostly known)
        if now >= st["next_take"]:
            st["next_take"] = now + 3
            edge_buy = p - ba - fee(ba); edge_sell = bb - p - fee(bb)
            margin = 0.03 if rem > 60 else 0.015
            agree_up = p_model > 0.5 and mid > 0.5; agree_down = p_model < 0.5 and mid < 0.5     # never fade the market's direction
            if edge_buy > margin and agree_up and (ba >= 0.85 or rem <= 60) and q + resting_buy < self.max_pos:
                acct.take(market, "buy", 2 * self.size(), limit=ba, tag="edge-buy" if rem > 60 else "twap-buy")
            elif edge_sell > margin and agree_down and (bb <= 0.15 or rem <= 60) and -q + resting_sell < self.max_pos:
                acct.take(market, "sell", 2 * self.size(), limit=bb, tag="edge-sell" if rem > 60 else "twap-sell")
