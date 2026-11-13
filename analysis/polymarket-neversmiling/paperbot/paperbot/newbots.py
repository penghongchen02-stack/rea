"""New tryout bots (user, 11-13 19:00Z): twenty ideas that differ from what already runs, and one volume bot, each on
its own $1,000 tryout account. Every name starts with "new_" so they stand out on the tryout page.

What the dashboards said before these were written: taker fees eat the active bots (0.095 x p x (1 - p) a contract:
2.4c at 50c, so a 50c bet must win 52.4% of the time), picking a direction has been a coin flip, and what held up were
bots that rest their orders or trade little. So the ideas avoid the fee (resting orders, free on Polymarket US), trade
only when the odds are better, use information the other bots ignore, or manage risk.

Two kinds of bot:
  Quoter  rests post-only orders around the model's fair price (fair.p_up) and holds what fills to the close.
  Picker  makes at most one bet a window, in a time band, on the side its signal names, and holds it to the close.
Sizes are a share of the settled bank (STAKE a bet, at most CAP in one window). All four coins, 5-minute windows,
unless a bot says otherwise. No bot trades in its first WARMUP seconds or on stale prices: on 11-13 18:34Z bots that
traded two seconds after an engine restart lost about $420.

Paper fills of resting orders are easier than real ones (the queue model is generous), so the quoters' results are
the best case.
"""
import time, collections, statistics
from .strategies import Base
from .fair import p_up

STAKE = 0.01; CAP = 0.03; WARMUP = 60.0; OPEN = (None, "MARKET_STATE_OPEN")
COINS = ("btc", "eth", "sol", "xrp")


def fee(theta, p): return theta * p * (1 - p)


class _New(Base):
    """Shared plumbing: fresh-data checks, the model's fair price, trends, and sizing."""
    def __init__(self, account, name, assets=COINS, durs=(300,)):
        super().__init__(account)
        self.name = name; self.assets = set(assets); self.durs = set(durs); self.born = time.time()

    def ready(self, market, now):
        return (now - self.born >= WARMUP and market.asset in self.assets and market.dur in self.durs and market.state in OPEN
                and market.best_bid is not None and market.best_ask is not None and market.best_ask > market.best_bid)

    def fair(self, market, ctx, now):
        """(P(Up), details) when the index and Coinbase prices are fresh, else (None, None)."""
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None or det.get("index_age", 99) > 8: return None, None
        px, ts = ctx["spot"].last(market.asset)
        if not px or not ts or now - ts > 10: return None, None
        return p, det

    def trend(self, ctx, asset, now, secs):
        """Coinbase price now minus its price `secs` ago, as a fraction; None when either is unknown."""
        px, ts = ctx["spot"].last(asset); old = ctx["spot"].sec(asset, now - secs)
        if not px or not old or not ts or now - ts > 10: return None
        return px / old - 1.0

    def held(self, slug):
        """Dollars at risk in this window."""
        p = self.acct.positions.get(slug)
        if not p or p.closed or abs(p.qty) < 1e-9: return 0.0
        return abs(p.qty) * (p.avg if p.qty > 0 else 1 - p.avg)

    def bet(self, market, up, dollars, tag):
        """Take `dollars` of the Up or Down side at the touch; True if anything filled."""
        price = market.best_ask if up else 1 - market.best_bid
        qty = round(min(dollars, max(0.0, CAP * self.acct.bank() - self.held(market.slug))) / max(price, 0.01), 2)
        if qty < max(market.min_qty, 0.01): return False
        o = self.acct.take(market, "buy" if up else "sell", qty, limit=market.best_ask if up else market.best_bid, tag=tag, fixed=True)
        return bool(o and o.filled > 0)


