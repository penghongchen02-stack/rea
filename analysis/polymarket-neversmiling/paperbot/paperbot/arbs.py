"""Arbitrage family (bots 65-74). Every bot here trades only relations that are fixed by the contracts' settlement
rules, not by a price model: dominance (contract a pays whenever b pays, so a is worth at least b), tiling (a set
of contracts pays exactly $1 together), mutual exclusion (two contracts cannot both pay), and the settlement
arithmetic of the final seconds (the 60-second average is nearly determined). A trade is taken only when the
locked profit clears both legs' taker fees plus an edge, on live books, with one contract count for every leg.

Settlement facts relied on (Polymarket US crypto markets):
  * Up/Down windows, Above/Below strikes and Range buckets that end at the same instant T settle on the same
    60-second index average; Up = average >= price-to-beat, Above = average >= strike, Range = lower <= average
    <= upper (inclusive on both bounds).
  * One-touch markets ('reach $X before ...') pay when the index trades at or beyond X at any time in their window;
    an above-X' contract paying at T inside that window implies the touch (X' >= X).
"""
import time, logging, collections, math
from . import feeds
from .ladder import XArb, LadderArb, leg_fee, ABOVE, RANGE

log = logging.getLogger("arbs")


def _tick_floor(p, tick=0.01): return math.floor(p / tick + 1e-9) * tick
def _tick_ceil(p, tick=0.01): return math.ceil(p / tick - 1e-9) * tick


class ArbBase:
    """Shared plumbing: per-scan live-book budget, equal-size multi-leg execution, opportunity log."""
    name = "arb"; MIN_EDGE = 0.01; MAX_SETS = 10.0; MAX_REPEATS = 3; PERIOD = 1.0; BUDGET = 2

    def __init__(self, account, engine):
        self.acct = account; self.mf = engine.mf; self.lf = engine.lf; self.idx = engine.idx; self.spot = engine.spot
        self.stats = collections.Counter(); self.opps = collections.deque(maxlen=50); self.repeats = collections.Counter()
        self.last_scan = 0.0; self.budget = 0

    def fee(self, p): return leg_fee(self.acct.theta, p)

    def live(self, m, now):
        """True when m's book is fresh enough to trade on. Windows are polled by the engine (2-6 s, 0.9 s at the
        end); ladder and touch legs are fetched on demand within the per-scan budget (snapshots are shared)."""
        max_age = 8.0 if m.kind == "updown" else 3.0
        if now - m.book_ts <= max_age: return True
        if m.kind == "updown": return False
        if self.budget <= 0: self.stats["budget_exhausted"] += 1; return False
        self.budget -= 1
        return self.lf.fetch_book(m, max_age=max_age)

    def depth(self, m, side):
        lv = m.offers if side == "buy" else m.bids
        return lv[0][1] if lv else 0.0

    def execute(self, legs, n, key, label, edge):
        """legs: [(market, side, limit, tag)]. One contract count for every leg: dollar universes draw the dollar
        size once at the first leg's price, then every leg uses that count (capped by the thinnest book)."""
        n = min(n, round(self.acct.size_for(legs[0][2], self.MAX_SETS), 2), self.acct.affordable_sets([(s, l) for _, s, l, _ in legs]))
        n = math.floor(n * 100) / 100
        if n < 0.01: self.stats["unaffordable"] += 1; return False
        self.opps.appendleft({"ts": time.time(), "kind": label, "legs": [m.slug for m, _, _, _ in legs], "edge": round(edge, 4), "n": round(n, 2)})
        filled = 0; got = []
        for m, side, limit, tag in legs:
            o = self.acct.take(m, side, n, limit=limit, tag=tag, fixed=True)
            f = o.filled if o else 0.0; got.append(f)
            if f > 0: filled += 1
        if filled:
            self.stats["trades"] += 1; self.repeats[key] += 1
            log.info("%s %s edge %.4f x %.2f: %s", self.name, label, edge, n, ", ".join(f"{s} {m.slug}@{l:.2f}" for m, s, l, _ in legs))
            # never hold a naked leg: unwind whatever filled beyond the thinnest leg, at market
            least = min(got)
            for (m, side, limit, tag), f in zip(legs, got):
                if f - least > 0.009:
                    self.stats["unwound"] += 1
                    self.acct.take(m, "sell" if side == "buy" else "buy", round(f - least, 2), limit=None, tag=f"unwind {tag}", fixed=True)
        return bool(filled)

    def pull(self, st):
        """Cancel a resting order and stop counting it (fills already in it are still hedged by run())."""
        self.acct.cancel(st["o"].id); st["o"].qty = st["o"].filled; self.stats["cancelled"] += 1

    def scan(self, now):
        if now - self.last_scan < self.PERIOD: return
        self.last_scan = now; self.budget = self.BUDGET; self.stats["scans"] += 1
        self.run(now)

    def run(self, now): raise NotImplementedError
    def snapshot(self): return {"stats": dict(self.stats), "recent": list(self.opps)[:10]}


