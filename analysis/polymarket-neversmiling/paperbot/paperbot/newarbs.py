"""New arbitrage bots for the tryout (user, 11-13 21:15Z): the ten ideas shown to the user, each on its own $1,000
account. Names start with "new_arb_". They join the builder source "new" (paperbot/newbots.py), so they size their
own orders, keep their own markets and wait WARMUP seconds after a start.

Two of the ten changed when they were built, and the user was told:
  2 (butterfly) is not built. For contracts that pay $1 above a strike, the middle of three strikes may be worth more
    than the average of its neighbours (that bound holds for options, not for these), so it is not an arbitrage.
  8 (two-source mismatch) cannot watch the official benchmark live: the only public source is a page read every 15
    minutes. It is built on the two prices the engine does have every second: the settlement the published index
    points to, and Coinbase's last price. When they are on opposite sides of the line late in a window, it bets the
    index's side, because the index average decides.
1 (full set of buckets) is the bucket part of the existing ladder bot (ladder.LadderArb), run here on its own.

Locks (cannot lose if every leg fills): 1, 3, 4, 5. Rule edges (not locks): 6, 7, 8, 9, 10.
"""
import math, time, logging, collections
import numpy as np
from .newbots import _New, Picker, Quoter, STAKE, WARMUP, OPEN, fee
from .arbs import ArbBase
from .ladder import LadderArb, leg_fee, ABOVE, RANGE
from .fair import settle_estimate, phi

log = logging.getLogger("newarbs")


class _Scan(_New):
    """A whole-book scanner run as a tryout bot: it scans at its own pace whichever market the engine is ticking."""
    def __init__(self, account, name, scanner):
        super().__init__(account, name); self.sc = scanner
    def on_tick(self, market, ctx, now):
        if now - self.born >= WARMUP: self.sc.scan(now)
    def snapshot(self): return self.sc.snapshot()


# ---- 1: the full set of buckets ---------------------------------------------------------------------------------------
class _BucketsOnly(LadderArb):
    """The ladder bot's bucket check alone: every price range of an hour bought when together they cost under $1
    after fees (or sold when they fetch more), on live books, thinnest leg first."""
    def try_pair(self, *a, **k): pass
    def try_complement(self, *a, **k): pass


# ---- 3: after the bell ------------------------------------------------------------------------------------------------
class AfterBell(ArbBase):
    """The window's result is fixed once the index value for its last second is published. If the book is still open
    and shows the winning side under MAX_PRICE, buy it. Only on a book that updated after that value was known."""
    name = "after_bell"; PERIOD = 0.5; MAX_PRICE = 0.97
    def __init__(self, account, engine): super().__init__(account, engine); self.done = set()
    def run(self, now):
        for m in list(self.mf.markets.values()):
            if m.kind != "updown" or m.p2b is None or m.slug in self.done or not (m.end - 1 <= now <= m.end + 60): continue
            v = self.idx.value_at(m.asset, int(m.end) - 1)
            if v is None: continue
            self.stats["results_known"] += 1
            if m.state not in OPEN or m.book_ts < m.end - 1 or now - m.book_ts > 3 or m.best_bid is None or m.best_ask is None: continue
            up = round(v, m.decimals) >= m.p2b; price = m.best_ask if up else 1 - m.best_bid
            if price > self.MAX_PRICE: continue
            n = round(STAKE * self.acct.bank() / price, 2)
            o = self.acct.take(m, "buy" if up else "sell", n, limit=m.best_ask if up else m.best_bid, tag=f"after-bell {'up' if up else 'down'} {price:.2f}", fixed=True)
            if o and o.filled > 0: self.done.add(m.slug); self.stats["trades"] += 1; log.info("after_bell %s %s @%.2f x %.2f", m.slug, "up" if up else "down", price, o.filled)


# ---- 4: already touched -----------------------------------------------------------------------------------------------
class TouchedSweep(ArbBase):
    """A 'reach X' market has won once the index is at or beyond X (a 'dip to X' once at or below). The published
    index is a 60-second average, so when it is MARGIN beyond X the raw price has certainly traded there. Buy any
    such market still offered at MAX_PRICE or less."""
    name = "touched_sweep"; PERIOD = 5.0; BUDGET = 1; MAX_PRICE = 0.97; MARGIN = 5e-4
    def __init__(self, account, engine): super().__init__(account, engine); self.done = set()
    def run(self, now):
        if now - getattr(self.lf, "last_touch_discover", 0) > 600:
            try: self.lf.discover_touch()
            except Exception as e: log.warning("touched_sweep discover: %s", e)
        for m in list(getattr(self.lf, "touch_legs", {}).values()):
            if m.closed or m.slug in self.done or m.strike is None or not m.direction or m.end - now < 60: continue
            its, v = self.idx.last(m.asset)
            if v is None or now - its > 8: continue
            hit = v >= m.strike * (1 + self.MARGIN) if m.direction == "up" else v <= m.strike * (1 - self.MARGIN)
            if not hit: continue
            self.stats["touched"] += 1
            if not self.live(m, now) or m.best_ask is None or m.best_ask > self.MAX_PRICE: continue
            n = round(STAKE * self.acct.bank() / m.best_ask, 2)
            o = self.acct.take(m, "buy", n, limit=m.best_ask, tag=f"touched {m.direction} {m.strike:g} index {v:g}", fixed=True)
            if o and o.filled > 0: self.done.add(m.slug); self.stats["trades"] += 1; log.info("touched_sweep %s @%.2f x %.2f", m.slug, m.best_ask, o.filled)