# ---- resting-order bots ---------------------------------------------------------------------------------------------
class Quoter(_New):
    """Rests post-only orders around the fair price. margin: cents under (over) fair for the bid (ask); el: seconds
    into the window it starts; stop_el: seconds into the window it stops (None = never); left: stops with this many
    seconds left; skew: how far a full inventory shifts both quotes; one: only the single better side; queue: rest at
    the level with the least size waiting instead of the best allowed price; trend_take: also take a small bet when
    the model's edge is at least this and the 3-minute trend agrees (the volume bot)."""
    def __init__(self, account, name, margin=0.03, el=20, stop_el=None, left=45, skew=0.0, one=False, queue=False, trend_take=None, **kw):
        super().__init__(account, name, **kw)
        self.margin = margin; self.el = el; self.stop_el = stop_el; self.left = left; self.skew = skew; self.one = one; self.queue = queue
        self.trend_take = trend_take

    def level(self, levels, best, limit, side):
        """The price to rest at: the best price not beyond `limit`, or (queue) the least crowded of the first three."""
        ok = [(px, q) for px, q in levels[:6] if (px <= limit if side == "buy" else px >= limit)][:3]
        if not ok: return round(limit, 2)
        return min(ok, key=lambda l: l[1])[0] if self.queue else ok[0][0]

    def on_tick(self, market, ctx, now):
        slug = market.slug; a = self.acct
        if market.asset not in self.assets or market.dur not in self.durs: return
        el = now - market.start; left = market.remaining(now)
        st = self.mem.setdefault(slug, {"q": None, "took": False})
        active = self.ready(market, now) and el >= self.el and left >= self.left and (self.stop_el is None or el <= self.stop_el)
        p = None
        if active: p, det = self.fair(market, ctx, now)
        if p is None:
            if st["q"] is not None: a.cancel_all(slug); st["q"] = None
            return
        bb, ba = market.best_bid, market.best_ask; bank = a.bank(); room = CAP * bank - self.held(slug)
        pos = a.positions.get(slug); inv = (pos.qty * (pos.avg if pos.qty > 0 else 1 - pos.avg) / (CAP * bank)) if pos and not pos.closed and bank > 0 else 0.0
        mid = p - self.skew * inv                                     # a long inventory lowers both quotes
        bid = min(self.level(market.bids, bb, mid - self.margin, "buy"), ba - 0.01)
        ask = max(self.level(market.offers, ba, mid + self.margin, "sell"), bb + 0.01)
        want_bid = 0.02 <= bid <= 0.98 and (room > 1 or inv < 0); want_ask = 0.02 <= ask <= 0.98 and (room > 1 or inv > 0)
        if self.one:                                                  # only the side whose touch is further from fair
            if (p - bb) >= (ba - p): want_ask = False
            else: want_bid = False
        q = (round(bid, 2) if want_bid else None, round(ask, 2) if want_ask else None)
        if q != st["q"]:                                              # requote only when a price changes (keeps the queue place)
            a.cancel_all(slug); st["q"] = q; d = STAKE * bank
            if q[0] is not None: a.place(market, "buy", q[0], round(d / q[0], 2), post_only=True, tag=f"{self.name} bid fair {p:.2f}", fixed=True)
            if q[1] is not None: a.place(market, "sell", q[1], round(d / (1 - q[1]), 2), post_only=True, tag=f"{self.name} ask fair {p:.2f}", fixed=True)
        if self.trend_take is not None and not st["took"] and 60 <= left <= 240:
            th = a.theta; e_up = p - ba - fee(th, ba); e_dn = (1 - p) - (1 - bb) - fee(th, bb); t = self.trend(ctx, market.asset, now, 180)
            if t is not None:
                if e_up >= self.trend_take and t > 0: st["took"] = self.bet(market, True, 0.5 * STAKE * bank, f"{self.name} take up edge {e_up:.2f}")
                elif e_dn >= self.trend_take and t < 0: st["took"] = self.bet(market, False, 0.5 * STAKE * bank, f"{self.name} take down edge {e_dn:.2f}")


