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


class FinalMinute(Base):
    """Third bot: no quotes, no hedges. Trades once per window inside the final minute, when most of the
    60 one-second index prices that decide settlement are already published and the projection cannot
    plausibly flip, but only while the market price still leaves room after the taker fee."""
    name = "final"
    Z_MIN = 2.5            # projection must be >= 2.5 sd from the price to beat (about 99.4%)
    MAX_PRICE = 0.93       # do not pay more than this for the near-certain side (user observed 0.99 books are already full)
    QTY = 3.0              # contracts per shot (~$2.5)
    def __init__(self, account, seed=3):
        super().__init__(account, clip=3.0, max_pos=3.0, seed=seed)

    def on_tick(self, market, ctx, now):
        slug = market.slug; st = self.m(slug); rem = market.remaining(now)
        if st.get("done") or rem > 60 or rem < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return
        bb, ba = market.best_bid, market.best_ask
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None or det.get("sd") is None or det.get("n_known", 0) < 25: return   # most of the settlement minute must be published
        z = det["z"]
        theta = self.acct.theta
        if z >= self.Z_MIN and ba is not None and ba <= self.MAX_PRICE:
            edge = 1.0 - ba - theta * ba * (1 - ba)             # win pays $1
            if edge > 0.03:
                o = self.acct.take(market, "buy", self.QTY, limit=ba, tag=f"final-up z{z:.1f}")
                if o and o.filled > 0: st["done"] = True
        elif z <= -self.Z_MIN and bb is not None and bb >= 1 - self.MAX_PRICE:
            edge = bb - theta * bb * (1 - bb)                   # short YES: keep the sale price when Down wins
            if edge > 0.03:
                o = self.acct.take(market, "sell", self.QTY, limit=bb, tag=f"final-down z{z:.1f}")
                if o and o.filled > 0: st["done"] = True


class WhiskasReplica(Base):
    """Fourth bot: replica of x-MoneyForWhiskas as measured on its recent fills: BTC 5-minute windows only,
    maker-heavy two-sided quoting in round lots spread evenly through the window, immediate pair discipline
    (when one side fills and the inventory is lopsided, hedge the other side only if the pair still costs
    under $1.00 after fees), no merges, everything held to settlement."""
    name = "whiskas"
    PAIR_CAP = 0.99
    def __init__(self, account, clip=2.0, max_pos=6.0, seed=4):
        super().__init__(account, clip, max_pos, seed)
    def size(self): return self.clip * self.rng.choice([1, 1, 1, 2])          # round lots: mostly 1x, sometimes 2x

    def on_tick(self, market, ctx, now):
        if market.asset != "btc" or market.dur != 300: return
        slug = market.slug; st = self.m(slug); rem = market.remaining(now); el = now - market.start
        if el < 10 or rem < 15 or market.state not in (None, "MARKET_STATE_OPEN"): return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None or ba - bb > 0.06: return                 # it quotes liquid books, not 60-cent spreads
        acct = self.acct; q = self.pos(slug); pos = acct.positions.get(slug)
        theta = acct.theta
        # --- join the touch on both sides while inventory is balanced enough; refresh when the touch moves
        if st["last_quote"] != (bb, ba):
            acct.cancel_all(slug); st["last_quote"] = (bb, ba)
            if q < self.max_pos: acct.place(market, "buy", bb, self.size(), post_only=True, tag="w-bid")
            if q > -self.max_pos: acct.place(market, "sell", ba, self.size(), post_only=True, tag="w-ask")
        # --- pair discipline: lopsided inventory gets hedged by taking the other side if the pair is still under $1
        if pos and abs(q) >= 4 and now >= st["next_take"]:
            st["next_take"] = now + 5
            if q > 0:                                   # long YES at avg: sell YES at bid => pair cost = avg + (1 - bb)
                pair = pos.avg + (1 - bb) + theta * bb * (1 - bb)
                if pair <= self.PAIR_CAP: acct.take(market, "sell", min(q, 2 * self.clip), limit=bb, tag="w-hedge")
            else:                                       # short YES (sold at avg): buy YES at ask => pair = (1 - avg) + ba
                pair = (1 - pos.avg) + ba + theta * ba * (1 - ba)
                if pair <= self.PAIR_CAP: acct.take(market, "buy", min(-q, 2 * self.clip), limit=ba, tag="w-hedge")


class LastCall(Base):
    """Fifth bot, 'LastCall': the lessons of the first four. BTC and ETH only (precise indices). Never quotes
    both sides. Enters only in the final minute, once most of the 60 settlement seconds are published and the
    projection is decisive, buying the near-certain side while it still costs <= $0.93. Size scales with
    confidence (z), in small uniform clips. Rests a take-profit 3 cents above cost (maker, no fee) so an early
    lift frees the capital; otherwise holds to settlement."""
    name = "lastcall"
    MAX_PRICE = 0.93
    def __init__(self, account, seed=5):
        super().__init__(account, clip=2.0, max_pos=8.0, seed=seed)

    def shots_for(self, z):
        z = abs(z)
        return 1 if z < 4 else (2 if z < 6 else 3)          # 2, 4 or 6 contracts

    def on_tick(self, market, ctx, now):
        if market.asset not in ("btc", "eth"): return
        slug = market.slug; st = self.m(slug); rem = market.remaining(now)
        if rem < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return
        acct = self.acct; bb, ba = market.best_bid, market.best_ask; pos = acct.positions.get(slug); q = self.pos(slug)
        tick = market.tick; theta = acct.theta
        # --- take-profit on inventory: rest 3 cents better than cost, cancel it in the last 5 seconds (settlement pays anyway)
        tags = {o.tag for o in acct.open_orders(slug)}
        if pos and abs(q) > 1e-9 and rem > 5:
            if q > 0 and "lc-tp-sell" not in tags and ba is not None:
                tp = max(math.ceil((pos.avg + 0.03) / tick) * tick, ba)
                if tp <= 0.99: acct.place(market, "sell", tp, round(q, 2), post_only=True, tag="lc-tp-sell")
            if q < 0 and "lc-tp-buy" not in tags and bb is not None:
                tp = min(math.floor((pos.avg - 0.03) / tick) * tick, bb)
                if tp >= 0.01: acct.place(market, "buy", tp, round(-q, 2), post_only=True, tag="lc-tp-buy")
        elif rem <= 5: acct.cancel_all(slug)
        # --- final-minute entries
        if rem > 60 or now < st["next_take"]: return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None or det.get("n_known", 0) < 25: return
        z = det["z"]; st["next_take"] = now + 2
        n = self.shots_for(z) * self.clip
        if z >= 2.5 and ba is not None and ba <= self.MAX_PRICE and q < self.max_pos:
            if 1.0 - ba - theta * ba * (1 - ba) > 0.03:
                acct.take(market, "buy", min(n, self.max_pos - q), limit=ba, tag=f"lc-up z{z:.1f}")
        elif z <= -2.5 and bb is not None and bb >= 1 - self.MAX_PRICE and -q < self.max_pos:
            if bb - theta * bb * (1 - bb) > 0.03:
                acct.take(market, "sell", min(n, self.max_pos + q), limit=bb, tag=f"lc-down z{z:.1f}")