# ---- 5: the puzzle solver ---------------------------------------------------------------------------------------------
class Solver(ArbBase):
    """Every contract that settles on one number at one instant (the Up/Down windows, the hourly strikes and the price
    ranges of one coin): try every basket of up to three orders, bought or sold one contract each, and take the one
    whose worst-case profit over every possible settlement price is above MIN_EDGE after fees. The existing bots only
    check fixed pairs and the bucket set. Strike and bucket quotes come from the listing for the search and are checked
    on their live books before any order."""
    name = "solver"; PERIOD = 10.0; BUDGET = 4; MIN_EDGE = 0.01; MAX_SETS = 10.0; MAX_LEGS = 24; MAX_REPEATS = 3

    def group(self, now):
        """The (asset, end) groups with at least one strike or bucket, soonest first, one per scan in turn."""
        g = collections.defaultdict(list)
        for m in list(self.lf.legs.values()):
            if 60 < m.end - now < 3600 and (m.strike is not None or m.market_type == RANGE): g[(m.asset, int(m.end))].append(m)
        for m in list(self.mf.markets.values()):
            if m.kind == "updown" and m.p2b is not None and (m.asset, int(m.end)) in g and now - m.book_ts < 5: g[(m.asset, int(m.end))].append(m)
        keys = sorted(g, key=lambda k: (k[1], k[0]))
        if not keys: return None, []
        self._turn = (getattr(self, "_turn", -1) + 1) % len(keys)
        return keys[self._turn], g[keys[self._turn]]

    @staticmethod
    def pays(m, s):
        """1 where contract m pays at settlement price s (an array)."""
        if m.kind == "updown": return (s >= m.p2b - 1e-9).astype(float)
        if m.market_type == RANGE: return ((s >= (m.r_lo if m.r_lo is not None else -1e18) - 1e-9) & (s <= (m.r_hi if m.r_hi is not None else 1e18) + 1e-9)).astype(float)
        return (s >= m.strike - 1e-9).astype(float)

    @staticmethod
    def quote(m, now):
        live = m.kind == "updown" or now - m.book_ts < 3
        return (m.best_bid, m.best_ask) if live else (m.list_bid, m.list_ask)

    def best(self, ms, now):
        """(worst-case profit, [(market, side, price)]) of the best basket of up to three legs."""
        q = 10 ** (-min(getattr(m, "decimals", 2) for m in ms))
        cuts = sorted({b for m in ms for b in ((m.p2b,) if m.kind == "updown" else (m.r_lo, m.r_hi) if m.market_type == RANGE else (m.strike,)) if b is not None and abs(b) < 1e17})
        if not cuts: return -1.0, []
        s = np.array(sorted({x for b in cuts for x in (b - q, b, b + q)})); th = self.acct.theta
        legs = []; rows = []
        for m in ms:
            bid, ask = self.quote(m, now); pay = self.pays(m, s)
            if ask is not None and 0.0 < ask < 1.0: legs.append((m, "buy", ask)); rows.append(pay - ask - leg_fee(th, ask))
            if bid is not None and 0.0 < bid < 1.0: legs.append((m, "sell", bid)); rows.append(bid - pay - leg_fee(th, bid))
        legs.append(None); rows.append(np.zeros(len(s)))                      # "no order", so pairs and singles count too
        P = np.array(rows); n = len(legs); pair = P[:, None, :] + P[None, :, :]; best = (-1.0, None)
        for i in range(n):
            worst = (P[i][None, None, :] + pair).min(axis=2); j, k = np.unravel_index(int(worst.argmax()), worst.shape)
            if worst[j, k] > best[0]: best = (float(worst[j, k]), (i, int(j), int(k)))
        if best[1] is None: return -1.0, []
        chosen = [legs[i] for i in sorted(set(best[1])) if legs[i] is not None]
        if len({m.slug for m, _, _ in chosen}) != len(chosen): return -1.0, []     # the same contract bought and sold
        return best[0], chosen

    def run(self, now):
        if now - self.lf.last_discover > 120:
            try: self.lf.discover()
            except Exception as e: log.warning("solver discover: %s", e)
        key, ms = self.group(now)
        if key is None or len(ms) < 3: return
        its, v = self.idx.last(key[0])
        if v is not None:                                                      # the contracts nearest the price carry the gaps
            line = lambda m: m.p2b if m.kind == "updown" else m.strike if m.strike is not None else (m.r_lo if m.r_lo is not None and abs(m.r_lo) < 1e17 else m.r_hi)
            ms = sorted(ms, key=lambda m: abs((line(m) or v) - v))[:self.MAX_LEGS]
        self.stats["groups"] += 1
        edge, chosen = self.best(ms, now)
        if edge > self.stats.get("best_gap_c", -99): self.stats["best_gap_c"] = round(edge * 100, 2)
        if edge < self.MIN_EDGE or not chosen: return
        self.stats["candidates"] += 1
        if not all(self.live(m, now) for m, _, _ in chosen): return
        edge, live = self.best([m for m, _, _ in chosen], now)                # the same contracts on their live books
        if edge < self.MIN_EDGE or len(live) != len(chosen): self.stats["vanished_on_live_book"] += 1; return
        k = tuple(sorted(m.slug for m, _, _ in live))
        if self.repeats[k] >= self.MAX_REPEATS: return
        n = min([self.MAX_SETS] + [self.depth(m, side) for m, side, _ in live])
        self.execute([(m, side, px, f"solver {side} {i + 1}/{len(live)}") for i, (m, side, px) in enumerate(live)], n, k, f"basket of {len(live)}", edge)