# ---- 65/66: cross-window dominance, tighter and windows-only ----------------------------------------------------
class XArbTight(XArb):
    """Bot 65: the cross-window arbitrage (64) with a 0.4-cent edge after fees, 25 sets and 10 repeats per pair."""
    name = "xarb_tight"; MIN_EDGE = 0.004; MAX_SETS = 25.0; MAX_REPEATS = 10


class XArbWindows(XArb):
    """Bot 66: cross-window dominance between the Up/Down windows only (5m, 15m, 1h sharing an end), every second
    on books the engine already polls; no ladder legs, so no extra requests. 0.5-cent edge, up to 50 sets."""
    name = "xarb_windows"; MIN_EDGE = 0.005; MAX_SETS = 50.0; MAX_REPEATS = 20
    def contracts(self, now):
        groups = collections.defaultdict(list)
        for m in list(self.mf.markets.values()):
            if m.kind == "updown" and m.p2b is not None and m.end - now > 8 and m.book_ts > now - 5:
                groups[(m.asset, int(m.end))].append((m.p2b, m, "live"))
        return groups


# ---- 67/68: maker-side cross-window arbitrage ---------------------------------------------------------------------
class XArbMakerBid(ArbBase):
    """Bot 67: rest the cheap leg. For a window a dominating contract b (K_a <= K_b, same asset and end), a is worth
    at least b, so a bid on a at bid(b) minus both fees minus the edge is covered: when it fills, b is sold at once
    (taker) and the pair is locked. Rests on windows only (live books, 1-2 s polling); the hedge may be a strike."""
    name = "xarb_maker_bid"; MIN_EDGE = 0.005; MAX_SETS = 10.0; MAX_HEDGED = 40.0; PERIOD = 1.0
    def __init__(self, account, engine):
        super().__init__(account, engine); self.rest = {}; self.hedged_total = {}    # slug a -> {"o": order, "b": market, "hedged": qty}

    def run(self, now):
        groups = XArb.contracts(self, now); acct = self.acct; tick = 0.01
        # 1) hedge any fills on resting orders
        for slug, st in list(self.rest.items()):
            o = st["o"]; b = st["b"]; due = o.filled - st["hedged"]
            if due > 0.009:
                if not self.live(b, now) or b.best_bid is None: continue
                h = acct.take(b, "sell", round(due, 2), limit=None, tag=f"xmb-hedge K{st['Kb']:.0f}", fixed=True)
                if h and h.filled > 0:
                    st["hedged"] += h.filled; self.stats["hedged"] += 1; self.hedged_total[slug] = self.hedged_total.get(slug, 0.0) + h.filled
                    log.info("xarb_maker_bid filled %s @%.2f x %.2f, hedged sell %s @%.2f", slug, o.price, h.filled, b.slug, h.price)
            if o.remaining <= 1e-9 and due <= 0.009: del self.rest[slug]
        # 2) (re)place resting bids
        for (asset, T), items in groups.items():
            if len(items) < 2: continue
            self.stats["groups"] += 1
            items.sort(key=lambda x: x[0])
            for i, (Ka, a, sa) in enumerate(items):
                if sa != "live" or a.best_ask is None or a.best_bid is None: continue
                best = None
                for Kb, b, sb in items[i + 1:]:
                    bid_b = b.best_bid if sb == "live" else b.list_bid
                    if bid_b is None: continue
                    if best is None or bid_b > best[0]: best = (bid_b, b, Kb)
                if best is None: continue
                bid_b, b, Kb = best
                p = _tick_floor(bid_b - self.fee(bid_b) - self.MIN_EDGE, tick)
                p = _tick_floor(p - self.fee(p), tick)
                st = self.rest.get(a.slug)
                if p >= a.best_ask or p < a.best_bid - 0.02 or p < 0.02:
                    if st and st["o"].remaining > 1e-9: self.pull(st)
                    continue
                if st and st["o"].remaining > 1e-9 and abs(st["o"].price - p) < 1e-9 and st["b"].slug == b.slug: continue
                if st and st["o"].filled - st["hedged"] > 0.009: continue      # hedge the old order's fills first
                if st and st["o"].remaining > 1e-9: self.pull(st)
                if self.hedged_total.get(a.slug, 0.0) >= self.MAX_HEDGED: continue
                n = round(acct.size_for(p, self.MAX_SETS), 2)
                o = acct.place(a, "buy", p, n, post_only=True, tag=f"xmb-rest K{Ka:.0f} vs K{Kb:.0f}", fixed=True)
                if o is None: continue
                self.stats["rested"] += 1; self.rest[a.slug] = {"o": o, "b": b, "Kb": Kb, "hedged": 0.0}
        self.stats["resting"] = sum(1 for st in self.rest.values() if st["o"].remaining > 1e-9)