class LastCallTilt(LastCall):
    """LastCall plus one selective early tilt per window: between 30 s in and the midpoint, if the projection
    already favours a side by z >= 2 and that side still costs <= $0.65 with at least 4 cents of modelled edge
    after the taker fee, take 2 contracts. Everything else as LastCall (final-minute entries, resting take-profit)."""
    name = "lastcall_tilt"
    def on_tick(self, market, ctx, now):
        super().on_tick(market, ctx, now)
        if market.asset not in ("btc", "eth"): return
        slug = market.slug; st = self.m(slug); rem = market.remaining(now); el = now - market.start
        if st.get("tilted") or el < 30 or rem < 0.5 * market.dur or market.state not in (None, "MARKET_STATE_OPEN"): return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None: return
        z = det["z"]; theta = self.acct.theta; q = self.pos(slug)
        if z >= 2.0 and ba <= 0.65 and p - ba - theta * ba * (1 - ba) > 0.04 and q < self.max_pos:
            o = self.acct.take(market, "buy", self.clip, limit=ba, tag=f"lc-tilt-up z{z:.1f}")
            if o and o.filled > 0: st["tilted"] = True
        elif z <= -2.0 and bb >= 0.35 and (1 - p) - (1 - bb) - theta * bb * (1 - bb) > 0.04 and -q < self.max_pos:
            o = self.acct.take(market, "sell", self.clip, limit=bb, tag=f"lc-tilt-down z{z:.1f}")
            if o and o.filled > 0: st["tilted"] = True


class LastCallHold(LastCall):
    """LastCall without the take-profit: a near-certain position is held to settlement for the full $1.00.
    The only exit is protective: if the published seconds turn and the confidence collapses (|z| < 1), get out."""
    name = "lastcall_hold"
    EXIT_Z = 1.0
    def manage(self, market, ctx, now):
        slug = market.slug; rem = market.remaining(now); q = self.pos(slug); acct = self.acct
        if abs(q) < 1e-9 or rem < 3: return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None or det.get("n_known", 0) < 20: return
        z = det["z"]
        if q > 0 and z < self.EXIT_Z and market.best_bid is not None:
            acct.cancel_all(slug); acct.take(market, "sell", q, limit=market.best_bid, tag=f"lc-exit z{z:.1f}")
        elif q < 0 and z > -self.EXIT_Z and market.best_ask is not None:
            acct.cancel_all(slug); acct.take(market, "buy", -q, limit=market.best_ask, tag=f"lc-exit z{z:.1f}")
    def on_tick(self, market, ctx, now):
        if market.asset not in ("btc", "eth"): return
        slug = market.slug; st = self.m(slug); rem = market.remaining(now)
        if rem < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return
        self.manage(market, ctx, now)
        if rem > 60 or now < st["next_take"]: return
        bb, ba = market.best_bid, market.best_ask; q = self.pos(slug); acct = self.acct; theta = acct.theta
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None or det.get("n_known", 0) < 25: return
        z = det["z"]; st["next_take"] = now + 2; n = self.shots_for(z) * self.clip
        mid = (bb + ba) / 2 if bb is not None and ba is not None else 0.5
        sure = det.get("n_known", 0) >= 50                      # the market may be wrong only when the minute is nearly all published
        if z >= 2.5 and (mid > 0.5 or sure) and ba is not None and ba <= self.MAX_PRICE and q < self.max_pos and 1.0 - ba - theta * ba * (1 - ba) > 0.03:
            acct.take(market, "buy", min(n, self.max_pos - q), limit=ba, tag=f"lc-up z{z:.1f}")
        elif z <= -2.5 and (mid < 0.5 or sure) and bb is not None and bb >= 1 - self.MAX_PRICE and -q < self.max_pos and bb - theta * bb * (1 - bb) > 0.03:
            acct.take(market, "sell", min(n, self.max_pos + q), limit=bb, tag=f"lc-down z{z:.1f}")


class LastCallRest(LastCallHold):
    """LastCallHold, but the entry is a resting order on the book: one tick inside the touch (capped at $0.90 for
    the near-certain side) placed as soon as the math is decisive, left to be filled fee-free; only if it is still
    unfilled with 20 seconds left does it cross the spread."""
    name = "lastcall_rest"
    REST_CAP = 0.90
    def on_tick(self, market, ctx, now):
        if market.asset not in ("btc", "eth"): return
        slug = market.slug; st = self.m(slug); rem = market.remaining(now); acct = self.acct
        if rem < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return
        self.manage(market, ctx, now)
        if rem > 60 or now < st["next_take"]: return
        bb, ba = market.best_bid, market.best_ask; q = self.pos(slug); theta = acct.theta; tick = market.tick
        if bb is None or ba is None: return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None or det.get("n_known", 0) < 25: return
        z = det["z"]; st["next_take"] = now + 2; n = self.shots_for(z) * self.clip
        resting = [o for o in acct.open_orders(slug) if o.tag.startswith("lc-rest")]
        mid = (bb + ba) / 2; sure = det.get("n_known", 0) >= 50
        if z >= 2.5 and (mid > 0.5 or sure) and q < self.max_pos:
            want = min(round(bb + tick, 2), self.REST_CAP, round(ba - tick, 2))
            if rem <= 20 and ba <= self.MAX_PRICE and 1.0 - ba - theta * ba * (1 - ba) > 0.03:
                acct.cancel_all(slug); acct.take(market, "buy", min(n, self.max_pos - q), limit=ba, tag=f"lc-rest-cross z{z:.1f}")
            elif not resting and want >= 0.5:
                acct.place(market, "buy", want, min(n, self.max_pos - q), post_only=True, tag=f"lc-rest-up z{z:.1f}")
        elif z <= -2.5 and (mid < 0.5 or sure) and -q < self.max_pos:
            want = max(round(ba - tick, 2), 1 - self.REST_CAP, round(bb + tick, 2))
            if rem <= 20 and bb >= 1 - self.MAX_PRICE and bb - theta * bb * (1 - bb) > 0.03:
                acct.cancel_all(slug); acct.take(market, "sell", min(n, self.max_pos + q), limit=bb, tag=f"lc-rest-cross z{z:.1f}")
            elif not resting and want <= 0.5:
                acct.place(market, "sell", want, min(n, self.max_pos + q), post_only=True, tag=f"lc-rest-down z{z:.1f}")
        elif resting and abs(z) < 1.5:
            acct.cancel_all(slug)                      # math no longer decisive: do not leave a bid out to be picked off


class LastCallEarly(LastCallHold):
    """Probe result: by the time 25 settlement seconds are published (about 32 s before the close, given the index
    lag) the BTC book is already 0.98/0.99. This variant acts earlier, between 90 and 30 seconds before the close,
    on the spot-carried projection alone (no published-seconds requirement) with a stricter z >= 3 and a $0.90 cap.
    Holds to settlement; protective exit if the published seconds then disagree."""
    name = "lastcall_early"
    MAX_PRICE = 0.90
    def on_tick(self, market, ctx, now):
        if market.asset not in ("btc", "eth"): return
        slug = market.slug; st = self.m(slug); rem = market.remaining(now)
        if rem < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return
        self.manage(market, ctx, now)
        if rem > 90 or rem < 30 or now < st["next_take"]: return
        bb, ba = market.best_bid, market.best_ask; q = self.pos(slug); acct = self.acct; theta = acct.theta
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None or det.get("index_age", 99) > 8: return
        z = det["z"]; st["next_take"] = now + 2; n = self.shots_for(z) * self.clip
        mid = (bb + ba) / 2 if bb is not None and ba is not None else 0.5
        if z >= 3.0 and mid > 0.5 and ba is not None and ba <= self.MAX_PRICE and q < self.max_pos and 1.0 - ba - theta * ba * (1 - ba) > 0.04:
            acct.take(market, "buy", min(n, self.max_pos - q), limit=ba, tag=f"lc-early-up z{z:.1f}")
        elif z <= -3.0 and mid < 0.5 and bb is not None and bb >= 1 - self.MAX_PRICE and -q < self.max_pos and bb - theta * bb * (1 - bb) > 0.04:
            acct.take(market, "sell", min(n, self.max_pos + q), limit=bb, tag=f"lc-early-down z{z:.1f}")