# ---- one-bet-a-window bots ------------------------------------------------------------------------------------------
class Picker(_New):
    """One bet a window, held to the close. t_hi/t_lo: seconds left between which it may bet; band: what the side may
    cost; edge: the model's edge after the fee that the side must have (None = the model is not asked). signal(market,
    ctx, now, p, det) returns True (Up), False (Down) or None (no bet); without a signal the side is the model's.
    gate(market, ctx, now, det) returning False skips the tick; scale(now) multiplies the stake."""
    def __init__(self, account, name, t_hi=240, t_lo=60, band=(0.30, 0.70), edge=0.03, engine=None, **kw):
        super().__init__(account, name, **kw)
        self.t_hi = t_hi; self.t_lo = t_lo; self.band = band; self.edge = edge; self.engine = engine

    def signal(self, market, ctx, now, p, det): return None if p is None else p >= 0.5
    def gate(self, market, ctx, now, det): return True
    def scale(self, now): return 1.0
    def after(self, market, ctx, now): pass                     # called every tick while a bet is open

    def on_tick(self, market, ctx, now):
        if market.asset not in self.assets or market.dur not in self.durs: return
        slug = market.slug; st = self.mem.setdefault(slug, {"done": False})
        if slug in self.acct.positions and not st["done"]: st["done"] = True      # a bet made before a restart counts
        if st["done"]: self.after(market, ctx, now); return
        left = market.remaining(now)
        if not self.ready(market, now) or not (self.t_lo <= left <= self.t_hi): return
        p, det = self.fair(market, ctx, now)
        if p is None or not self.gate(market, ctx, now, det): return
        up = self.signal(market, ctx, now, p, det)
        if up is None: return
        bb, ba = market.best_bid, market.best_ask; price = ba if up else 1 - bb
        if not (self.band[0] <= price <= self.band[1]): return
        if self.edge is not None:
            e = (p if up else 1 - p) - price - fee(self.acct.theta, price)
            if e < self.edge: return
        m = self.scale(now)
        if m <= 0: return
        if self.bet(market, up, STAKE * self.acct.bank() * m, f"{self.name} {'up' if up else 'down'} {price:.2f} {left:.0f}s"):
            st["done"] = True; st["up"] = up; st["price"] = price; self.placed(market, now)

    def placed(self, market, now): pass

    def day(self, now, since=None):
        """Settled P/L after fees since `since` (default: the start of the UTC day)."""
        t0 = since if since is not None else now - now % 86400
        return sum(r.get("pnl_total", 0.0) - r.get("fees", 0.0) for r in self.acct.settlements if r.get("ts", 0) >= t0)


class StrongTrend(Picker):
    """6: the 1-, 3- and 10-minute trends all point the same way."""
    def signal(self, market, ctx, now, p, det):
        ts = [self.trend(ctx, market.asset, now, s) for s in (60, 180, 600)]
        if any(t is None for t in ts): return None
        if all(t > 0 for t in ts): return True
        if all(t < 0 for t in ts): return False
        return None


class BigMove(Picker):
    """7: the coin has moved more than usual in this window (the model's z between Z_LO and Z_HI): bet with the move."""
    Z_LO = 0.8; Z_HI = 1.6
    def signal(self, market, ctx, now, p, det):
        z = det["z"]
        return (z > 0) if self.Z_LO <= abs(z) <= self.Z_HI else None


class QuietSkip(Picker):
    """8: the model's side, but not while the coin's volatility is under QUIET of its recent median."""
    QUIET = 0.7
    def __init__(self, *a, **kw): super().__init__(*a, **kw); self._sig = collections.defaultdict(lambda: collections.deque(maxlen=900))
    def gate(self, market, ctx, now, det):
        s = det.get("sigma_1s") or 0.0; q = self._sig[market.asset]
        if s > 0: q.append(s)
        return len(q) >= 120 and s >= self.QUIET * statistics.median(q)