class XArbMakerAsk(ArbBase):
    """Bot 68: rest the rich leg. For a window b dominated by contract a (K_a <= K_b), b is worth at most a, so an
    offer on b at ask(a) plus both fees plus the edge is covered: when it fills, a is bought at once (taker)."""
    name = "xarb_maker_ask"; MIN_EDGE = 0.005; MAX_SETS = 10.0; MAX_HEDGED = 40.0; PERIOD = 1.0
    def __init__(self, account, engine):
        super().__init__(account, engine); self.rest = {}; self.hedged_total = {}

    def run(self, now):
        groups = XArb.contracts(self, now); acct = self.acct; tick = 0.01
        for slug, st in list(self.rest.items()):
            o = st["o"]; a = st["a"]; due = o.filled - st["hedged"]
            if due > 0.009:
                if not self.live(a, now) or a.best_ask is None: continue
                h = acct.take(a, "buy", round(due, 2), limit=None, tag=f"xma-hedge K{st['Ka']:.0f}", fixed=True)
                if h and h.filled > 0:
                    st["hedged"] += h.filled; self.stats["hedged"] += 1; self.hedged_total[slug] = self.hedged_total.get(slug, 0.0) + h.filled
                    log.info("xarb_maker_ask filled %s @%.2f x %.2f, hedged buy %s @%.2f", slug, o.price, h.filled, a.slug, h.price)
            if o.remaining <= 1e-9 and due <= 0.009: del self.rest[slug]
        for (asset, T), items in groups.items():
            if len(items) < 2: continue
            self.stats["groups"] += 1
            items.sort(key=lambda x: x[0])
            for j, (Kb, b, sb) in enumerate(items):
                if sb != "live" or b.best_ask is None or b.best_bid is None: continue
                best = None
                for Ka, a, sa in items[:j]:
                    ask_a = a.best_ask if sa == "live" else a.list_ask
                    if ask_a is None: continue
                    if best is None or ask_a < best[0]: best = (ask_a, a, Ka)
                if best is None: continue
                ask_a, a, Ka = best
                p = _tick_ceil(ask_a + self.fee(ask_a) + self.MIN_EDGE, tick)
                p = _tick_ceil(p + self.fee(p), tick)
                st = self.rest.get(b.slug)
                if p <= b.best_bid or p > b.best_ask + 0.02 or p > 0.98:
                    if st and st["o"].remaining > 1e-9: self.pull(st)
                    continue
                if st and st["o"].remaining > 1e-9 and abs(st["o"].price - p) < 1e-9 and st["a"].slug == a.slug: continue
                if st and st["o"].filled - st["hedged"] > 0.009: continue
                if st and st["o"].remaining > 1e-9: self.pull(st)
                if self.hedged_total.get(b.slug, 0.0) >= self.MAX_HEDGED: continue
                n = round(acct.size_for(p, self.MAX_SETS), 2)
                o = acct.place(b, "sell", p, n, post_only=True, tag=f"xma-rest K{Kb:.0f} vs K{Ka:.0f}", fixed=True)
                if o is None: continue
                self.stats["rested"] += 1; self.rest[b.slug] = {"o": o, "a": a, "Ka": Ka, "hedged": 0.0}
        self.stats["resting"] = sum(1 for st in self.rest.values() if st["o"].remaining > 1e-9)