class Param(LastCallHold):
    """Parametric variant used for the sweep. Enters the near-certain side when the spot-led projection is decisive
    inside a time band before the close, by taking or by resting one tick inside the touch (crossing only if still
    unfilled near the end of the band). Holds to settlement; protective exit when the published seconds disagree."""
    def __init__(self, account, name, t_lo, t_hi, z_min, max_price, entry="take", durs=(300,), assets=("btc", "eth"),
                 n_min=0, clip=2.0, max_pos=8.0, edge=0.03, seed=11):
        super().__init__(account, seed=seed)
        self.name = name; self.t_lo = t_lo; self.t_hi = t_hi; self.z_min = z_min; self.max_price = max_price; self.entry = entry
        self.durs = set(durs); self.assets = set(assets); self.n_min = n_min; self.clip = clip; self.max_pos = max_pos; self.edge = edge
        # 02:25Z: sampled z against the book: the market is already at $0.90-0.97 by the time the model reaches z 1.5, so
        # every cap below $0.95 (with a 3-cent edge) never fills. Caps are raised to $0.95 with a 1.5-cent edge, except
        # the two bots whose whole idea is the cheap side (v_cheap, v_open_mom).
        if name not in ("v_cheap", "v_open_mom"): self.max_price = max(self.max_price, 0.95); self.edge = min(self.edge, 0.015)

    def on_tick(self, market, ctx, now):
        if market.asset not in self.assets or market.dur not in self.durs: return
        slug = market.slug; st = self.m(slug); left = market.remaining(now); acct = self.acct
        if left < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return
        self.manage(market, ctx, now)
        if left > self.t_hi or left < self.t_lo or now < st["next_take"]: return
        bb, ba = market.best_bid, market.best_ask; q = self.pos(slug); theta = acct.theta; tick = market.tick
        if bb is None or ba is None: return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None or det.get("index_age", 99) > 8 or det.get("n_known", 0) < self.n_min: return
        z = det["z"]; st["next_take"] = now + 2; n = self.shots_for(z) * self.clip
        resting = [o for o in acct.open_orders(slug) if o.tag.startswith("v-rest")]
        late = left <= self.t_lo + 10
        mid = (bb + ba) / 2; sure = det.get("n_known", 0) >= 50
        if z >= self.z_min and (mid > 0.5 or sure) and q < self.max_pos:
            fee = theta * ba * (1 - ba)
            if self.entry == "take" or late:
                if ba <= self.max_price and 1.0 - ba - fee > self.edge:
                    acct.cancel_all(slug); acct.take(market, "buy", min(n, self.max_pos - q), limit=ba, tag=f"v-up z{z:.1f}")
            elif not resting:
                want = min(round(bb + tick, 2), self.max_price, round(ba - tick, 2))
                if want >= 0.5: acct.place(market, "buy", want, min(n, self.max_pos - q), post_only=True, tag=f"v-rest-up z{z:.1f}")
        elif z <= -self.z_min and (mid < 0.5 or sure) and -q < self.max_pos:
            fee = theta * bb * (1 - bb)
            if self.entry == "take" or late:
                if bb >= 1 - self.max_price and bb - fee > self.edge:
                    acct.cancel_all(slug); acct.take(market, "sell", min(n, self.max_pos + q), limit=bb, tag=f"v-down z{z:.1f}")
            elif not resting:
                want = max(round(ba - tick, 2), 1 - self.max_price, round(bb + tick, 2))
                if want <= 0.5: acct.place(market, "sell", want, min(n, self.max_pos + q), post_only=True, tag=f"v-rest-down z{z:.1f}")
        elif resting and abs(z) < self.z_min - 1:
            acct.cancel_all(slug)


class _SureSized:
    """Account wrapper for ParamSure: its entries are sized by the owner's confidence instead of by the dashboard."""
    def __init__(self, acct, owner): object.__setattr__(self, "_a", acct); object.__setattr__(self, "_o", owner)
    def __getattr__(self, k): return getattr(self._a, k)
    def __setattr__(self, k, v): setattr(self._a, k, v)
    def place(self, market, side, price, qty, post_only=False, tag="", fixed=False):
        qty = self._o.stake(market, side, price)
        return self._a.place(market, side, price, qty, post_only=post_only, tag=tag, fixed=True) if qty > 0 else None
    def take(self, market, side, qty, limit=None, tag="", fixed=False):
        px = market.best_ask if side == "buy" else market.best_bid
        qty = self._o.stake(market, side, px) if px is not None else 0.0
        return self._a.take(market, side, qty, limit=limit, tag=tag, fixed=True) if qty > 0 else None


class ParamSure(Param):
    """Param that never exits early and bets more the surer it is (user, 11-13 10:50Z, for v_early_rest): SURE_STAKES of
    the settled bank an order by |z|, at most SURE_WINDOW of the bank in one window. Held to settlement."""
    SURE_STAKES = ((4.0, 0.03), (3.0, 0.02), (2.0, 0.01), (0.0, 0.005)); SURE_WINDOW = 0.05
    def __init__(self, account, *a, **k):
        super().__init__(account, *a, **k); self.acct = _SureSized(account, self); self.EXIT_Z = -99.0; self._z = 0.0

    def stake(self, market, side, price):
        a = self.acct._a; unit = price if side == "buy" else 1 - price; pos = a.positions.get(market.slug)
        held = abs(pos.qty) * (pos.avg if pos.qty > 0 else 1 - pos.avg) if pos and abs(pos.qty) > 1e-9 else 0.0
        frac = next(f for z0, f in self.SURE_STAKES if abs(self._z) >= z0)
        usd = min(frac * a.bank(), self.SURE_WINDOW * a.bank() - held)
        return round(usd / max(unit, 0.01), 2) if usd > 0 else 0.0

    def on_tick(self, market, ctx, now):
        if market.asset in self.assets and market.dur in self.durs:
            p, det = p_up(market, ctx["index"], ctx["spot"], now)
            if p is not None: self._z = det["z"]
        super().on_tick(market, ctx, now)


SWEEP = {
    # name:            (t_lo, t_hi, z_min, max_price, entry,  durs,        assets,                        n_min)
    "v_mid_take":      (90,   180,  1.5,   0.85,      "take", (300,),      ("btc", "eth"),                0),
    "v_mid_rest":      (90,   180,  1.5,   0.85,      "rest", (300,),      ("btc", "eth"),                0),
    "v_early_z2":      (30,   90,   2.0,   0.90,      "take", (300,),      ("btc", "eth"),                0),
    "v_early_rest":    (30,   90,   1.5,   0.90,      "rest", (300,),      ("btc", "eth"),                0),
    "v_15m_early":     (60,   180,  1.5,   0.90,      "take", (900,),      ("btc", "eth"),                0),
    "v_15m_rest":      (60,   180,  1.5,   0.90,      "rest", (900,),      ("btc", "eth"),                0),
    "v_open_mom":      (210,  280,  1.0,   0.65,      "take", (300,),      ("btc", "eth"),                0),
    "v_cheap":         (60,   280,  1.5,   0.70,      "take", (300, 900),  ("btc", "eth"),                0),
    "v_all_assets":    (30,   90,   3.0,   0.90,      "take", (300,),      ("btc", "eth", "sol", "xrp"),  0),
    "v_strict":        (30,   120,  2.0,   0.92,      "take", (300, 900),  ("btc", "eth"),                0),
    # looser round (added 14:10Z after an hour with almost no fills): same idea, more room to act
    "v_loose_z2":      (30,   120,  2.0,   0.95,      "take", (300,),      ("btc", "eth"),                0),
    "v_loose_rest":    (30,   120,  2.0,   0.95,      "rest", (300,),      ("btc", "eth"),                0),
    "v_mid_z2":        (90,   240,  2.0,   0.80,      "take", (300,),      ("btc", "eth"),                0),
    "v_15m_z2":        (60,   300,  2.0,   0.90,      "take", (900,),      ("btc", "eth", "sol", "xrp"),  0),
}


