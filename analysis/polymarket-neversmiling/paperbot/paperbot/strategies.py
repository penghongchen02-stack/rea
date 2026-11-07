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


SWEEP = {
    # name:            (t_lo, t_hi, z_min, max_price, entry,  durs,        assets,                        n_min)
    "v_mid_take":      (90,   180,  3.0,   0.85,      "take", (300,),      ("btc", "eth"),                0),
    "v_mid_rest":      (90,   180,  3.0,   0.85,      "rest", (300,),      ("btc", "eth"),                0),
    "v_early_z2":      (30,   90,   2.0,   0.90,      "take", (300,),      ("btc", "eth"),                0),
    "v_early_rest":    (30,   90,   3.0,   0.90,      "rest", (300,),      ("btc", "eth"),                0),
    "v_15m_early":     (60,   180,  3.0,   0.90,      "take", (900,),      ("btc", "eth"),                0),
    "v_15m_rest":      (60,   180,  3.0,   0.90,      "rest", (900,),      ("btc", "eth"),                0),
    "v_open_mom":      (210,  280,  2.0,   0.65,      "take", (300,),      ("btc", "eth"),                0),
    "v_cheap":         (60,   280,  3.0,   0.70,      "take", (300, 900),  ("btc", "eth"),                0),
    "v_all_assets":    (30,   90,   3.0,   0.90,      "take", (300,),      ("btc", "eth", "sol", "xrp"),  0),
    "v_strict":        (30,   120,  4.0,   0.92,      "take", (300, 900),  ("btc", "eth"),                0),
}
