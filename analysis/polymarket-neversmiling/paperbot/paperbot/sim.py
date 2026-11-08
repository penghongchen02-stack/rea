"""Paper account and fill simulator for Polymarket US's single-instrument YES/NO model.

Positions are signed YES contracts: +q long (bought YES = "Up"), -q short (sold YES = "Down").
Taker fills pay fee = theta * qty * p * (1-p). Maker fills pay nothing (Tier 1 has no rebate).
Shorts lock $1.00 of margin per contract; buying power = cash - locked margin.
"""
import time, math, itertools, logging, collections

log = logging.getLogger("sim")
_ids = itertools.count(1)


class Order:
    __slots__ = ("id", "slug", "side", "price", "qty", "filled", "ts", "queue_ahead", "post_only", "tag")
    def __init__(self, slug, side, price, qty, post_only, tag):
        self.id = next(_ids); self.slug = slug; self.side = side; self.price = round(price, 2); self.qty = qty
        self.filled = 0.0; self.ts = time.time(); self.queue_ahead = 0.0; self.post_only = post_only; self.tag = tag
    @property
    def remaining(self): return self.qty - self.filled


class Position:
    __slots__ = ("qty", "avg", "realized", "fees", "fills", "closed")
    def __init__(self): self.qty = 0.0; self.avg = 0.0; self.realized = 0.0; self.fees = 0.0; self.fills = 0; self.closed = False