class MarketFollow(LastCallHold):
    """User's rule: with 1 to 2 minutes left in a BTC/ETH 5-minute window, if the leading side is priced around
    80 cents, buy it. Pure market-following, no model, unless z_min is set (then the settlement arithmetic must
    at least mildly agree). One entry per window, 2 contracts, held to settlement (protective exit kept)."""
    def __init__(self, account, name, t_lo=60, t_hi=120, p_lo=0.75, p_hi=0.85, z_min=None, seed=21, durs=(300,)):
        super().__init__(account, seed=seed)
        self.name = name; self.t_lo = t_lo; self.t_hi = t_hi; self.p_lo = p_lo; self.p_hi = p_hi; self.z_min = z_min; self.clip = 2.0; self.max_pos = 2.0; self.durs = set(durs)

    def on_tick(self, market, ctx, now):
        if market.asset not in ("btc", "eth") or market.dur not in self.durs: return
        slug = market.slug; st = self.m(slug); left = market.remaining(now); acct = self.acct
        if left < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return
        if self.z_min is not None: self.manage(market, ctx, now)
        if st.get("done") or left > self.t_hi or left < self.t_lo: return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        z = None
        if self.z_min is not None:
            p, det = p_up(market, ctx["index"], ctx["spot"], now)
            if p is None: return
            z = det["z"]
        if self.p_lo <= ba <= self.p_hi and (z is None or z >= self.z_min):          # Up is leading at ~80c: buy Up
            o = acct.take(market, "buy", self.clip, limit=ba, tag=f"follow-up {ba:.2f}" + (f" z{z:.1f}" if z is not None else ""))
            if o and o.filled > 0: st["done"] = True
        elif self.p_lo <= 1 - bb <= self.p_hi and (z is None or z <= -self.z_min):    # Down is leading at ~80c: sell YES
            o = acct.take(market, "sell", self.clip, limit=bb, tag=f"follow-down {1-bb:.2f}" + (f" z{z:.1f}" if z is not None else ""))
            if o and o.filled > 0: st["done"] = True