# ---- 69/70: one-touch dominance -------------------------------------------------------------------------------------
class TouchArb(ArbBase):
    """Bot 69: a one-touch 'reach X' pays whenever any above-X' contract (X' >= X) pays at a time T inside the touch
    window, and a 'dip to X' pays whenever any below-X' (X' <= X) does. Buy the touch, sell the dominated contract
    (strike or window) when the bid exceeds the touch's ask by more than the fees plus the edge."""
    name = "touch_arb"; MIN_EDGE = 0.01; MAX_SETS = 10.0; PERIOD = 5.0; BUDGET = 2

    def touches(self, now):
        if now - getattr(self.lf, "last_touch_discover", 0) > 600:
            try: self.lf.discover_touch()
            except Exception as e: log.warning("touch_arb discover: %s", e)
        return [m for m in getattr(self.lf, "touch_legs", {}).values() if not m.closed and m.end - now > 600 and m.strike is not None and m.direction]

    def aboves(self, now):
        """Every 'above K at T' contract: hourly/daily strikes plus the Up/Down windows (Up = above price-to-beat)."""
        out = []
        for m in list(self.lf.legs.values()):
            if m.market_type.endswith("ABOVE_BELOW") and m.strike is not None and m.end - now > 30: out.append((m.strike, m))
        for m in list(self.mf.markets.values()):
            if m.kind == "updown" and m.p2b is not None and m.end - now > 30 and m.book_ts > now - 8: out.append((m.p2b, m))
        return out

    def run(self, now):
        aboves = self.aboves(now); theta = self.acct.theta
        for t in self.touches(now):
            ask_t = t.best_ask if now - t.book_ts < 60 else t.list_ask
            if ask_t is None or ask_t > 0.97: continue
            for K, c in aboves:
                if c.asset != t.asset or c.end > t.end - 30: continue
                bid_c = c.best_bid if c.kind == "updown" or now - c.book_ts < 60 else c.list_bid
                ask_c = c.best_ask if c.kind == "updown" or now - c.book_ts < 60 else c.list_ask
                key = (t.slug, c.slug)
                if self.repeats[key] >= self.MAX_REPEATS: continue
                if t.direction == "up" and K >= t.strike and bid_c is not None:
                    est = bid_c - ask_t - self.fee(bid_c) - self.fee(ask_t)
                    if est < self.MIN_EDGE: continue
                    self.stats["candidates"] += 1
                    if not (self.live(t, now) and self.live(c, now)) or t.best_ask is None or c.best_bid is None: continue
                    edge = c.best_bid - t.best_ask - self.fee(c.best_bid) - self.fee(t.best_ask)
                    if edge < self.MIN_EDGE: self.stats["vanished"] += 1; continue
                    n = min(self.depth(t, "buy"), self.depth(c, "sell"))
                    self.execute([(t, "buy", t.best_ask, f"touch-buy {t.strike:.0f}"), (c, "sell", c.best_bid, f"touch-sell above {K:.0f}")], n, key, "touch>=above", edge)
                elif t.direction == "down" and K <= t.strike and ask_c is not None:
                    # dip-to-X dominates below-X' (= not above X'): buy the touch and buy above X'; one of them pays $1
                    est = 1 - ask_t - ask_c - self.fee(ask_t) - self.fee(ask_c)
                    if est < self.MIN_EDGE: continue
                    self.stats["candidates"] += 1
                    if not (self.live(t, now) and self.live(c, now)) or t.best_ask is None or c.best_ask is None: continue
                    edge = 1 - t.best_ask - c.best_ask - self.fee(t.best_ask) - self.fee(c.best_ask)
                    if edge < self.MIN_EDGE: self.stats["vanished"] += 1; continue
                    n = min(self.depth(t, "buy"), self.depth(c, "buy"))
                    self.execute([(t, "buy", t.best_ask, f"touch-buy dip {t.strike:.0f}"), (c, "buy", c.best_ask, f"touch-buy above {K:.0f}")], n, key, "dip>=below", edge)