class Account:
    def __init__(self, name, cash=100.0, theta=0.095, size_mode="contracts", size_min=0.1, size_max=10.0, seed=7):
        import random
        self.name = name; self.cash = cash; self.start_cash = cash; self.theta = theta
        self.size_mode = size_mode; self.size_min = size_min; self.size_max = size_max; self.rng = random.Random(seed + hash(name) % 1000)
        self.positions = collections.defaultdict(Position); self.orders = {}; self.fills = []; self.settlements = []
        self.fees = 0.0; self.maker_fills = 0; self.taker_fills = 0; self.rejected = 0; self.events = collections.deque(maxlen=200)
        self.universe = "std"   # set by the Universe
    @property
    def liq_key(self):
        """Liquidity consumption is tracked per account: each bot is evaluated as if it alone traded against the
        displayed book, so scan order between bots never decides who 'gets' a thin level."""
        return f"{self.universe}:{self.name}"

    # ---- accounting -------------------------------------------------
    def locked_margin(self): return sum(-p.qty for p in self.positions.values() if p.qty < 0 and not p.closed)
    def buying_power(self): return self.cash - self.locked_margin() - self.open_order_reserve()
    def open_order_reserve(self):
        r = 0.0
        for o in self.orders.values():
            r += o.remaining * (o.price if o.side == "buy" else 1 - o.price)
        return r
    def cash_needed(self, slug, side, price, qty):
        """Buying power an order consumes. Only the part that opens or grows a position needs cash: selling
        contracts you hold, or buying back contracts you are short, releases cash/margin instead (it was treated
        as a new short, so unwinds and hedges of an existing leg were rejected)."""
        p = self.positions.get(slug); held = p.qty if p and not p.closed else 0.0
        closing = min(qty, held) if side == "sell" and held > 0 else (min(qty, -held) if side == "buy" and held < 0 else 0.0)
        opening = qty - closing
        return opening * (price if side == "buy" else 1 - price)

    def affordable_sets(self, legs):
        """How many complete sets of legs [(side, price)] the buying power covers (buys need price, shorts lock 1-price).
        Arbitrage must never leg: every leg of a set is sent only if all of them can be paid for."""
        need = sum((p if side == "buy" else 1 - p) for side, p in legs)
        return max(0.0, self.buying_power() * 0.98 / need) if need > 0 else 0.0

    def fee(self, price, qty, maker): return 0.0 if maker else round(self.theta * qty * price * (1 - price), 6)

    def mark_value(self, markets):
        """Mark-to-market of open positions: long at best bid, short at (1 - best ask), fallback mid."""
        v = 0.0
        for slug, p in self.positions.items():
            if p.closed or abs(p.qty) < 1e-9: continue
            m = markets.get(slug); px = None
            if m is not None:
                px = m.best_bid if p.qty > 0 else m.best_ask
                if px is None: px = m.mid
            if px is None: px = p.avg
            v += p.qty * px if p.qty > 0 else (-p.qty) * (1 - px) - (-p.qty)   # short: margin $1 locked already counted in cash? no: cash holds proceeds
        return v

    def equity(self, markets):
        # cash already includes short proceeds; a short's liability at mark is qty*px; long asset is qty*px
        e = self.cash
        for slug, p in self.positions.items():
            if p.closed or abs(p.qty) < 1e-9: continue
            m = markets.get(slug); px = None
            if m is not None:
                px = m.best_bid if p.qty > 0 else m.best_ask
                if px is None: px = m.mid
            if px is None: px = p.avg
            e += p.qty * px
        return e

    # ---- orders -------------------------------------------------------
    def size_for(self, price, qty):
        """Dollar mode: ignore the strategy's contract count and size each order to a random dollar amount
        (log-uniform between size_min and size_max) at the order price."""
        if self.size_mode != "dollar": return qty
        import math
        d = math.exp(self.rng.uniform(math.log(self.size_min), math.log(self.size_max)))
        return round(d / max(price, 0.01), 2)

    def place(self, market, side, price, qty, post_only=False, tag="", fixed=False):
        """fixed=True keeps the caller's contract count (arbitrage legs must match); otherwise dollar universes resize."""
        price = round(max(0.01, min(0.99, price)), 2); qty = round(qty if fixed else self.size_for(price, qty), 2)
        if qty < market.min_qty: return None
        need = self.cash_needed(market.slug, side, price, qty)
        if need > self.buying_power() + 1e-9:
            self.rejected += 1; return None
        o = Order(market.slug, side, price, qty, post_only, tag)
        # crossing?
        if side == "buy" and market.best_ask is not None and price >= market.best_ask:
            if post_only: self.rejected += 1; return None
            self._take(market, o); return o
        if side == "sell" and market.best_bid is not None and price <= market.best_bid:
            if post_only: self.rejected += 1; return None
            self._take(market, o); return o
        # rest on the book behind existing size at that level
        lvl = dict(market.bids if side == "buy" else market.offers)
        o.queue_ahead = lvl.get(price, 0.0); self.orders[o.id] = o
        return o

    def take(self, market, side, qty, limit=None, tag="", fixed=False):
        """Immediate-or-cancel against the current book. fixed=True keeps the caller's contract count."""
        px = market.best_ask if side == "buy" else market.best_bid
        if px is None: return None
        if limit is not None and ((side == "buy" and px > limit) or (side == "sell" and px < limit)): return None
        o = Order(market.slug, side, limit if limit is not None else px, round(qty if fixed else self.size_for(px, qty), 2), False, tag)
        if o.qty < market.min_qty: return None
        need = self.cash_needed(market.slug, side, o.price, o.qty)
        if need > self.buying_power() + 1e-9: self.rejected += 1; return None
        self._take(market, o); return o

    def _take(self, market, o):
        for px, q in market.avail(o.side, self.liq_key):
            if o.remaining <= 1e-9: break
            if o.side == "buy" and px > o.price: break
            if o.side == "sell" and px < o.price: break
            fq = min(q, o.remaining); self._fill(market, o, px, fq, maker=False)
            k = (self.liq_key, o.side, px); market.consumed[k] = market.consumed.get(k, 0.0) + fq
        if o.remaining > 1e-9 and o.id in self.orders: del self.orders[o.id]

    def cancel(self, oid): return self.orders.pop(oid, None)
    def cancel_all(self, slug=None):
        for oid in [k for k, o in self.orders.items() if slug is None or o.slug == slug]: del self.orders[oid]
    def open_orders(self, slug): return [o for o in self.orders.values() if o.slug == slug]

    # ---- fills ---------------------------------------------------------
    def _fill(self, market, o, px, qty, maker):
        fee = self.fee(px, qty, maker); p = self.positions[o.slug]; signed = qty if o.side == "buy" else -qty
        realized = 0.0
        if p.qty * signed < 0:   # reduces or flips existing position: realize vs avg
            closing = min(abs(p.qty), qty)
            realized = closing * ((px - p.avg) if p.qty > 0 else (p.avg - px))
            p.realized += realized; new_qty = p.qty + signed
            if abs(new_qty) < 1e-9: p.qty = 0.0
            elif p.qty * new_qty < 0: p.qty = new_qty; p.avg = px
            else: p.qty = new_qty
        else:
            tot = abs(p.qty) + qty; p.avg = (abs(p.qty) * p.avg + qty * px) / tot if tot else px; p.qty += signed
        self.cash += (-px * qty if o.side == "buy" else px * qty) - fee
        p.fees += fee; self.fees += fee; p.fills += 1; o.filled += qty
        if maker: self.maker_fills += 1
        else: self.taker_fills += 1
        self.fills.append({"ts": time.time(), "bot": self.name, "slug": o.slug, "side": o.side, "price": px, "qty": round(qty, 2),
                           "fee": round(fee, 5), "liq": "maker" if maker else "taker", "tag": o.tag, "realized": round(realized, 4)})
        if o.remaining <= 1e-9 and o.id in self.orders: del self.orders[o.id]

    def on_book(self, market):
        """Match resting orders against the latest book and inferred tape."""
        for o in list(self.open_orders(market.slug)):
            if o.remaining <= 1e-9: continue
            # 1) book crossed through our level: the displayed size at or through our price trades with us at our
            #    price (never more than the exchange shows), and that size is consumed
            if o.side == "buy" and market.best_ask is not None and market.best_ask <= o.price:
                got = 0.0
                for px, q in market.avail("buy", self.liq_key):
                    if px > o.price or o.remaining <= 1e-9: break
                    fq = min(q, o.remaining); self._fill(market, o, o.price, fq, maker=True); got += fq
                    k = (self.liq_key, "buy", px); market.consumed[k] = market.consumed.get(k, 0.0) + fq
                continue
            if o.side == "sell" and market.best_bid is not None and market.best_bid >= o.price:
                for px, q in market.avail("sell", self.liq_key):
                    if px < o.price or o.remaining <= 1e-9: break
                    fq = min(q, o.remaining); self._fill(market, o, o.price, fq, maker=True)
                    k = (self.liq_key, "sell", px); market.consumed[k] = market.consumed.get(k, 0.0) + fq
                continue
            # 2) trades printed at our price or better since we placed: they consume queue ahead, then us
            recent = []
            for ts, px, q in reversed(market.tape):
                if ts <= o.ts: break
                recent.append((ts, px, q))
            for ts, px, q in reversed(recent):
                if o.side == "buy" and px <= o.price + 1e-9 or o.side == "sell" and px >= o.price - 1e-9:
                    if (o.side == "buy" and px < o.price - 1e-9) or (o.side == "sell" and px > o.price + 1e-9):
                        self._fill(market, o, o.price, o.remaining, maker=True); break
                    take = max(0.0, q - o.queue_ahead); o.queue_ahead = max(0.0, o.queue_ahead - q); o.ts = ts
                    if take > 0:
                        self._fill(market, o, o.price, min(take, o.remaining), maker=True)
                        if o.remaining <= 1e-9: break

    # ---- settlement -----------------------------------------------------
    def settle(self, market, up, settle_px):
        self.cancel_all(market.slug); p = self.positions[market.slug]
        if p.closed: return 0.0
        pnl = 0.0
        if abs(p.qty) > 1e-9:
            payout = 1.0 if up else 0.0
            pnl = p.qty * (payout - p.avg)          # long: +(1-avg) or -avg ; short (qty<0): -(payout-avg) = avg-payout
            self.cash += p.qty * payout             # long receives payout; short pays payout from its locked margin
            p.realized += pnl
        p.closed = True
        rec = {"ts": time.time(), "bot": self.name, "slug": market.slug, "asset": market.asset, "horizon": market.horizon, "up": up,
               "p2b": market.p2b, "settle_px": settle_px, "final_qty": round(p.qty, 2), "pnl_settle": round(pnl, 4),
               "pnl_total": round(p.realized, 4), "fees": round(p.fees, 4), "fills": p.fills}
        self.settlements.append(rec); return rec

    def snapshot(self, markets):
        eq = self.equity(markets)
        openpos = {s: {"qty": round(p.qty, 2), "avg": round(p.avg, 4), "realized": round(p.realized, 4), "fees": round(p.fees, 4)}
                   for s, p in self.positions.items() if not p.closed and abs(p.qty) > 1e-9}
        return {"name": self.name, "cash": round(self.cash, 4), "equity": round(eq, 4), "pnl": round(eq - self.start_cash, 4),
                "buying_power": round(self.buying_power(), 4), "fees": round(self.fees, 4), "maker_fills": self.maker_fills,
                "taker_fills": self.taker_fills, "rejected": self.rejected, "open_orders": len(self.orders), "open_positions": openpos,
                "markets_settled": len(self.settlements), "markets_won": sum(1 for s in self.settlements if s["pnl_total"] > 0),
                "realized": round(sum(p.realized for p in self.positions.values()), 4)}