class OneAnHour(Picker):
    """9: at most one bet in a clock hour (the first whose edge is large)."""
    def __init__(self, *a, **kw): super().__init__(*a, **kw); self._hour = None
    def gate(self, market, ctx, now, det):
        if self._hour is None and self.acct.settlements: self._hour = int(self.acct.settlements[-1].get("ts", 0) // 3600)   # survives a restart
        return int(now // 3600) != self._hour
    def placed(self, market, now): self._hour = int(now // 3600)


class BitcoinLeads(Picker):
    """11: on the other coins, Bitcoin has just moved LEAD times its usual one-minute move and this coin has not yet
    followed (under half as far): bet this coin Bitcoin's way."""
    LEAD = 1.5
    def signal(self, market, ctx, now, p, det):
        b = self.trend(ctx, "btc", now, 60); c = self.trend(ctx, market.asset, now, 60)
        if b is None or c is None: return None
        usual = ctx["spot"].sigma("btc") * 60 ** 0.5
        if abs(b) < self.LEAD * usual or abs(c) > 0.5 * abs(b): return None
        return b > 0


class BookPressure(Picker):
    """12: one side of the order book has RATIO times the size of the other within its first five levels."""
    RATIO = 3.0
    def signal(self, market, ctx, now, p, det):
        bid = sum(q for _, q in market.bids[:5]); ask = sum(q for _, q in market.offers[:5])
        if bid <= 0 or ask <= 0: return None
        if bid >= self.RATIO * ask: return True
        if ask >= self.RATIO * bid: return False
        return None


class ExchangeGap(Picker):
    """13: Coinbase has moved GAP times its usual 10-second move in the last 10 s and Polymarket's mid price has not
    moved a cent in that time: bet with Coinbase."""
    GAP = 2.0
    def __init__(self, *a, **kw): super().__init__(*a, **kw); self._mid = collections.defaultdict(lambda: collections.deque(maxlen=40))
    def signal(self, market, ctx, now, p, det):
        if len(self._mid) > 60:                                      # windows that have closed
            for k in list(self._mid)[:30]: del self._mid[k]
        h = self._mid[market.slug]; mid = (market.best_bid + market.best_ask) / 2; h.append((now, mid))
        old = next((m for t, m in h if now - t <= 10.5), None)
        if old is None or now - h[0][0] < 9 or abs(mid - old) >= 0.01: return None
        t = self.trend(ctx, market.asset, now, 10)
        if t is None or abs(t) < self.GAP * ctx["spot"].sigma(market.asset) * 10 ** 0.5: return None
        return t > 0


class LongVsShort(Picker):
    """14: the same coin's 15-minute window leans one way (its Up price outside 40-60c) and the 5-minute window has
    not followed: bet the 5-minute window the 15-minute window's way."""
    def signal(self, market, ctx, now, p, det):
        big = next((m for m in list(self.engine.mf.markets.values()) if m.asset == market.asset and m.kind == "updown" and m.dur == 900
                    and m.start <= now < m.end and m.best_bid is not None and m.best_ask is not None), None)
        if big is None or now - big.book_ts > 10: return None
        mid = (big.best_bid + big.best_ask) / 2
        if mid >= 0.60: return True
        if mid <= 0.40: return False
        return None


class Echo(Picker):
    """15: does a window's result repeat in the next one? A window closed Up when the next window's price to beat is
    at or above its own. Over the last N windows of the coin: repeats at least HI of the time, bet the last result
    again; at most LO, bet against it; otherwise no bet."""
    N = 20; HI = 0.6; LO = 0.4
    def __init__(self, *a, **kw): super().__init__(*a, **kw); self._p2b = collections.defaultdict(dict)       # asset -> window start -> price to beat
    def signal(self, market, ctx, now, p, det):
        h = self._p2b[market.asset]
        if market.p2b is not None: h[int(market.start)] = market.p2b
        for k in sorted(h)[:-(self.N + 3)]: del h[k]
        ks = sorted(h); ups = [h[b] >= h[a] for a, b in zip(ks, ks[1:]) if b - a == 300]      # results of consecutive windows
        if len(ups) < 8 or ks[-1] != int(market.start) or ks[-1] - ks[-2] != 300: return None
        rep = sum(1 for x, y in zip(ups, ups[1:]) if x == y) / (len(ups) - 1)
        if rep >= self.HI: return ups[-1]
        if rep <= self.LO: return not ups[-1]
        return None


class HedgedPair(_New):
    """16: Bitcoin's and Ether's windows over the same five minutes are priced at least GAP apart: buy Up in the
    cheaper one and Down in the dearer one, the same number of contracts, once per window. The pair pays one of the
    two when the coins move together and is held to the close."""
    GAP = 0.15
    def __init__(self, account, name, engine=None, **kw): super().__init__(account, name, assets=("btc", "eth"), **kw); self.engine = engine
    def on_tick(self, market, ctx, now):
        if market.asset != "btc" or market.dur not in self.durs: return
        st = self.mem.setdefault(market.slug, {"done": False})
        if market.slug in self.acct.positions: st["done"] = True
        left = market.remaining(now)
        if st["done"] or not self.ready(market, now) or not (60 <= left <= 240): return
        other = next((m for m in list(self.engine.mf.markets.values()) if m.asset == "eth" and m.kind == "updown" and m.dur == market.dur and abs(m.end - market.end) < 1), None)
        if other is None or not self.ready(other, now) or now - other.book_ts > 10: return
        if self.fair(market, ctx, now)[0] is None: return
        for cheap, dear in ((market, other), (other, market)):
            cost = cheap.best_ask + (1 - dear.best_bid)                    # Up in the cheap one, Down in the dear one
            if dear.best_bid - cheap.best_ask >= self.GAP:
                n = round(STAKE * self.acct.bank() / cost, 2)
                if n < max(cheap.min_qty, dear.min_qty, 0.01): return
                a = self.acct.take(cheap, "buy", n, limit=cheap.best_ask, tag=f"{self.name} up {cheap.asset} {cheap.best_ask:.2f}", fixed=True)
                if a and a.filled > 0:
                    self.acct.take(dear, "sell", round(a.filled, 2), limit=dear.best_bid, tag=f"{self.name} down {dear.asset} {1 - dear.best_bid:.2f}", fixed=True)
                    st["done"] = True
                return


class LockIn(Picker):
    """17: the model's side; once it is worth GAIN more a contract than was paid, it is sold to keep the profit."""
    GAIN = 0.20
    def after(self, market, ctx, now):
        st = self.mem[market.slug]; pos = self.acct.positions.get(market.slug)
        if st.get("out") or "price" not in st or not pos or pos.closed or abs(pos.qty) < 1e-9 or market.remaining(now) < 5: return
        if market.best_bid is None or market.best_ask is None: return
        worth = market.best_bid if pos.qty > 0 else 1 - market.best_ask
        if worth - st["price"] >= self.GAIN:
            side, lim = ("sell", market.best_bid) if pos.qty > 0 else ("buy", market.best_ask)
            o = self.acct.take(market, side, round(abs(pos.qty), 2), limit=lim, tag=f"{self.name} lock {worth:.2f} from {st['price']:.2f}", fixed=True)
            if o and o.filled > 0: st["out"] = True


class ShrinkAfterLosses(Picker):
    """18: the model's side; the bet halves after each losing clock hour (down to an eighth) and doubles back after a
    winning one."""
    def scale(self, now):
        hours = {}
        for r in self.acct.settlements:
            hours[int(r.get("ts", 0) // 3600)] = hours.get(int(r.get("ts", 0) // 3600), 0.0) + r.get("pnl_total", 0.0) - r.get("fees", 0.0)
        m = 1.0
        for h in sorted(hours):
            if h >= int(now // 3600): break
            m = max(0.125, m / 2) if hours[h] < 0 else min(1.0, m * 2)
        return m


class ProfitTarget(Picker):
    """19: the model's side; no new bets for the rest of the UTC day once the day's settled profit reaches TARGET of
    the bank."""
    TARGET = 0.02
    def gate(self, market, ctx, now, det): return self.day(now) < self.TARGET * self.acct.start_cash


class Committee(Picker):
    """20: all_horizons, stairstep and consensus each run on a private practice account; a bet is made only in a
    window where all three hold the same side, in that side, at up to MAX_PRICE."""
    MEMBERS = ("all_horizons", "stairstep", "consensus"); MAX_PRICE = 0.95
    def __init__(self, account, name, engine=None, pos_mult=15.0, **kw):
        from .strategies import build_registry
        from .sim import Account
        super().__init__(account, name, t_hi=120, t_lo=5, band=(0.05, self.MAX_PRICE), edge=None, assets=("btc", "eth"), engine=engine, **kw)
        reg = build_registry(pos_mult); self.shadow = []
        for k in self.MEMBERS:
            a = Account(f"{name}~{k}", 1e9); a.universe = f"shadow:{name}"; s = reg[k](a)
            if isinstance(getattr(s, "durs", None), (set, tuple, list)): s.durs = {300}
            self.shadow.append(s)

    def on_tick(self, market, ctx, now):
        if market.asset in self.assets and market.dur in self.durs:
            for s in self.shadow:
                s.acct.on_book(market); s.on_tick(market, ctx, now); s.acct.fills.clear()
                if len(s.acct.positions) > 12:                     # practice positions are never settled: forget closed windows
                    live = self.engine.mf.markets
                    for slug in [k for k in s.acct.positions if k not in live]: s.acct.cancel_all(slug); del s.acct.positions[slug]
                    s.acct.cash = 1e9
        super().on_tick(market, ctx, now)

    def signal(self, market, ctx, now, p, det):
        qs = [(s.acct.positions.get(market.slug).qty if market.slug in s.acct.positions else 0.0) for s in self.shadow]
        if all(q > 1e-9 for q in qs): return True
        if all(q < -1e-9 for q in qs): return False
        return None


# name: (what it does, class, settings). The number is the idea's number in the list shown to the user.
BOTS = {
    "new_00_volume_rest_all_windows_plus_trend_take": ("NEW volume bot: rests one order at the better side of fair value on all four coins, 5- and 15-minute windows, plus a small take when the model's edge is 5c and the 3-minute trend agrees",
                                                       Quoter, {"one": True, "durs": (300, 900), "trend_take": 0.05}),
    "new_01_both_sides_quoter": ("NEW 1: rests a bid 3c under and an offer 3c over fair value", Quoter, {}),
    "new_02_fair_value_rester": ("NEW 2: rests one order, on the side whose price is further from fair value", Quoter, {"one": True}),
    "new_03_early_window_rester": ("NEW 3: both-sides quotes in the first minute of a window only", Quoter, {"el": 5, "stop_el": 60, "left": 200}),
    "new_04_inventory_balancer": ("NEW 4: both-sides quotes that shift up to 4c to get rid of what it holds", Quoter, {"skew": 0.04}),
    "new_05_queue_sitter": ("NEW 5: both-sides quotes at the allowed price with the least size waiting", Quoter, {"queue": True}),
    "new_06_strong_trend_only": ("NEW 6: bets only when the 1-, 3- and 10-minute trends agree and the model has a 1c edge", StrongTrend, {"edge": 0.01}),
    "new_07_big_move_follower": ("NEW 7: bets with the move once the coin has moved more than usual in the window, up to 80c", BigMove, {"edge": 0.0, "band": (0.30, 0.80)}),
    "new_08_quiet_market_skipper": ("NEW 8: the model's side at a 3c edge, skipped while the coin is quiet", QuietSkip, {}),
    "new_09_one_bet_an_hour": ("NEW 9: one bet a clock hour, the first with a 6c edge", OneAnHour, {"edge": 0.06}),
    "new_10_cheap_side_only": ("NEW 10: the model's side at a 3c edge, never above 45c", Picker, {"band": (0.10, 0.45)}),
    "new_11_bitcoin_leads": ("NEW 11: Ether, Solana and XRP windows bet Bitcoin's way after Bitcoin jumps and they have not followed", BitcoinLeads, {"assets": ("eth", "sol", "xrp"), "edge": None}),
    "new_12_order_book_pressure": ("NEW 12: bets the side with three times the resting size behind it", BookPressure, {"edge": None}),
    "new_13_exchange_gap": ("NEW 13: bets with a Coinbase jump while Polymarket's price has not moved", ExchangeGap, {"edge": None, "t_hi": 280, "t_lo": 20, "band": (0.20, 0.80)}),
    "new_14_long_window_vs_short": ("NEW 14: bets the 5-minute window the way the 15-minute window leans, at 55c or less", LongVsShort, {"edge": None, "band": (0.20, 0.55)}),
    "new_15_last_window_echo": ("NEW 15: bets that the last window's result repeats (or reverses) when it has been doing so", Echo, {"edge": None, "t_hi": 290, "t_lo": 180, "band": (0.35, 0.58)}),
    "new_16_hedged_pair": ("NEW 16: Up in the cheaper of Bitcoin and Ether, Down in the dearer, when 15c apart", HedgedPair, {}),
    "new_17_lock_in": ("NEW 17: the model's side at a 3c edge, sold once it is 20c ahead", LockIn, {}),
    "new_18_shrink_after_losses": ("NEW 18: the model's side at a 3c edge, bet halved after each losing hour", ShrinkAfterLosses, {}),
    "new_19_profit_target": ("NEW 19: the model's side at a 3c edge, stops for the day at +2% of the bank", ProfitTarget, {}),
    "new_20_committee": ("NEW 20: bets only when all_horizons, stairstep and consensus hold the same side", Committee, {}),
}
LABELS = {k: v[0] for k, v in BOTS.items()}
NEEDS_ENGINE = (LongVsShort, HedgedPair, Committee)


def build_new_registry(engine=None, pos_mult=15.0):
    def make(k, cls, kw):
        def f(acct):
            extra = {"engine": engine} if issubclass(cls, NEEDS_ENGINE) else {}
            if cls is Committee: extra["pos_mult"] = pos_mult
            return cls(acct, k, **kw, **extra)
        return f
    return {k: make(k, cls, kw) for k, (_, cls, kw) in BOTS.items()}