class TouchLadder(ArbBase):
    """Bot 70: touch versus touch. Same asset and direction, both still open: 'reach X_a before T_a' pays whenever
    'reach X_b before T_b' pays if X_a <= X_b and T_a >= T_b (dips: X_a >= X_b). Buy the dominant, sell the other."""
    name = "touch_ladder"; MIN_EDGE = 0.01; MAX_SETS = 10.0; PERIOD = 10.0; BUDGET = 2

    def run(self, now):
        ts = TouchArb.touches(self, now)
        for a in ts:
            ask_a = a.best_ask if now - a.book_ts < 60 else a.list_ask
            if ask_a is None: continue
            for b in ts:
                if a is b or a.asset != b.asset or a.direction != b.direction or a.end < b.end: continue
                if (a.direction == "up" and a.strike > b.strike) or (a.direction == "down" and a.strike < b.strike): continue
                if a.strike == b.strike and a.end == b.end: continue
                bid_b = b.best_bid if now - b.book_ts < 60 else b.list_bid
                if bid_b is None: continue
                key = (a.slug, b.slug)
                if self.repeats[key] >= self.MAX_REPEATS: continue
                est = bid_b - ask_a - self.fee(bid_b) - self.fee(ask_a)
                if est < self.MIN_EDGE: continue
                self.stats["candidates"] += 1
                if not (self.live(a, now) and self.live(b, now)) or a.best_ask is None or b.best_bid is None: continue
                edge = b.best_bid - a.best_ask - self.fee(b.best_bid) - self.fee(a.best_ask)
                if edge < self.MIN_EDGE: self.stats["vanished"] += 1; continue
                n = min(self.depth(a, "buy"), self.depth(b, "sell"))
                self.execute([(a, "buy", a.best_ask, f"tl-buy {a.strike:.0f}"), (b, "sell", b.best_bid, f"tl-sell {b.strike:.0f}")], n, key, "touch>=touch", edge)


# ---- 71: range buckets versus the strikes that bound them ---------------------------------------------------------
class RangeArb(ArbBase):
    """Bot 71: a range bucket [lo, hi] equals above(lo) minus above(hi) (plus the exact-tie at hi). Buy the bucket
    and sell the spread when the bucket is cheaper, or sell the bucket and buy the spread when it is richer; the
    tail buckets are compared with their single bounding strike. Three legs, one size, verified live."""
    name = "range_arb"; MIN_EDGE = 0.01; MAX_SETS = 10.0; PERIOD = 15.0; BUDGET = 3

    def run(self, now):
        for (asset, end), legs in self.lf.ladders().items():
            if end - now < 60: continue
            above = {round(m.strike, 2): m for m in legs if m.market_type == ABOVE and m.strike is not None}
            for r in legs:
                if r.market_type != RANGE: continue
                lo = above.get(round(r.r_lo, 2)) if r.r_lo is not None and r.r_lo > -1e17 else None
                hi = above.get(round(r.r_hi, 2)) if r.r_hi is not None and r.r_hi < 1e17 else None
                if r.r_kind == "interior" and lo is not None and hi is not None:
                    self.check(now, r, lo, hi, "buy-range"); self.check(now, r, lo, hi, "sell-range")
                elif r.r_kind == "below" and hi is not None:
                    self.check_tail(now, r, hi, "buy"); self.check_tail(now, r, hi, "sell")
                elif r.r_kind == "above" and lo is not None:
                    self.check_equal(now, r, lo)

    def q(self, m, side, now):
        if now - m.book_ts < 60: return m.best_ask if side == "buy" else m.best_bid
        return m.list_ask if side == "buy" else m.list_bid

    def check(self, now, r, lo, hi, mode):
        key = (mode, r.slug)
        if self.repeats[key] >= self.MAX_REPEATS: return
        if mode == "buy-range":   # buy range, sell above(lo), buy above(hi): payoff 0 (or +1 on an exact tie at hi)
            legs = [(r, "buy"), (lo, "sell"), (hi, "buy")]
        else:                     # sell range, buy above(lo), sell above(hi): payoff 0 (-1 on an exact tie at hi)
            legs = [(r, "sell"), (lo, "buy"), (hi, "sell")]
        qs = [self.q(m, s, now) for m, s in legs]
        if any(x is None for x in qs): return
        cash = sum((q if s == "sell" else -q) for (m, s), q in zip(legs, qs)); est = cash - sum(self.fee(q) for q in qs)
        if est < self.MIN_EDGE: return
        self.stats["candidates"] += 1
        if not all(self.live(m, now) for m, _ in legs): return
        qs = [(m.best_ask if s == "buy" else m.best_bid) for m, s in legs]
        if any(x is None for x in qs): return
        cash = sum((q if s == "sell" else -q) for (m, s), q in zip(legs, qs)); edge = cash - sum(self.fee(q) for q in qs)
        if edge < self.MIN_EDGE: self.stats["vanished"] += 1; return
        n = min(self.depth(m, s) for m, s in legs)
        self.execute([(m, s, q, f"range-{mode} {r.r_lo:.0f}-{r.r_hi:.0f}") for (m, s), q in zip(legs, qs)], n, key, mode, edge)

    def check_tail(self, now, r, hi, side):
        """below-tail [.., hi] + above(hi): exactly one pays (both on an exact tie). Buy both under $1, sell both over."""
        key = ("tail-" + side, r.slug)
        if self.repeats[key] >= self.MAX_REPEATS: return
        q1, q2 = self.q(r, side, now), self.q(hi, side, now)
        if q1 is None or q2 is None: return
        est = (1 - q1 - q2) if side == "buy" else (q1 + q2 - 1); est -= self.fee(q1) + self.fee(q2)
        if est < self.MIN_EDGE: return
        self.stats["candidates"] += 1
        if not (self.live(r, now) and self.live(hi, now)): return
        q1, q2 = (r.best_ask, hi.best_ask) if side == "buy" else (r.best_bid, hi.best_bid)
        if q1 is None or q2 is None: return
        edge = ((1 - q1 - q2) if side == "buy" else (q1 + q2 - 1)) - self.fee(q1) - self.fee(q2)
        if edge < self.MIN_EDGE: self.stats["vanished"] += 1; return
        n = min(self.depth(r, side), self.depth(hi, side))
        self.execute([(r, side, q1, f"tail-{side} {r.r_hi:.0f}"), (hi, side, q2, f"tail-{side} above {hi.strike:.0f}")], n, key, "tail-" + side, edge)

    def check_equal(self, now, r, lo):
        """above-tail [lo, ..] is the same contract as above(lo): buy the cheaper, sell the richer."""
        for x, y in ((r, lo), (lo, r)):
            key = ("equal", x.slug, y.slug)
            if self.repeats[key] >= self.MAX_REPEATS: continue
            ax, by = self.q(x, "buy", now), self.q(y, "sell", now)
            if ax is None or by is None: continue
            est = by - ax - self.fee(ax) - self.fee(by)
            if est < self.MIN_EDGE: continue
            self.stats["candidates"] += 1
            if not (self.live(x, now) and self.live(y, now)) or x.best_ask is None or y.best_bid is None: continue
            edge = y.best_bid - x.best_ask - self.fee(x.best_ask) - self.fee(y.best_bid)
            if edge < self.MIN_EDGE: self.stats["vanished"] += 1; continue
            n = min(self.depth(x, "buy"), self.depth(y, "sell"))
            self.execute([(x, "buy", x.best_ask, f"equal-buy {lo.strike:.0f}"), (y, "sell", y.best_bid, f"equal-sell {lo.strike:.0f}")], n, key, "equal", edge)