# ---- 6: a tie goes to Up ----------------------------------------------------------------------------------------------
class TieUp(Picker):
    """The rule is 'at or above', so an exact tie pays Up. The published price moves in steps (XRP's are large next to
    its usual move), so late in a window with the price on the line a tie has a real chance. Buy Up when that chance,
    which the smooth model splits evenly, makes Up worth EDGE more than it costs."""
    TIE_MIN = 0.04
    def signal(self, market, ctx, now, p, det):
        sd = det.get("sd") or 0.0
        if sd <= 0: return None
        tie = min(0.5, 10 ** (-market.decimals) / (sd * math.sqrt(2 * math.pi)) * math.exp(-det["z"] ** 2 / 2))
        if tie < self.TIE_MIN: return None
        ask = market.best_ask
        return True if p + tie / 2 - ask - fee(self.acct.theta, ask) >= 0.01 else None


# ---- 7: a head start on the next window -------------------------------------------------------------------------------
class HeadStart(_New):
    """The next window's price to beat is this window's settlement: an average of the last 60 seconds, which lags a
    moving price. In the last LAST seconds that average is nearly known. If Coinbase's price now sits far enough from
    it that the next window is at least P_MIN likely to finish on that side, and the next window's quote has not
    priced it, buy that side of the next window. One bet per window."""
    LAST = 15; P_MIN = 0.60; EDGE = 0.05
    def __init__(self, account, name, engine=None, **kw): super().__init__(account, name, **kw); self.engine = engine; self.done = set()
    def on_tick(self, market, ctx, now):
        left = market.remaining(now)
        if now - self.born < WARMUP or market.asset not in self.assets or market.dur not in self.durs or not (2 <= left <= self.LAST): return
        nxt = next((m for m in list(self.engine.mf.markets.values()) if m.asset == market.asset and m.kind == "updown" and m.dur == market.dur and abs(m.start - market.end) < 1), None)
        if nxt is None or nxt.slug in self.done or nxt.slug in self.acct.positions: return
        if nxt.state not in OPEN or nxt.best_bid is None or nxt.best_ask is None or now - nxt.book_ts > 10: return
        idx, spot = ctx["index"], ctx["spot"]; est = settle_estimate(market.asset, market.end, idx, spot, now)
        cb, ts = spot.last(market.asset); its, rm = idx.last(market.asset)
        if est is None or not cb or not ts or now - ts > 5 or rm is None or now - its > 8 or est[2] < 40: return
        m60 = spot.mean(market.asset, int(its) - 59, int(its) + 1)
        if not m60: return
        raw = rm + (cb - m60)                                              # the raw index level now, from Coinbase's move
        sd = spot.sigma(market.asset) * raw * math.sqrt(nxt.dur - 20)
        if sd <= 0: return
        p = phi((raw - est[0]) / sd); th = self.acct.theta
        if p >= self.P_MIN and p - nxt.best_ask - fee(th, nxt.best_ask) >= self.EDGE: up = True
        elif 1 - p >= self.P_MIN and (1 - p) - (1 - nxt.best_bid) - fee(th, nxt.best_bid) >= self.EDGE: up = False
        else: return
        if self.bet(nxt, up, STAKE * self.acct.bank(), f"{self.name} {'up' if up else 'down'} p {p if up else 1 - p:.2f}"): self.done.add(nxt.slug)


