"""Settlement-lock family (bots 76-80): arbitrage from the settlement arithmetic of the final seconds, independent of
the cross-window dominance model.

Every window and strike settles on the 60-second index average ending at T. Once n of those 60 seconds are
published, the remaining r = 60 - n seconds can move the average only so far: a random walk from the last print
keeps the average of the remaining seconds within k standard deviations of sigma * sqrt(r / 3), plus a basis
allowance. If the lower bound of the final average is already at or above the threshold the contract pays $1, if
the upper bound is below it, $0. Anything priced against that is bought or sold. No direction model is involved:
the bots differ only in how early they act, how much of the book they take, and whether they make or take.
"""
import time, logging, collections, math
from .arbs import ArbBase, XArb
from .ladder import RANGE

log = logging.getLogger("locks")


class LockBase(ArbBase):
    N_MIN = 45; K_SD = 4.0; BASIS = 1e-4; HORIZON_S = 25; MIN_EDGE = 0.005; MAX_SETS = 100.0; PERIOD = 1.0

    def bounds(self, asset, T):
        """(lo, hi, n): bounds of the final 60-second average, or None if too few seconds are published."""
        known, n = self.idx.avg(asset, T - 60, T)
        if known is None or n < self.N_MIN: return None
        its, S = self.idx.last(asset)
        if S is None: return None
        r = max(0, 60 - n); sig = max(self.idx.sigma(asset), 1.5e-4)
        b = (self.K_SD * sig * math.sqrt(r / 3.0) + self.BASIS) * S if r > 0 else 0.0
        return (known * n + r * (S - b)) / 60.0, (known * n + r * (S + b)) / 60.0, n

    def value(self, m, lo, hi):
        """Locked payout of m given the bounds, else None. Up/Down and Above: average >= K; range: both bounds inside."""
        quant = 2 * 10 ** (-getattr(m, "decimals", 2))
        if m.market_type == RANGE:
            rlo = m.r_lo if m.r_lo is not None else -1e18; rhi = m.r_hi if m.r_hi is not None else 1e18
            if lo - quant >= rlo and hi + quant <= rhi: return 1.0
            if hi + quant < rlo or lo - quant > rhi: return 0.0
            return None
        K = m.p2b if m.kind == "updown" else m.strike
        if K is None: return None
        if lo - quant >= K: return 1.0
        if hi + quant < K: return 0.0
        return None

    def windows(self, now):
        for m in list(self.mf.markets.values()):
            if m.kind == "updown" and m.p2b is not None and 0.5 <= m.end - now <= self.HORIZON_S and m.book_ts > now - 3:
                yield m

    def take_locked(self, m, v, now, sweep=False):
        """Buy a $1 contract under 1 - fee - edge, sell a $0 contract above fee + edge; best level or every level."""
        uni = self.acct.universe; key = m.slug
        if self.repeats[key] >= self.MAX_REPEATS: return
        side = "buy" if v >= 0.5 else "sell"
        levels = m.avail(side, uni)
        if not levels: return
        for px, q in (levels if sweep else levels[:1]):
            edge = (1 - px - self.fee(px)) if side == "buy" else (px - self.fee(px))
            if edge < self.MIN_EDGE: break
            self.stats["candidates"] += 1
            n = min(q, self.MAX_SETS, self.acct.affordable_sets([(side, px)]))
            n = math.floor(n * 100) / 100
            if n < 0.01: self.stats["unaffordable"] += 1; break
            if not self.execute([(m, side, px, f"lock-{side} v{v:.0f}")], n, key, "locked-%d" % v, edge): break


# ---- 76 -----------------------------------------------------------------------------------------------------------
class LockFast(LockBase):
    """Bot 76: windows only, last 25 s, 45+ seconds published, 4-sd bound, best level each second, up to 100."""
    name = "lock_fast"; N_MIN = 45; K_SD = 4.0; HORIZON_S = 25; MIN_EDGE = 0.005; MAX_SETS = 100.0; MAX_REPEATS = 10
    def run(self, now):
        for m in self.windows(now):
            b = self.bounds(m.asset, int(m.end))
            if b is None: continue
            self.stats["bounded"] += 1; v = self.value(m, b[0], b[1])
            if v is None: continue
            self.take_locked(m, v, now)


# ---- 77 -----------------------------------------------------------------------------------------------------------
class LockSweep(LockBase):
    """Bot 77: maximum volume. Windows, last 15 s, 50+ seconds published, 5-sd bound; walks every book level priced
    against the locked outcome, up to 300 contracts per pass, repeating each second while anything is left."""
    name = "lock_sweep"; N_MIN = 50; K_SD = 5.0; HORIZON_S = 15; MIN_EDGE = 0.003; MAX_SETS = 300.0; MAX_REPEATS = 30
    def run(self, now):
        for m in self.windows(now):
            b = self.bounds(m.asset, int(m.end))
            if b is None: continue
            self.stats["bounded"] += 1; v = self.value(m, b[0], b[1])
            if v is None: continue
            self.take_locked(m, v, now, sweep=True)