# ---- 72: settlement lock in the final seconds -----------------------------------------------------------------------
class SettleLock(ArbBase):
    """Bot 72: in the last 12 seconds, once at least 55 of the 60 settlement seconds are published, bound the final
    average: the unpublished seconds can move the index by at most five standard deviations of a random walk from
    its last print (plus a basis allowance). If the lower bound is already at or above K the contract pays $1,
    if the upper bound is below K it pays $0; buy or sell anything priced against that, windows and hourly strikes
    alike. Pure settlement arithmetic, no direction model."""
    name = "settle_lock"; MIN_EDGE = 0.01; MAX_SETS = 20.0; MAX_REPEATS = 3; PERIOD = 1.0; BUDGET = 2

    def run(self, now):
        groups = XArb.contracts(self, now)
        for (asset, T), items in groups.items():
            left = T - now
            if left > 12 or left < 0.5: continue
            from .fair import settle_estimate
            est = settle_estimate(asset, T, self.idx, self.spot, now)
            if est is None: continue
            mean, sd, n, info = est
            if n < 55: continue
            b = 5 * sd + 1e-4 * mean; lo = mean - b; hi = mean + b
            self.stats["groups"] += 1
            quant = 2 * 10 ** (-getattr(items[0][1], "decimals", 2))
            for K, m, src in items:
                key = m.slug
                if self.repeats[key] >= self.MAX_REPEATS: continue
                if lo - quant >= K: want = "buy"
                elif hi + quant < K: want = "sell"
                else: continue
                if not self.live(m, now): continue
                if want == "buy" and m.best_ask is not None:
                    edge = 1 - m.best_ask - self.fee(m.best_ask)
                    if edge < self.MIN_EDGE: continue
                    self.stats["candidates"] += 1
                    self.execute([(m, "buy", m.best_ask, f"lock-buy n{n} K{K:.0f}")], self.depth(m, "buy"), key, "locked-1", edge)
                elif want == "sell" and m.best_bid is not None:
                    edge = m.best_bid - self.fee(m.best_bid)
                    if edge < self.MIN_EDGE: continue
                    self.stats["candidates"] += 1
                    self.execute([(m, "sell", m.best_bid, f"lock-sell n{n} K{K:.0f}")], self.depth(m, "sell"), key, "locked-0", edge)