# ---- 8: the index and Coinbase disagree -------------------------------------------------------------------------------
class IndexDecides(Picker):
    """Late in a window the settlement the index points to and Coinbase's last price are on opposite sides of the
    line. The index's 60-second average decides, so bet its side when the model is at least Z_MIN sure."""
    Z_MIN = 1.0
    def signal(self, market, ctx, now, p, det):
        if abs(det["z"]) < self.Z_MIN or market.p2b is None: return None
        its, rm = ctx["index"].last(market.asset); cb, _ = ctx["spot"].last(market.asset)
        m60 = ctx["spot"].mean(market.asset, int(its) - 59, int(its) + 1)
        if not m60 or not cb or rm is None: return None
        raw_up = rm + (cb - m60) >= market.p2b; up = det["z"] > 0
        return up if raw_up != up else None


# ---- 10: quotes left over from before a gap ---------------------------------------------------------------------------
class ReopenSweep(Picker):
    """A window's book went GAP seconds without an update (a pause or a dropped stream) and has just come back. For
    HOT seconds its quotes may be left over from before the gap: take one the model puts EDGE away from fair."""
    GAP = 12.0; HOT = 5.0
    def __init__(self, *a, **kw): super().__init__(*a, **kw); self._last = {}; self._hot = {}
    def on_tick(self, market, ctx, now):
        if len(self._last) > 80:
            for k in [k for k, t in self._last.items() if now - t > 900]: self._last.pop(k, None); self._hot.pop(k, None)      # closed windows
        last = self._last.get(market.slug); self._last[market.slug] = market.book_ts      # the book's own update times
        if last is not None and market.book_ts - last > self.GAP: self._hot[market.slug] = now + self.HOT
        super().on_tick(market, ctx, now)
    def gate(self, market, ctx, now, det): return now < self._hot.get(market.slug, 0.0)


BOTS = {
    "new_arb_01_full_set_of_buckets": ("NEW ARB 1: buys every price range of an hour when the whole set costs under $1 after fees (a lock)",
                                       lambda a, e: _Scan(a, "new_arb_01_full_set_of_buckets", _BucketsOnly(a, e.lf))),
    "new_arb_03_after_the_bell": ("NEW ARB 3: buys the known winner if the book is still open after the window's last index value is published (a lock)",
                                  lambda a, e: _Scan(a, "new_arb_03_after_the_bell", AfterBell(a, e))),
    "new_arb_04_already_touched_sweep": ("NEW ARB 4: buys a 'reach this price' market that has already been reached and still sells under 97c (a lock)",
                                         lambda a, e: _Scan(a, "new_arb_04_already_touched_sweep", TouchedSweep(a, e))),
    "new_arb_05_puzzle_solver": ("NEW ARB 5: tries every basket of up to three windows, strikes and ranges that settle on one number, and takes one that cannot lose",
                                 lambda a, e: _Scan(a, "new_arb_05_puzzle_solver", Solver(a, e))),
    "new_arb_06_tie_goes_to_up": ("NEW ARB 6: buys Up late in a window when the price sits on the line and a tie, which pays Up, is likely enough",
                                  lambda a, e: TieUp(a, "new_arb_06_tie_goes_to_up", t_hi=30, t_lo=3, band=(0.30, 0.75), edge=None)),
    "new_arb_07_head_start_on_next_window": ("NEW ARB 7: in a window's last 15 s, buys the side of the next window that its nearly known starting line favours",
                                             lambda a, e: HeadStart(a, "new_arb_07_head_start_on_next_window", engine=e)),
    "new_arb_08_index_decides": ("NEW ARB 8: late in a window, when the index and Coinbase are on opposite sides of the line, bets the index's side",
                                 lambda a, e: IndexDecides(a, "new_arb_08_index_decides", t_hi=40, t_lo=5, band=(0.30, 0.85), edge=0.02)),
    "new_arb_09_fat_finger_catcher": ("NEW ARB 9: rests a bid 25c under and an offer 25c over fair value to catch mistaken orders",
                                      lambda a, e: Quoter(a, "new_arb_09_fat_finger_catcher", margin=0.25, el=30, left=30)),
    "new_arb_10_reopen_sweep": ("NEW ARB 10: after a book goes quiet for 12 s and comes back, takes a quote left 6c or more from fair value",
                                lambda a, e: ReopenSweep(a, "new_arb_10_reopen_sweep", t_hi=285, t_lo=10, band=(0.05, 0.90), edge=0.06)),
}
LABELS = {k: v[0] for k, v in BOTS.items()}


def build_new_arb_registry(engine=None):
    return {k: (lambda acct, f=f: f(acct, engine)) for k, (_, f) in BOTS.items()}