def random_bots(n=5, seed=2026):
    """Randomly drawn configurations across the design space; reproducible by seed. Returns {name: (kind, params)}."""
    import random
    rng = random.Random(seed); out = {}
    for i in range(n):
        kind = rng.choice(["model", "model", "follow"])
        durs = rng.choice([(300,), (900,), (300, 900)])
        assets = rng.choice([("btc",), ("btc", "eth"), ("btc", "eth", "sol", "xrp")])
        dmax = max(durs); t_lo = rng.choice([20, 30, 60, 90]); t_hi = min(dmax - 10, t_lo + rng.choice([60, 90, 150, 240]))
        if kind == "model":
            params = dict(t_lo=t_lo, t_hi=t_hi, z_min=rng.choice([1.5, 2.0, 2.5, 3.0, 4.0]), max_price=rng.choice([0.6, 0.7, 0.8, 0.9, 0.95]),
                          entry=rng.choice(["take", "rest"]), durs=durs, assets=assets, n_min=0, clip=rng.choice([1.0, 2.0, 3.0]))
            label = "model z%.1f <=%.2f %s %ds-%ds %s %s c%g" % (params["z_min"], params["max_price"], params["entry"], t_hi, t_lo,
                     "/".join(str(d // 60) + "m" for d in durs), "+".join(a.upper() for a in assets), params["clip"])
        else:
            lo = rng.choice([0.55, 0.65, 0.75, 0.85]); hi = lo + rng.choice([0.08, 0.10, 0.15])
            params = dict(t_lo=t_lo, t_hi=t_hi, p_lo=lo, p_hi=round(hi, 2), z_min=rng.choice([None, None, 0.5, 1.0]))
            label = "follow %.2f-%.2f %ds-%ds %s" % (lo, params["p_hi"], t_hi, t_lo, "z>=%.1f" % params["z_min"] if params["z_min"] is not None else "no model")
        out[f"rnd{i+1}"] = (kind, params, label)
    return out

RANDOM_BOTS = random_bots()
# 01:58Z: the draws with z >= 2.5 never fired in 500 windows; loosened to 1.5 (labels updated)
for _k, (_kind, _p, _label) in list(RANDOM_BOTS.items()):
    if _kind == "model" and _p["z_min"] > 2.0:
        RANDOM_BOTS[_k] = (_kind, {**_p, "z_min": 1.5}, __import__("re").sub(r"model z\d\.\d", "model z1.5", _label))


class PickOff(LastCallHold):
    """Best-effort pick-off bot, handicapped by 1-second book polling: when Coinbase spot has just jumped (>= 2.5 sd
    over the last 3 s) and the YES book has not moved yet, hit the stale side at the touch in spot's direction,
    if the carried-forward model still shows edge after the fee. BTC/ETH 5-minute windows, any time with > 20 s
    left, 2 contracts, one shot per window, held to settlement with the protective exit."""
    name = "pickoff"
    def on_tick(self, market, ctx, now):
        if market.asset not in ("btc", "eth") or market.dur != 300: return
        slug = market.slug; st = self.m(slug); left = market.remaining(now); acct = self.acct
        if left < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return
        self.manage(market, ctx, now)
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        mids = st.setdefault("mids", [])
        mids.append((now, (bb + ba) / 2)); st["mids"] = [x for x in mids if now - x[0] <= 4]
        if st.get("done") or left < 20 or now < st["next_take"]: return
        spot = ctx["spot"]; a = market.asset; cb_now, cb_ts = spot.last(a); cb_then = spot.at(a, now - 3)
        if not cb_now or not cb_then or cb_ts is None or now - cb_ts > 2: return
        sig = ctx["index"].sigma(a) * cb_now * (3 ** 0.5); jump = (cb_now - cb_then) / max(sig, 1e-9)
        old_mid = next((mv for t_, mv in st["mids"] if now - t_ >= 2.5), None)
        if old_mid is None or abs(((bb + ba) / 2) - old_mid) > 0.011: return     # book already moved: nothing stale to hit
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None: return
        theta = acct.theta; st["next_take"] = now + 2
        if jump >= 2.5 and ba <= 0.90 and p - ba - theta * ba * (1 - ba) > 0.02:
            o = acct.take(market, "buy", self.clip, limit=ba, tag=f"pickoff-up j{jump:.1f}")
            if o and o.filled > 0: st["done"] = True
        elif jump <= -2.5 and bb >= 0.10 and (1 - p) - (1 - bb) - theta * bb * (1 - bb) > 0.02:
            o = acct.take(market, "sell", self.clip, limit=bb, tag=f"pickoff-down j{jump:.1f}")
            if o and o.filled > 0: st["done"] = True


class Scalper(LastCallHold):
    """High-frequency scalper for BTC/ETH 5-minute windows: whenever the model's P(Up) differs from the touch by
    more than the fee plus a margin, take 2 contracts, then rest a closing order 3 cents better (maker, no fee).
    'with' trades only in the market's own direction (mid and model on the same side of 50c); 'fade' trades
    against overshoots too. Up to 3 open scalps per window; leftovers settle at the close (protective exit kept)."""
    def __init__(self, account, name, fade=False, margin=0.04, seed=31, tp=0.03):
        super().__init__(account, seed=seed)
        self.name = name; self.fade = fade; self.margin = margin; self.clip = 2.0; self.max_pos = 6.0; self.tp = tp

    def on_tick(self, market, ctx, now):
        if market.asset not in ("btc", "eth") or market.dur != 300: return
        slug = market.slug; st = self.m(slug); left = market.remaining(now); acct = self.acct
        if left < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        tick = market.tick; q = self.pos(slug); pos = acct.positions.get(slug); theta = acct.theta
        # closing orders on inventory (maker): 3 cents better than cost
        tags = {o.tag for o in acct.open_orders(slug)}
        if pos and abs(q) > 1e-9 and left > 5:
            if q > 0 and "sc-close-sell" not in tags:
                tp = max(math.ceil((pos.avg + self.tp) / tick) * tick, ba)
                if tp <= 0.99: acct.place(market, "sell", tp, round(q, 2), post_only=True, tag="sc-close-sell")
            if q < 0 and "sc-close-buy" not in tags:
                tp = min(math.floor((pos.avg - self.tp) / tick) * tick, bb)
                if tp >= 0.01: acct.place(market, "buy", tp, round(-q, 2), post_only=True, tag="sc-close-buy")
        elif left <= 5: acct.cancel_all(slug)
        if left < 30 or now < st["next_take"] or left > market.dur - 20: return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None or det.get("index_age", 99) > 8: return
        st["next_take"] = now + 3; mid = (bb + ba) / 2
        up_ok = self.fade or mid > 0.5; down_ok = self.fade or mid < 0.5
        if up_ok and p - ba - theta * ba * (1 - ba) > self.margin and q < self.max_pos and 0.05 <= ba <= 0.95:
            acct.take(market, "buy", self.clip, limit=ba, tag=f"sc-buy m{mid:.2f} p{p:.2f}")
        elif down_ok and bb - p - theta * bb * (1 - bb) > self.margin and -q < self.max_pos and 0.05 <= bb <= 0.95:
            acct.take(market, "sell", self.clip, limit=bb, tag=f"sc-sell m{mid:.2f} p{p:.2f}")


class BurstLean(Base):
    """Replica family: taker only, bursts buying both sides in round lots; optional lean (2:1) toward the side spot
    is moving toward; only while the pair still costs under the cap after fees. Everything held to settlement."""
    name = "burst_lean"
    def __init__(self, account, seed=41, name="burst_lean", assets=("btc",), durs=(300,), lean=True, clip=3.0, pair_cap=0.98, period=(8, 14)):
        super().__init__(account, clip=clip, max_pos=4 * clip, seed=seed)
        self.name = name; self.assets = set(assets); self.durs = set(durs); self.lean = lean; self.pair_cap = pair_cap; self.period = period
    def on_tick(self, market, ctx, now):
        if market.asset not in self.assets or market.dur not in self.durs: return
        slug = market.slug; st = self.m(slug); left = market.remaining(now); acct = self.acct
        if left < 20 or now - market.start < 15 or market.state not in (None, "MARKET_STATE_OPEN") or now < st["next_take"]: return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None or ba - bb > 0.06: return
        st["next_take"] = now + self.rng.uniform(*self.period)
        spot = ctx["spot"]; cb_now, cb_ts = spot.last(market.asset); cb_then = spot.at(market.asset, now - 10)
        if not cb_now or not cb_then: return
        up_lean = cb_now >= cb_then; theta = acct.theta
        pair = ba + (1 - bb) + theta * ba * (1 - ba) + theta * bb * (1 - bb)      # buy Up at ask and Down at (1 - bid)
        q = self.pos(slug)
        n_up, n_dn = ((2, 1) if up_lean else (1, 2)) if self.lean else (1, 1)
        if pair > self.pair_cap:
            # US adaptation (01:58Z): without rebates the pair never costs under $1, so take one side only: the side spot
            # is leaning toward (lean bots) or the cheaper side (two-sided bots), and only while it costs <= $0.60
            if self.lean: side = "buy" if up_lean else "sell"
            else: side = "buy" if ba <= 1 - bb else "sell"
            px = ba if side == "buy" else 1 - bb
            if px > 0.60: return
            if side == "buy" and q + self.clip <= self.max_pos: acct.take(market, "buy", self.clip, limit=ba, tag="burst-one-up")
            elif side == "sell" and -q + self.clip <= self.max_pos: acct.take(market, "sell", self.clip, limit=bb, tag="burst-one-down")
            return
        if q + n_up * self.clip <= self.max_pos: acct.take(market, "buy", n_up * self.clip, limit=ba, tag=f"burst-up{'*' if up_lean else ''}")
        if -q + n_dn * self.clip <= self.max_pos: acct.take(market, "sell", n_dn * self.clip, limit=bb, tag=f"burst-down{'*' if not up_lean else ''}")


class FairValue(LastCallHold):
    """Replica: 5-minute BTC/ETH, 6-contract round lots, taker only, any time in the window including late. Buys
    whichever side is cheap against the model's probability by more than the fee plus 2 cents, up to $0.95. Holds."""
    name = "fair_value"
    def __init__(self, account, seed=42): super().__init__(account, seed=seed); self.clip = 6.0; self.max_pos = 12.0
    def on_tick(self, market, ctx, now):
        if market.asset not in ("btc", "eth") or market.dur != 300: return
        slug = market.slug; st = self.m(slug); left = market.remaining(now); acct = self.acct
        if left < 5 or now - market.start < 15 or market.state not in (None, "MARKET_STATE_OPEN"): return
        self.manage(market, ctx, now)
        if now < st["next_take"]: return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None or det.get("index_age", 99) > 8: return
        st["next_take"] = now + 4; theta = acct.theta; q = self.pos(slug)
        if p - ba - theta * ba * (1 - ba) > 0.02 and ba <= 0.95 and q < self.max_pos:
            acct.take(market, "buy", self.clip, limit=ba, tag=f"fv-up p{p:.2f}")
        elif bb - p - theta * bb * (1 - bb) > 0.02 and bb >= 0.05 and -q < self.max_pos:
            acct.take(market, "sell", self.clip, limit=bb, tag=f"fv-down p{p:.2f}")


class Crouch(LastCallHold):
    """Replica: 5- and 15-minute BTC/ETH. In the last quarter of the window it buys the near-certain side at
    80 to 93 cents when the model at least mildly agrees (z >= 1.5). One entry per window, 3 contracts, holds."""
    name = "crouch"
    def __init__(self, account, seed=43, name="crouch", frac=0.25, p_lo=0.80, p_hi=0.93, z_min=1.5):
        super().__init__(account, seed=seed); self.clip = 3.0; self.max_pos = 3.0; self.name = name; self.frac = frac; self.p_lo = p_lo; self.p_hi = p_hi; self.z_min = z_min
    def on_tick(self, market, ctx, now):
        if market.asset not in ("btc", "eth") or market.dur not in (300, 900): return
        slug = market.slug; st = self.m(slug); left = market.remaining(now); acct = self.acct
        if left < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return
        self.manage(market, ctx, now)
        if st.get("done") or left > self.frac * market.dur or left < 10: return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None: return
        z = det["z"]
        if self.p_lo <= ba <= self.p_hi and z >= self.z_min:
            o = acct.take(market, "buy", self.clip, limit=ba, tag=f"crouch-up {ba:.2f} z{z:.1f}")
            if o and o.filled > 0: st["done"] = True
        elif self.p_lo <= 1 - bb <= self.p_hi and z <= -self.z_min:
            o = acct.take(market, "sell", self.clip, limit=bb, tag=f"crouch-down {1-bb:.2f} z{z:.1f}")
            if o and o.filled > 0: st["done"] = True


def build_extra_registry(pos_mult=1.0):
    """Bots that exist only where they are asked for (the tryout's add.json, src "extra"), not on every dashboard."""
    def f(acct):
        st = ParamSure(acct, "v_early_rest_hold_and_bet_more_when_sure", *SWEEP["v_early_rest"]); st.max_pos *= pos_mult; return st
    return {"v_early_rest_hold_and_bet_more_when_sure": f}


def build_registry(pos_mult=1.0):
    """Every bot in the competition, as name -> factory(account) (ladder bots are built by the engine, they need
    the feeds). Add a bot here, or drop a module in paperbot/bots/ that defines REGISTRY = {name: factory}, and it
    appears in every universe/dashboard automatically."""
    def scaled(cls, *a, **k):
        def f(acct):
            st = cls(acct, *a, **k); st.max_pos *= pos_mult; return st
        return f
    reg = {
        "old": scaled(OldModel), "improved": scaled(Improved), "final": scaled(FinalMinute), "whiskas": scaled(WhiskasReplica),
        "lastcall": scaled(LastCall), "lastcall_tilt": scaled(LastCallTilt), "lastcall_hold": scaled(LastCallHold),
        "lastcall_rest": scaled(LastCallRest), "lastcall_early": scaled(LastCallEarly),
    }
    for k, v in SWEEP.items(): reg[k] = (lambda k=k, v=v: scaled(Param, k, *v))()
    reg["follow80"] = scaled(MarketFollow, "follow80"); reg["follow80_model"] = scaled(MarketFollow, "follow80_model", z_min=1.0)
    for k, (kind, params, label) in RANDOM_BOTS.items():
        if kind == "model": reg[k] = (lambda k=k, params=params: scaled(Param, k, params["t_lo"], params["t_hi"], params["z_min"], params["max_price"], params["entry"], params["durs"], params["assets"], 0, clip=params["clip"]))()
        else: reg[k] = (lambda k=k, params=params: scaled(MarketFollow, k, params["t_lo"], params["t_hi"], params["p_lo"], params["p_hi"], params["z_min"]))()
    reg["pickoff"] = scaled(PickOff)
    reg["hourly_last"] = scaled(Param, "hourly_last", 30, 120, 1.5, 0.90, "take", (3600,), ("btc", "eth"), 0)
    reg["hourly_rest"] = scaled(Param, "hourly_rest", 30, 120, 1.5, 0.90, "rest", (3600,), ("btc", "eth"), 0)
    reg["hourly_follow80"] = scaled(MarketFollow, "hourly_follow80", 60, 180, 0.75, 0.85, None, durs=(3600,))
    reg["scalper_with"] = scaled(Scalper, "scalper_with", fade=False); reg["scalper_fade"] = scaled(Scalper, "scalper_fade", fade=True)
    reg["burst_lean"] = scaled(BurstLean); reg["fair_value"] = scaled(FairValue); reg["crouch"] = scaled(Crouch)
    reg["norm1e69"] = scaled(BurstLean, name="norm1e69", assets=("btc", "eth", "sol"), durs=(300, 900), lean=False, clip=3.0, pair_cap=0.98)
    reg["b55f"] = scaled(BurstLean, name="b55f", assets=("btc", "eth", "sol", "xrp"), durs=(900,), lean=False, clip=2.0, pair_cap=0.98, period=(15, 30))
    reg["whiskas_taker"] = scaled(BurstLean, name="whiskas_taker", assets=("btc", "eth"), durs=(300,), lean=True, clip=1.0, pair_cap=0.97, period=(4, 8))
    reg["bonereaper"] = scaled(Bonereaper); reg["almach"] = scaled(Almach); reg["arcsine"] = scaled(Arcsine)
    reg["late_fav"] = scaled(LateFavourite)
    # constructive round (no rebates assumed anywhere)
    reg["consensus"] = scaled(Param, "consensus", 30, 90, 1.5, 0.90, "take", (300,), ("btc", "eth"), 0)
    reg["stairstep"] = scaled(Stairstep); reg["lastcent"] = scaled(LastCent)
    reg["quarter"] = scaled(Param, "quarter", 45, 150, 1.5, 0.88, "take", (900,), ("btc", "eth"), 0)
    reg["guardrail"] = scaled(Guardrail)
    # high-volume round built on the rules that stayed green (no rebates)
    reg["consensus_z2"] = scaled(Param, "consensus_z2", 30, 90, 2.0, 0.90, "take", (300,), ("btc", "eth"), 0)
    reg["consensus_wide"] = scaled(Param, "consensus_wide", 20, 120, 2.5, 0.93, "take", (300,), ("btc", "eth"), 0)
    reg["multi_entry"] = scaled(MultiEntry)
    reg["all_coins_z2"] = scaled(Param, "all_coins_z2", 30, 90, 2.0, 0.90, "take", (300,), ("btc", "eth", "sol", "xrp"), 0)
    reg["all_horizons"] = scaled(Param, "all_horizons", 30, 120, 1.5, 0.90, "take", (300, 900, 3600), ("btc", "eth"), 0)
    reg["guardrail_wide"] = scaled(Guardrail, name="guardrail_wide", t_lo=60, t_hi=240, p_lo=0.55, p_hi=0.80, z_min=1.0, tp=0.04)
    reg["scalper_tp2"] = scaled(Scalper, "scalper_tp2", fade=False, margin=0.03, tp=0.02)
    reg["momentum_cut"] = scaled(Guardrail, name="momentum_cut", t_lo=200, t_hi=285, p_lo=0.52, p_hi=0.65, z_min=1.0, tp=0.05)
    reg["rest_ladder"] = scaled(RestLadder)
    reg["crouch_wide"] = scaled(Crouch, name="crouch_wide", frac=0.40, p_lo=0.75, p_hi=0.93, z_min=1.5)
    # drop-in bots: paperbot/bots/*.py with REGISTRY = {name: factory(account)}
    import os, importlib, pkgutil
    bots_dir = os.path.join(os.path.dirname(__file__), "bots")
    if os.path.isdir(bots_dir):
        for mod in pkgutil.iter_modules([bots_dir]):
            try:
                m = importlib.import_module(f"paperbot.bots.{mod.name}")
                for k, f in getattr(m, "REGISTRY", {}).items(): reg[k] = f
            except Exception as e:
                import logging; logging.getLogger("strat").warning("bot module %s failed: %s", mod.name, e)
    return reg

LADDER_BOTS = ("ladder", "ladder_model", "touch_model", "xarb")


class Bonereaper(LastCallHold):
    """Replica: BTC 5m, 15m and 1h windows at once, buys the cheap side at 7-70 cents when the model says it is
    worth more than the price plus fee plus 3 cents; 2 contracts normally, a 10-contract block one time in ten. Holds."""
    name = "bonereaper"
    def __init__(self, account, seed=51): super().__init__(account, seed=seed); self.clip = 2.0; self.max_pos = 14.0
    def on_tick(self, market, ctx, now):
        if market.asset != "btc" or market.dur not in (300, 900, 3600): return
        slug = market.slug; st = self.m(slug); left = market.remaining(now); acct = self.acct
        if left < 10 or now - market.start < 10 or market.state not in (None, "MARKET_STATE_OPEN"): return
        self.manage(market, ctx, now)
        if now < st["next_take"]: return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None: return
        st["next_take"] = now + 6; theta = acct.theta; q = self.pos(slug); n = 10.0 if self.rng.random() < 0.1 else self.clip
        if 0.07 <= ba <= 0.70 and p - ba - theta * ba * (1 - ba) > 0.03 and q + n <= self.max_pos:
            acct.take(market, "buy", n, limit=ba, tag=f"bone-up {ba:.2f}")
        elif 0.07 <= 1 - bb <= 0.70 and (1 - p) - (1 - bb) - theta * bb * (1 - bb) > 0.03 and -q + n <= self.max_pos:
            acct.take(market, "sell", n, limit=bb, tag=f"bone-down {1-bb:.2f}")


class LateFavourite(LastCallHold):
    """Late favourite (user, 11-11 21:10Z), from the PR&R trader reports (BTC5MScour, SLIP-ME) and their TWAP settlement
    study: 5-minute windows settle on an average of the final seconds, so a clear leader late in the window rarely
    flips, while thin leads (under 1-2 bp) are still near coin flips. In the last T_MAX seconds of a 5-minute window,
    buy the side whose projected settlement leads the price to beat by at least LEAD_BP basis points, at no more than
    MAX_PRICE, when the model's probability beats the price plus the taker fee by EDGE. Never both sides; at most
    WINDOW_CAP dollars per window; held to settlement with LastCallHold's protective exit (confidence collapses)."""
    name = "late_fav"
    T_MAX = 60; T_MIN = 3; LEAD_BP = 5.0; MAX_PRICE = 0.97; EDGE = 0.01; WINDOW_CAP = 60.0; PERIOD = 2.0; ORDER_USD = 20.0
    def __init__(self, account, assets=("btc", "eth", "sol", "xrp"), durs=(300,), seed=17):
        super().__init__(account, seed=seed); self.assets = set(assets); self.durs = set(durs)
    def on_tick(self, market, ctx, now):
        if market.asset not in self.assets or market.dur not in self.durs: return
        slug = market.slug; st = self.m(slug); left = market.remaining(now); acct = self.acct
        if left < self.T_MIN or market.state not in (None, "MARKET_STATE_OPEN"): return
        self.manage(market, ctx, now)
        if left > self.T_MAX or now < st["next_take"]: return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None or det.get("index_age", 99) > 8 or not det.get("K"): return
        st["next_take"] = now + self.PERIOD
        lead = (det["mean"] - det["K"]) / det["K"] * 1e4; q = self.pos(slug); theta = acct.theta
        pos = acct.positions.get(slug); spent = abs(q) * (pos.avg if q > 0 else 1 - pos.avg) if pos and abs(q) > 1e-9 else 0.0
        if spent >= self.WINDOW_CAP: return
        if lead >= self.LEAD_BP and q >= 0 and ba <= self.MAX_PRICE and p - ba - theta * ba * (1 - ba) >= self.EDGE:
            own = acct.size_mode not in ("dollar", "pct")     # own sizing only where the dashboard sets none: about ORDER_USD per order
            acct.take(market, "buy", round(self.ORDER_USD / ba, 2) if own else self.clip, limit=ba, tag=f"late-up {lead:.1f}bp p{p:.3f}", fixed=own)
        elif lead <= -self.LEAD_BP and q <= 0 and 1 - bb <= self.MAX_PRICE and (1 - p) - (1 - bb) - theta * bb * (1 - bb) >= self.EDGE:
            own = acct.size_mode not in ("dollar", "pct")
            acct.take(market, "sell", round(self.ORDER_USD / (1 - bb), 2) if own else self.clip, limit=bb, tag=f"late-down {-lead:.1f}bp p{1 - p:.3f}", fixed=own)


class Almach(LastCallHold):
    """Replica: 5m, 15m and 1h BTC/ETH windows; buys the extremes in the last quarter: 2-cent longshots when the model
    gives them at least double the odds (20 contracts), and 90-99 cent near-certain sides when the model says
    >= 99.5% (3 contracts). Holds."""
    name = "almach"
    def __init__(self, account, seed=52): super().__init__(account, seed=seed); self.clip = 3.0; self.max_pos = 30.0
    def on_tick(self, market, ctx, now):
        if market.asset not in ("btc", "eth") or market.dur not in (300, 900, 3600): return
        slug = market.slug; st = self.m(slug); left = market.remaining(now); acct = self.acct
        if left < 5 or left > 0.25 * market.dur or market.state not in (None, "MARKET_STATE_OPEN") or now < st["next_take"]: return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None: return
        st["next_take"] = now + 5; theta = acct.theta; q = self.pos(slug)
        if ba <= 0.03 and p >= 2 * ba + 0.02 and not st.get("long_up"):
            o = acct.take(market, "buy", 20.0, limit=ba, tag=f"almach-longshot-up {ba:.2f}"); st["long_up"] = bool(o and o.filled > 0)
        elif bb >= 0.97 and (1 - p) >= 2 * (1 - bb) + 0.02 and not st.get("long_dn"):
            o = acct.take(market, "sell", 20.0, limit=bb, tag=f"almach-longshot-down {1-bb:.2f}"); st["long_dn"] = bool(o and o.filled > 0)
        elif 0.90 <= ba <= 0.99 and p >= 0.995 and not st.get("sure"):
            o = acct.take(market, "buy", self.clip, limit=ba, tag=f"almach-sure-up {ba:.2f}"); st["sure"] = bool(o and o.filled > 0)
        elif 0.90 <= 1 - bb <= 0.99 and (1 - p) >= 0.995 and not st.get("sure"):
            o = acct.take(market, "sell", self.clip, limit=bb, tag=f"almach-sure-down {1-bb:.2f}"); st["sure"] = bool(o and o.filled > 0)


class Arcsine(Base):
    """Replica of arcsine-barrister: a pure market maker that never takes. On Polymarket US the 'split then sell
    both sides' is the same as resting a sell at the ask and a buy at the bid: inventory-neutral quoting of
    5-minute BTC, 5 contracts a side, refreshed whenever the touch moves, leftovers held to settlement."""
    name = "arcsine"
    def __init__(self, account, seed=53): super().__init__(account, clip=5.0, max_pos=15.0, seed=seed)
    def on_tick(self, market, ctx, now):
        if market.asset != "btc" or market.dur != 300: return
        slug = market.slug; st = self.m(slug); left = market.remaining(now); acct = self.acct
        if left < 15 or now - market.start < 5 or market.state not in (None, "MARKET_STATE_OPEN"): return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None or ba - bb > 0.10: return
        q = self.pos(slug)
        if st["last_quote"] != (bb, ba):
            acct.cancel_all(slug); st["last_quote"] = (bb, ba)
            if q < self.max_pos: acct.place(market, "buy", bb, self.clip, post_only=True, tag="arc-bid")
            if q > -self.max_pos: acct.place(market, "sell", ba, self.clip, post_only=True, tag="arc-ask")


class Stairstep(LastCallHold):
    """Constructive #50: buy the near-certain side early and cheap (<= $0.80, z >= 3, 120-60 s before close), then add
    one clip at 60-30 s if the math still holds and the price is <= $0.90. Max 3 clips, market agreement required,
    hold to settlement with the protective exit."""
    name = "stairstep"
    def __init__(self, account, seed=61): super().__init__(account, seed=seed); self.clip = 2.0; self.max_pos = 6.0
    def on_tick(self, market, ctx, now):
        if market.asset not in ("btc", "eth") or market.dur != 300: return
        slug = market.slug; st = self.m(slug); left = market.remaining(now); acct = self.acct
        if left < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return
        self.manage(market, ctx, now)
        if left > 120 or left < 30 or now < st["next_take"]: return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None or det.get("index_age", 99) > 8: return
        z = det["z"]; st["next_take"] = now + 3; theta = acct.theta; q = self.pos(slug); mid = (bb + ba) / 2
        cap = 0.93 if left > 60 else 0.96; step = st.setdefault("steps", 0)
        if step >= 3: return
        if z >= 1.5 and mid > 0.5 and ba <= cap and 1 - ba - theta * ba * (1 - ba) > 0.015 and q < self.max_pos:
            o = acct.take(market, "buy", self.clip, limit=ba, tag=f"stair-up {ba:.2f} z{z:.1f}")
            if o and o.filled > 0: st["steps"] = step + 1
        elif z <= -1.5 and mid < 0.5 and 1 - bb <= cap and bb - theta * bb * (1 - bb) > 0.015 and -q < self.max_pos:
            o = acct.take(market, "sell", self.clip, limit=bb, tag=f"stair-down {1-bb:.2f} z{z:.1f}")
            if o and o.filled > 0: st["steps"] = step + 1


class LastCent(LastCallHold):
    """Constructive #51, the fee minimiser: in the final 25 s, once >= 40 of the 60 settlement seconds are published
    and z >= 4, rest a bid for the near-certain side at $0.94-$0.96 (maker, no fee); cross at <= $0.97 only when
    >= 50 seconds are in. 4 contracts, held to settlement."""
    name = "lastcent"
    def __init__(self, account, seed=62): super().__init__(account, seed=seed); self.clip = 4.0; self.max_pos = 4.0
    def on_tick(self, market, ctx, now):
        if market.asset not in ("btc", "eth") or market.dur not in (300, 900): return
        slug = market.slug; st = self.m(slug); left = market.remaining(now); acct = self.acct
        if left < 2 or market.state not in (None, "MARKET_STATE_OPEN"): return
        if left > 25:
            if acct.open_orders(slug): acct.cancel_all(slug)
            return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None or det.get("n_known", 0) < 30: return
        z = det["z"]; n = det["n_known"]; theta = acct.theta; q = self.pos(slug); tick = market.tick
        if abs(z) < 2.5:
            acct.cancel_all(slug); return
        resting = [o for o in acct.open_orders(slug) if o.tag.startswith("cent")]
        if z >= 4 and q < self.max_pos:
            if n >= 40 and ba <= 0.98 and 1 - ba - theta * ba * (1 - ba) > 0.01:
                acct.cancel_all(slug); acct.take(market, "buy", self.clip, limit=ba, tag=f"cent-cross-up {ba:.2f}")
            elif not resting:
                want = min(0.97, max(0.93, round(bb + tick, 2)))
                if want < ba: acct.place(market, "buy", want, self.clip, post_only=True, tag=f"cent-rest-up {want:.2f}")
        elif z <= -4 and -q < self.max_pos:
            if n >= 40 and bb >= 0.02 and bb - theta * bb * (1 - bb) > 0.01:
                acct.cancel_all(slug); acct.take(market, "sell", self.clip, limit=bb, tag=f"cent-cross-down {1-bb:.2f}")
            elif not resting:
                want = max(0.03, min(0.07, round(ba - tick, 2)))
                if want > bb: acct.place(market, "sell", want, self.clip, post_only=True, tag=f"cent-rest-down {1-want:.2f}")


class Guardrail(LastCallHold):
    """Constructive #53: follow the 60-75c leader 4 to 2 minutes out when the model agrees (z >= 1.5), rest a
    take-profit 5 cents better (maker), and cut the position at market the moment the model flips sign."""
    name = "guardrail"
    def __init__(self, account, seed=63, name="guardrail", t_lo=120, t_hi=240, p_lo=0.60, p_hi=0.75, z_min=0.5, tp=0.05, durs=(300,)):
        super().__init__(account, seed=seed); self.clip = 2.0; self.max_pos = 2.0
        self.name = name; self.t_lo = t_lo; self.t_hi = t_hi; self.p_lo = p_lo; self.p_hi = p_hi; self.z_min = z_min; self.tp = tp; self.durs = set(durs)
    def on_tick(self, market, ctx, now):
        if market.asset not in ("btc", "eth") or market.dur not in self.durs: return
        slug = market.slug; st = self.m(slug); left = market.remaining(now); acct = self.acct
        if left < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        q = self.pos(slug); pos = acct.positions.get(slug); tick = market.tick; theta = acct.theta
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None: return
        z = det["z"]
        # manage: cut when the model flips, else keep a take-profit resting
        if pos and abs(q) > 1e-9:
            tags = {o.tag for o in acct.open_orders(slug)}
            if (q > 0 and z < 0) or (q < 0 and z > 0):
                acct.cancel_all(slug); acct.take(market, "sell" if q > 0 else "buy", abs(q), limit=bb if q > 0 else ba, tag=f"guard-cut z{z:.1f}"); return
            if q > 0 and "guard-tp-sell" not in tags and left > 5:
                tp = max(math.ceil((pos.avg + self.tp) / tick) * tick, ba)
                if tp <= 0.99: acct.place(market, "sell", tp, round(q, 2), post_only=True, tag="guard-tp-sell")
            if q < 0 and "guard-tp-buy" not in tags and left > 5:
                tp = min(math.floor((pos.avg - self.tp) / tick) * tick, bb)
                if tp >= 0.01: acct.place(market, "buy", tp, round(-q, 2), post_only=True, tag="guard-tp-buy")
            return
        if st.get("done") or left > self.t_hi or left < self.t_lo or now < st["next_take"]: return
        st["next_take"] = now + 3
        if self.p_lo <= ba <= self.p_hi and z >= self.z_min:
            o = acct.take(market, "buy", self.clip, limit=ba, tag=f"guard-up {ba:.2f} z{z:.1f}")
            if o and o.filled > 0: st["done"] = True
        elif self.p_lo <= 1 - bb <= self.p_hi and z <= -self.z_min:
            o = acct.take(market, "sell", self.clip, limit=bb, tag=f"guard-down {1-bb:.2f} z{z:.1f}")
            if o and o.filled > 0: st["done"] = True


class MultiEntry(LastCallHold):
    """High-volume #56: every 10 s from 120 s out, while z >= 2.5, market agrees and the near-certain side costs
    <= $0.90, add a 2-contract clip, up to 6 per window. Hold with the protective exit."""
    name = "multi_entry"
    def __init__(self, account, seed=71): super().__init__(account, seed=seed); self.clip = 2.0; self.max_pos = 12.0
    def on_tick(self, market, ctx, now):
        if market.asset not in ("btc", "eth") or market.dur != 300: return
        slug = market.slug; st = self.m(slug); left = market.remaining(now); acct = self.acct
        if left < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return
        self.manage(market, ctx, now)
        if left > 120 or left < 20 or now < st["next_take"]: return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None or det.get("index_age", 99) > 8: return
        z = det["z"]; st["next_take"] = now + 10; theta = acct.theta; q = self.pos(slug); mid = (bb + ba) / 2
        if z >= 1.5 and mid > 0.5 and ba <= 0.95 and 1 - ba - theta * ba * (1 - ba) > 0.015 and q < self.max_pos:
            acct.take(market, "buy", self.clip, limit=ba, tag=f"multi-up {ba:.2f} z{z:.1f}")
        elif z <= -1.5 and mid < 0.5 and 1 - bb <= 0.95 and bb - theta * bb * (1 - bb) > 0.015 and -q < self.max_pos:
            acct.take(market, "sell", self.clip, limit=bb, tag=f"multi-down {1-bb:.2f} z{z:.1f}")


class RestLadder(LastCallHold):
    """High-volume #62: from 90 s out, when z >= 3 and the market agrees, rest three fee-free bids for the
    near-certain side at 88, 90 and 92 cents (2 contracts each); pulled if z drops below 2. Hold."""
    name = "rest_ladder"
    def __init__(self, account, seed=72): super().__init__(account, seed=seed); self.clip = 2.0; self.max_pos = 6.0
    def on_tick(self, market, ctx, now):
        if market.asset not in ("btc", "eth") or market.dur != 300: return
        slug = market.slug; st = self.m(slug); left = market.remaining(now); acct = self.acct
        if left < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return
        self.manage(market, ctx, now)
        if left > 90 or now < st["next_take"]: return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None: return
        z = det["z"]; st["next_take"] = now + 2; q = self.pos(slug); mid = (bb + ba) / 2
        resting = [o for o in acct.open_orders(slug) if o.tag.startswith("rl-")]
        if abs(z) < 2 or left < 5:
            if resting: acct.cancel_all(slug)
            return
        if resting or abs(q) >= self.max_pos: return
        if z >= 3 and mid > 0.5:
            for px in (0.88, 0.90, 0.92):
                if px < ba: acct.place(market, "buy", px, self.clip, post_only=True, tag=f"rl-up {px:.2f}")
        elif z <= -3 and mid < 0.5:
            for px in (0.12, 0.10, 0.08):
                if px > bb: acct.place(market, "sell", px, self.clip, post_only=True, tag=f"rl-down {1-px:.2f}")