# ---- 73: tighter ladder arbitrage -----------------------------------------------------------------------------------
class LadderTight(LadderArb):
    """Bot 73: the ladder-consistency arbitrage (31) with a 0.5-cent edge, 20 sets and a 10-second scan."""
    name = "ladder_tight"; MIN_EDGE = 0.005; MAX_SETS = 20.0; SCAN_EVERY = 10


# ---- 74: windows versus range buckets -------------------------------------------------------------------------------
class WindowTile(ArbBase):
    """Bot 74: an Up window (average >= price-to-beat P) and a range bucket entirely below P cannot both pay, so
    their bids cannot sum to more than $1 (sell both). A bucket entirely at or above P pays only if Up pays, so it
    cannot be worth more than Up (buy Up, sell the bucket). Same asset and settlement instant."""
    name = "window_tile"; MIN_EDGE = 0.01; MAX_SETS = 10.0; PERIOD = 5.0; BUDGET = 2

    def run(self, now):
        ladders = {(a, int(e)): legs for (a, e), legs in self.lf.ladders().items()}
        for w in list(self.mf.markets.values()):
            if w.kind != "updown" or w.p2b is None or w.end - now < 30 or w.book_ts < now - 8 or w.best_bid is None or w.best_ask is None: continue
            legs = ladders.get((w.asset, int(w.end)))
            if not legs: continue
            self.stats["groups"] += 1; P = w.p2b
            for r in legs:
                if r.market_type != RANGE or r.r_lo is None or r.r_hi is None: continue
                key = (w.slug, r.slug)
                if self.repeats[key] >= self.MAX_REPEATS: continue
                bid_r = r.best_bid if now - r.book_ts < 60 else r.list_bid
                if bid_r is None: continue
                if r.r_hi < P:            # exclusive with Up: sell both if bids sum over $1 after fees
                    est = bid_r + w.best_bid - 1 - self.fee(bid_r) - self.fee(w.best_bid)
                    if est < self.MIN_EDGE: continue
                    self.stats["candidates"] += 1
                    if not self.live(r, now) or r.best_bid is None: continue
                    edge = r.best_bid + w.best_bid - 1 - self.fee(r.best_bid) - self.fee(w.best_bid)
                    if edge < self.MIN_EDGE: self.stats["vanished"] += 1; continue
                    n = min(self.depth(r, "sell"), self.depth(w, "sell"))
                    self.execute([(w, "sell", w.best_bid, f"tile-sell up {P:.0f}"), (r, "sell", r.best_bid, f"tile-sell {r.r_lo:.0f}-{r.r_hi:.0f}")], n, key, "exclusive", edge)
                elif r.r_lo >= P:         # bucket implies Up: buy Up, sell the bucket
                    est = bid_r - w.best_ask - self.fee(bid_r) - self.fee(w.best_ask)
                    if est < self.MIN_EDGE: continue
                    self.stats["candidates"] += 1
                    if not self.live(r, now) or r.best_bid is None: continue
                    edge = r.best_bid - w.best_ask - self.fee(r.best_bid) - self.fee(w.best_ask)
                    if edge < self.MIN_EDGE: self.stats["vanished"] += 1; continue
                    n = min(self.depth(r, "sell"), self.depth(w, "buy"))
                    self.execute([(w, "buy", w.best_ask, f"tile-buy up {P:.0f}"), (r, "sell", r.best_bid, f"tile-sell {r.r_lo:.0f}-{r.r_hi:.0f}")], n, key, "dominance", edge)