# ---- 78 -----------------------------------------------------------------------------------------------------------
class LockEarly(LockBase):
    """Bot 78: acts earlier, from 25 published seconds (about 35 s before the end), with the same 4-sd bound plus a
    2-bp basis allowance and a 1-cent edge; up to 30 contracts per window. More windows qualify, more risk that a
    late jump breaks the bound. Puts its windows on 1-second polling from 40 s out."""
    name = "lock_early"; N_MIN = 25; K_SD = 4.0; BASIS = 2e-4; HORIZON_S = 40; MIN_EDGE = 0.01; MAX_SETS = 30.0; MAX_REPEATS = 3
    def run(self, now):
        for m in self.windows(now):
            m.hot_until = now + 5
            b = self.bounds(m.asset, int(m.end))
            if b is None: continue
            self.stats["bounded"] += 1; v = self.value(m, b[0], b[1])
            if v is None: continue
            self.take_locked(m, v, now)


# ---- 79 -----------------------------------------------------------------------------------------------------------
class LockRest(LockBase):
    """Bot 79: the maker version. Once a window is locked (45+ seconds, 4 sd) it rests a bid one tick above the best
    bid (capped at $0.97) for a $1 contract, or an offer one tick below the best ask (floored at $0.03) for a $0
    contract, 50 contracts, fee-free when hit; the order is pulled the moment the bound no longer locks."""
    name = "lock_rest"; N_MIN = 45; K_SD = 4.0; HORIZON_S = 30; MAX_SETS = 50.0
    def __init__(self, account, engine): super().__init__(account, engine); self.rest = {}
    def run(self, now):
        acct = self.acct; live = set()
        for m in self.windows(now):
            b = self.bounds(m.asset, int(m.end)); v = self.value(m, b[0], b[1]) if b else None
            st = self.rest.get(m.slug)
            if v is None:
                if st: acct.cancel(st["o"].id); del self.rest[m.slug]; self.stats["pulled"] += 1
                continue
            self.stats["bounded"] += 1; live.add(m.slug); tick = m.tick
            if m.best_bid is None or m.best_ask is None: continue
            if v >= 0.5: side, p = "buy", min(0.97, round(m.best_bid + tick, 2))
            else: side, p = "sell", max(0.03, round(m.best_ask - tick, 2))
            if (side == "buy" and p >= m.best_ask) or (side == "sell" and p <= m.best_bid): continue   # would cross: the takers' job
            if st and st["o"].remaining > 1e-9 and abs(st["o"].price - p) < 1e-9: continue
            if st: acct.cancel(st["o"].id)
            n = round(acct.size_for(p, self.MAX_SETS), 2)
            o = acct.place(m, side, p, n, post_only=True, tag=f"lockrest-{side} v{v:.0f}", fixed=True)
            if o is None: continue
            self.rest[m.slug] = {"o": o}; self.stats["rested"] += 1
        for slug in list(self.rest):
            if slug not in live and slug not in self.mf.markets: del self.rest[slug]
        self.stats["resting"] = sum(1 for st in self.rest.values() if st["o"].remaining > 1e-9)


# ---- 80 -----------------------------------------------------------------------------------------------------------
class LockStrikes(LockBase):
    """Bot 80: the widest universe. At every settlement instant it also covers the hourly strikes and range buckets
    expiring there (fetched directly, 8 per second in the last 30 s) and the 1-hour windows, walking levels; 45+
    seconds published, 4-sd bound, up to 100 per contract."""
    name = "lock_strikes"; N_MIN = 45; K_SD = 4.0; HORIZON_S = 30; MIN_EDGE = 0.005; MAX_SETS = 100.0; MAX_REPEATS = 10; BUDGET = 8
    def run(self, now):
        groups = XArb.contracts(self, now)
        ladders = {(a, int(e)): legs for (a, e), legs in self.lf.ladders().items()}
        for (asset, T), items in groups.items():
            if not (0.5 <= T - now <= self.HORIZON_S): continue
            b = self.bounds(asset, T)
            if b is None: continue
            self.stats["bounded"] += 1
            legs = [m for _, m, _ in items] + [m for m in ladders.get((asset, T), []) if m.market_type == RANGE]
            for m in legs:
                v = self.value(m, b[0], b[1])
                if v is None: continue
                if m.kind != "updown":
                    if now - m.book_ts > 1.5:
                        if self.budget <= 0: self.stats["budget_exhausted"] += 1; continue
                        self.budget -= 1; self.lf.fetch_book(m, max_age=1.5)
                    if now - m.book_ts > 3: continue
                self.take_locked(m, v, now, sweep=True)


LOCK_BOTS = {"lock_fast": LockFast, "lock_sweep": LockSweep, "lock_early": LockEarly, "lock_rest": LockRest, "lock_strikes": LockStrikes}