# ---- 75: fast, high-volume cross-window arbitrage -------------------------------------------------------------------
class XArbFast(ArbBase):
    """Bot 75: the cross-window dominance model (64) built for speed and size. Every second it groups the Up/Down
    windows and hourly strikes by coin and settlement instant; any pair within 3 cents of a violation puts both
    books on 1-second polling (strikes are fetched directly), so the gap is seen within a second instead of up to
    six. When the gap clears both fees plus 0.3 cents it walks the two books level by level, taking the full
    visible size at every level pair that still pays, up to 200 sets per pass and the cash on hand, and keeps
    taking on later passes while the gap persists. Takers consume the displayed liquidity, so repeated passes
    never pretend the same contracts are there twice. Unequal fills are unwound at once."""
    name = "xarb_fast"; MIN_EDGE = 0.003; MAX_SETS = 200.0; PERIOD = 1.0; BUDGET = 4; WATCH = 0.03

    def run(self, now):
        groups = XArb.contracts(self, now); uni = self.acct.universe
        for (asset, T), items in groups.items():
            if len(items) < 2: continue
            self.stats["groups"] += 1
            items.sort(key=lambda x: x[0])
            for i, (Ka, a, sa) in enumerate(items):
                for Kb, b, sb in items[i + 1:]:
                    if a.slug == b.slug: continue
                    ask_a = a.best_ask if sa == "live" else (a.best_ask if now - a.book_ts < 60 else a.list_ask)
                    bid_b = b.best_bid if sb == "live" else (b.best_bid if now - b.book_ts < 60 else b.list_bid)
                    if ask_a is None or bid_b is None: continue
                    gap = bid_b - ask_a - self.fee(ask_a) - self.fee(bid_b)
                    if gap < -self.WATCH: continue
                    # near a violation: watch both books every second
                    a.hot_until = b.hot_until = now + 10; self.stats["watching"] += 1
                    for m, src in ((a, sa), (b, sb)):
                        if src == "list" and now - m.book_ts > 1.5:
                            if self.budget <= 0: break
                            self.budget -= 1; self.lf.fetch_book(m, max_age=1.5)
                    if now - a.book_ts > 3 or now - b.book_ts > 3: continue
                    if gap < self.MIN_EDGE: continue
                    self.stats["candidates"] += 1
                    # walk the books: pair level i of a's offers with level j of b's bids while the pair still pays
                    legs_done = 0
                    for _ in range(6):
                        av_a = a.avail("buy", uni); av_b = b.avail("sell", uni)
                        if not av_a or not av_b: break
                        pa, qa = av_a[0]; pb, qb = av_b[0]
                        edge = pb - pa - self.fee(pa) - self.fee(pb)
                        if edge < self.MIN_EDGE: break
                        n = min(qa, qb, self.MAX_SETS, self.acct.affordable_sets([("buy", pa), ("sell", pb)]))
                        n = math.floor(n * 100) / 100
                        if n < 0.01: self.stats["unaffordable"] += 1; break
                        if not self.execute([(a, "buy", pa, f"xf-buy K{Ka:.0f}"), (b, "sell", pb, f"xf-sell K{Kb:.0f}")], n, (a.slug, b.slug), "xarb-fast", edge): break
                        legs_done += 1
                    if legs_done > 1: self.stats["walked_levels"] += legs_done - 1


ARB_BOTS = ("xarb_tight", "xarb_windows", "xarb_maker_bid", "xarb_maker_ask", "touch_arb", "touch_ladder", "range_arb", "settle_lock", "ladder_tight", "window_tile", "xarb_fast",
            "lock_fast", "lock_sweep", "lock_early", "lock_rest", "lock_strikes")


def make_arbs(accounts, engine):
    """One scanner per arbitrage bot, bound to that bot's account in this universe."""
    from .locks import LOCK_BOTS
    out = {k: cls(accounts[k], engine) for k, cls in LOCK_BOTS.items()}
    out.update({
        "xarb_tight": XArbTight(accounts["xarb_tight"], engine.mf, engine.lf),
        "xarb_windows": XArbWindows(accounts["xarb_windows"], engine.mf, engine.lf),
        "xarb_maker_bid": XArbMakerBid(accounts["xarb_maker_bid"], engine),
        "xarb_maker_ask": XArbMakerAsk(accounts["xarb_maker_ask"], engine),
        "touch_arb": TouchArb(accounts["touch_arb"], engine),
        "touch_ladder": TouchLadder(accounts["touch_ladder"], engine),
        "range_arb": RangeArb(accounts["range_arb"], engine),
        "settle_lock": SettleLock(accounts["settle_lock"], engine),
        "ladder_tight": LadderTight(accounts["ladder_tight"], engine.lf),
        "window_tile": WindowTile(accounts["window_tile"], engine),
        "xarb_fast": XArbFast(accounts["xarb_fast"], engine),
    })
    return out
