"""Twenty more tryout bots (user, 11-14 11:15Z): ideas not used or proposed before, each on its own $1,000 tryout
account. Names start with "new2_". They join the builder source "new" (own sizes, own markets, WARMUP after a start).

The brief: unique, fitted to Polymarket US, and not "buy at 50c, wait, take 2%". What is particular to Polymarket US
and used here: one contract a market (Down is a sale of Up); a taker fee of 0.095 x p x (1 - p) a contract, which is
2.4c at 50c and under half a cent beyond 5c/95c, while resting orders are free; one-cent price steps; four coins
whose windows start and end together; a line set from the index at the window's start.

Each bot is one rule about something the other bots do not look at:
  the order book's own history (1, 14, 17, 18), the coin's path inside the window (3, 4, 5, 15), the clock (7, 16),
  the four coins as a group (6, 13, 20), the fee (12, 19), how an order is worked (8, 11), and how much to bet or
  which rule to trust (2, 9, 10).
All bet at most once a window on 5-minute windows and hold to the close unless the line says otherwise. None of
them has been tested before; they are in the tryout to find out.
"""
import math, time, random, collections, statistics
from .newbots import _New, Picker, STAKE, CAP, fee

Snap = collections.namedtuple("Snap", "t mid bb ba")


def secs(ctx, asset, t0, t1):
    """Coinbase's price in each second from t0 to t1 (carried forward); None where nothing is known."""
    sp = ctx["spot"]
    return [sp.sec(asset, t) for t in range(int(t0), int(t1) + 1)]


class _Hist(Picker):
    """A Picker that keeps each window's order-book history (time, mid, best bid, best ask) from its first tick."""
    def __init__(self, *a, **kw): super().__init__(*a, **kw); self._h = collections.defaultdict(lambda: collections.deque(maxlen=900))

    def on_tick(self, market, ctx, now):
        if market.asset in self.assets and market.dur in self.durs and market.best_bid is not None and market.best_ask is not None:
            h = self._h[market.slug]
            if not h or now - h[-1].t >= 0.5: h.append(Snap(now, (market.best_bid + market.best_ask) / 2, market.best_bid, market.best_ask))
            if len(self._h) > 40:
                for k in list(self._h)[:20]: del self._h[k]
        super().on_tick(market, ctx, now)

    def ago(self, slug, now, s):
        """The newest snapshot at least `s` seconds old, or None."""
        return next((x for x in reversed(self._h[slug]) if now - x.t >= s), None)


# ---- 1: phantom move ----------------------------------------------------------------------------------------------------
class PhantomFade(_Hist):
    """The contract's price has moved MOVE in ten seconds while Coinbase has barely moved (under QUIET of its usual
    ten-second move): nothing in the coin explains the move, so bet that it comes back."""
    MOVE = 0.10; QUIET = 0.7
    def signal(self, market, ctx, now, p, det):
        old = self.ago(market.slug, now, 10)
        if old is None or now - old.t > 13: return None
        d = (market.best_bid + market.best_ask) / 2 - old.mid; t = self.trend(ctx, market.asset, now, 10)
        if abs(d) < self.MOVE or t is None or abs(t) > self.QUIET * ctx["spot"].sigma(market.asset) * 10 ** 0.5: return None
        return d < 0


# ---- 2: change of weather -----------------------------------------------------------------------------------------------
class VolShift(Picker):
    """Compares how much the coin moved in the last minute with the last five. A sudden storm (SPIKE times as lively)
    makes a comeback likelier than the price says: buy the trailing side when it costs CHEAP or less. A sudden calm
    (CALM times) makes the leader safer: buy the leading side at up to DEAR."""
    SPIKE = 2.0; CALM = 0.5; CHEAP = 0.30; DEAR = 0.85
    @staticmethod
    def sd(xs):
        r = [math.log(b / a) for a, b in zip(xs, xs[1:]) if a and b]
        return statistics.pstdev(r) if len(r) >= 20 else None
    def signal(self, market, ctx, now, p, det):
        xs = secs(ctx, market.asset, now - 300, now); a = self.sd(xs[-60:]); b = self.sd(xs)
        if not a or not b: return None
        lead_up = det["z"] > 0; up_cost = market.best_ask; dn_cost = 1 - market.best_bid
        if a >= self.SPIKE * b:
            cost = dn_cost if lead_up else up_cost
            return (not lead_up) if cost <= self.CHEAP else None
        if a <= self.CALM * b:
            cost = up_cost if lead_up else dn_cost
            return lead_up if 0.60 <= cost <= self.DEAR else None
        return None


# ---- 3: round-number wall -----------------------------------------------------------------------------------------------
class RoundWall(Picker):
    """Round prices slow a coin down. When a round number lies between the coin and the line, the coin has to get
    through it to change the result: bet that it does not. The step is the 1-2.5-5 number nearest NEAR of the price."""
    NEAR = 0.003
    @staticmethod
    def step(px, near):
        want = px * near; e = 10 ** math.floor(math.log10(want))
        return min((m * e for m in (1, 2.5, 5, 10)), key=lambda s: abs(math.log(s / want)))
    def signal(self, market, ctx, now, p, det):
        S = det["S"]; K = det["K"]
        if not S or not K or S == K: return None
        s = self.step(S, self.NEAR); lo, hi = min(S, K), max(S, K)
        wall = math.ceil(lo / s) * s
        return (S > K) if lo < wall < hi else None


# ---- 4: break of the window's range --------------------------------------------------------------------------------------
class RangeBreak(Picker):
    """In the second half of a window the coin sets a new high (low) for the window after at least WAIT seconds
    inside its range: bet with the break."""
    WAIT = 60
    def signal(self, market, ctx, now, p, det):
        if now - market.start < market.dur / 2: return None
        xs = [x for x in secs(ctx, market.asset, market.start, now) if x]
        if len(xs) < self.WAIT + 30: return None
        old = xs[:-3]; new = max(xs[-3:]), min(xs[-3:])
        if new[0] > max(old) and max(old[-self.WAIT:]) < max(old): return True
        if new[1] < min(old) and min(old[-self.WAIT:]) > min(old): return False
        return None


# ---- 5: tick count ------------------------------------------------------------------------------------------------------
class TickCount(Picker):
    """Counts seconds, not size: of the last 30 seconds in which Coinbase's price changed, SHARE or more went one
    way (and at least MIN changed): bet that way."""
    SHARE = 0.72; MIN = 14
    def signal(self, market, ctx, now, p, det):
        xs = secs(ctx, market.asset, now - 30, now); ch = [b - a for a, b in zip(xs, xs[1:]) if a and b and a != b]
        if len(ch) < self.MIN: return None
        up = sum(1 for c in ch if c > 0) / len(ch)
        return True if up >= self.SHARE else False if up <= 1 - self.SHARE else None


# ---- 6: odd one out -----------------------------------------------------------------------------------------------------
class _Group(Picker):
    """Sees the other coins' windows that started with this one."""
    def others(self, market, ctx, now):
        """[(market, P(Up), z)] for the other coins' same-length windows with the same start and a live book."""
        out = []
        for m in list(self.engine.mf.markets.values()):
            if m is market or m.kind != "updown" or m.dur != market.dur or m.start != market.start or m.best_bid is None or m.best_ask is None or now - m.book_ts > 10: continue
            p, det = self.fair(m, ctx, now)
            if p is not None: out.append((m, p, det["z"]))
        return out


class OddOneOut(_Group):
    """The other three coins are all clearly on one side of their lines (z beyond PULL) and this coin is still at
    its line (within FLAT): the group usually drags the last one along, so bet it joins them."""
    PULL = 0.8; FLAT = 0.3
    def signal(self, market, ctx, now, p, det):
        o = self.others(market, ctx, now)
        if len(o) < 3 or abs(det["z"]) > self.FLAT: return None
        if all(z >= self.PULL for _, _, z in o): return True
        if all(z <= -self.PULL for _, _, z in o): return False
        return None


# ---- 7: turn of the hour ------------------------------------------------------------------------------------------------
class HourTurn(Picker):
    """Only the first window of a clock hour. If the coin ran BIG times its usual quarter-hour move in the fifteen
    minutes before the hour, bet the first window goes the other way."""
    BIG = 1.5
    def signal(self, market, ctx, now, p, det):
        if int(market.start) % 3600: return None
        a = ctx["spot"].sec(market.asset, market.start - 900); b = ctx["spot"].sec(market.asset, market.start)
        if not a or not b: return None
        mv = b / a - 1
        if abs(mv) < self.BIG * ctx["spot"].sigma(market.asset) * 900 ** 0.5: return None
        return mv < 0


# ---- 8: climbing bid ----------------------------------------------------------------------------------------------------
class ClimbingBid(_New):
    """Works one resting order like a reverse auction: on the model's side it starts START below the model's fair
    price and comes one cent closer every STEP seconds, stopping END short of fair. The first fill is the bet; an
    impatient seller sets the price instead of the bot."""
    START = 0.07; END = 0.01; STEP = 12.0
    def on_tick(self, market, ctx, now):
        if market.asset not in self.assets or market.dur not in self.durs: return
        slug = market.slug; a = self.acct; st = self.mem.setdefault(slug, {"t0": None, "q": None})
        left = market.remaining(now); pos = a.positions.get(slug)
        if pos and abs(pos.qty) > 1e-9 or not self.ready(market, now) or not (45 <= left <= 255):
            if st["q"] is not None: a.cancel_all(slug); st["q"] = None
            return
        p, det = self.fair(market, ctx, now)
        if p is None or 0.40 <= p <= 0.60:
            if st["q"] is not None: a.cancel_all(slug); st["q"] = None
            return
        up = p > 0.5
        if st["t0"] is None or st.get("up") != up: st["t0"] = now; st["up"] = up
        gap = max(self.END, self.START - 0.01 * int((now - st["t0"]) / self.STEP))
        px = round(min(p - gap, market.best_ask - 0.01), 2) if up else round(max(p + gap, market.best_bid + 0.01), 2)
        cost = px if up else 1 - px
        if not (0.10 <= cost <= 0.90):
            if st["q"] is not None: a.cancel_all(slug); st["q"] = None
            return
        if px != st["q"]:
            a.cancel_all(slug); st["q"] = px
            a.place(market, "buy" if up else "sell", px, round(STAKE * a.bank() / cost, 2), post_only=True, tag=f"{self.name} {'up' if up else 'down'} {gap * 100:.0f}c under fair {p:.2f}", fixed=True)


# ---- 9: learner ---------------------------------------------------------------------------------------------------------
class Bandit(Picker):
    """Four simple rules vote in every window (the model's side; the one-minute trend; against the three-minute
    trend; the heavier side of the order book). It scores each rule on every window's result, whether or not it bet,
    and each window follows the rule that a random draw from its record favours, so rules that have been right
    lately get used more. It starts knowing nothing; its record is lost at an engine restart."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw); self.score = {k: [1, 1] for k in ("model", "trend60", "fade180", "book")}; self.open = {}
    def votes(self, market, ctx, now, p):
        v = {"model": p >= 0.5}
        t = self.trend(ctx, market.asset, now, 60)
        if t: v["trend60"] = t > 0
        t = self.trend(ctx, market.asset, now, 180)
        if t: v["fade180"] = t < 0
        b = sum(q for _, q in market.bids[:5]); a = sum(q for _, q in market.offers[:5])
        if b > 0 and a > 0 and max(a, b) >= 1.5 * min(a, b): v["book"] = b > a
        return v
    def on_tick(self, market, ctx, now):
        for slug, (asset, end, line, v) in list(self.open.items()):       # score the windows that have ended
            if now < end + 3: continue
            x = ctx["index"].value_at(asset, int(end) - 1)
            if x is not None:
                for k, up in v.items(): self.score[k][0 if up == (x >= line) else 1] += 1
            if x is not None or now > end + 120: del self.open[slug]
        super().on_tick(market, ctx, now)
    def signal(self, market, ctx, now, p, det):
        v = self.votes(market, ctx, now, p)
        if market.slug not in self.open and market.p2b is not None: self.open[market.slug] = (market.asset, market.end, market.p2b, dict(v))
        pick = max(v, key=lambda k: random.betavariate(*self.score[k]))
        self.mem[market.slug]["rule"] = pick
        return v[pick]
    def snapshot(self): return {k: {"right": w - 1, "wrong": l - 1} for k, (w, l) in self.score.items()}


# ---- 10: bet by the odds ------------------------------------------------------------------------------------------------
class Kelly(Picker):
    """The model's side, but the size follows the odds: a quarter of the stake that the edge and the price justify
    (edge after the fee divided by what a win pays), from a fifth of the usual stake to three times it."""
    FRACTION = 0.25
    def signal(self, market, ctx, now, p, det):
        up = p >= 0.5; price = market.best_ask if up else 1 - market.best_bid
        e = (p if up else 1 - p) - price - fee(self.acct.theta, price)
        self._m = max(0.2, min(3.0, self.FRACTION * e / max(1 - price, 0.02) / STAKE)) if e > 0 else 0.0
        return up
    def scale(self, now): return getattr(self, "_m", 1.0)


# ---- 11: early cashier --------------------------------------------------------------------------------------------------
class EarlyCashier(Picker):
    """Buys a clear leader mid-window and does not wait for the bell: straight after the bet it rests an order to
    sell at CASH, so the last seconds' reversals happen to someone else. If that never fills it holds to the close."""
    CASH = 0.97
    def signal(self, market, ctx, now, p, det): return (p >= 0.5) if abs(p - 0.5) >= 0.25 else None
    def after(self, market, ctx, now):
        st = self.mem[market.slug]; pos = self.acct.positions.get(market.slug)
        if st.get("out") or not pos or pos.closed or abs(pos.qty) < 1e-9 or market.best_bid is None or market.best_ask is None: return
        if pos.qty > 0 and market.best_bid < self.CASH:
            st["out"] = bool(self.acct.place(market, "sell", self.CASH, round(pos.qty, 2), post_only=True, tag=f"{self.name} cash out", fixed=True))
        elif pos.qty < 0 and market.best_ask > 1 - self.CASH:
            st["out"] = bool(self.acct.place(market, "buy", round(1 - self.CASH, 2), round(-pos.qty, 2), post_only=True, tag=f"{self.name} cash out", fixed=True))


# ---- 12: lottery ticket -------------------------------------------------------------------------------------------------
class Lottery(Picker):
    """Where the fee is almost nothing: a side priced at TOP or less that the model gives at least twice its price
    (and 3c more). Small stakes, most tickets lose, a winner pays ten to thirty times."""
    TOP = 0.10
    def signal(self, market, ctx, now, p, det):
        up, dn = market.best_ask, 1 - market.best_bid
        if up <= self.TOP and p >= max(2 * up, up + 0.03): return True
        if dn <= self.TOP and 1 - p >= max(2 * dn, dn + 0.03): return False
        return None
    def scale(self, now): return 0.3


# ---- 13: liveliest coin -------------------------------------------------------------------------------------------------
class HotCoin(Picker):
    """Each window it trades one coin only: the one that is liveliest compared with its own recent normal (its
    volatility now over its median of the last half hour). The model's side at a 3c edge, in that coin."""
    def __init__(self, *a, **kw): super().__init__(*a, **kw); self._s = collections.defaultdict(lambda: collections.deque(maxlen=360))
    def heat(self, ctx, asset, now):
        q = self._s[asset]; s = ctx["spot"].sigma(asset)
        if not q or now - q[-1][0] >= 5: q.append((now, s))
        return s / statistics.median(x for _, x in q) if len(q) >= 24 else None
    def gate(self, market, ctx, now, det):
        h = {a: self.heat(ctx, a, now) for a in self.assets}
        if any(v is None for v in h.values()): return False
        return max(h, key=h.get) == market.asset and h[market.asset] >= 1.1


# ---- 14: V shape --------------------------------------------------------------------------------------------------------
class VShape(_Hist):
    """Reads the contract's own chart. It fell DROP from its high of the window to a low and has since won back
    BACK of the fall: the sellers are done, bet Up. The mirror image (a rise given half back) bets Down."""
    DROP = 0.15; BACK = 0.5
    def signal(self, market, ctx, now, p, det):
        h = list(self._h[market.slug])
        if len(h) < 40: return None
        mids = [x.mid for x in h]; cur = mids[-1]
        lo = min(mids); i = mids.index(lo); hi = max(mids[:i + 1])
        if hi - lo >= self.DROP and len(mids) - i >= 10 and self.BACK * (hi - lo) <= cur - lo < 0.9 * (hi - lo): return True
        hi = max(mids); i = mids.index(hi); lo = min(mids[:i + 1])
        if hi - lo >= self.DROP and len(mids) - i >= 10 and self.BACK * (hi - lo) <= hi - cur < 0.9 * (hi - lo): return False
        return None


# ---- 15: one-way window -------------------------------------------------------------------------------------------------
class OneWay(Picker):
    """The coin has been on the same side of the line every second since the window's first half minute: it has
    not once looked back. Bet that side, at up to 82c."""
    def signal(self, market, ctx, now, p, det):
        K = det["K"]; xs = [x for x in secs(ctx, market.asset, market.start + 30, now) if x]
        if len(xs) < 90: return None
        if all(x > K for x in xs): return True
        if all(x < K for x in xs): return False
        return None


# ---- 16: too sure too soon ----------------------------------------------------------------------------------------------
class TooSure(Picker):
    """In a window's first 75 seconds one side is already priced at SURE or more. Most of the window is still to
    come, so take the other side cheaply."""
    SURE = 0.78
    def signal(self, market, ctx, now, p, det):
        mid = (market.best_bid + market.best_ask) / 2
        return False if mid >= self.SURE else True if mid <= 1 - self.SURE else None


# ---- 17: the makers come back -------------------------------------------------------------------------------------------
class MakerReturn(_Hist):
    """The gap between the best bid and offer blew out to WIDE or more (the market makers pulled their orders) and
    has closed again within LOOK seconds. Where they came back tells what they learned: if the middle is now
    higher than while the gap was open, bet Up; lower, Down."""
    WIDE = 0.04; LOOK = 15
    def signal(self, market, ctx, now, p, det):
        if market.best_ask - market.best_bid > 0.02: return None
        wide = [x for x in self._h[market.slug] if now - x.t <= self.LOOK and x.ba - x.bb >= self.WIDE]
        if not wide: return None
        d = (market.best_bid + market.best_ask) / 2 - wide[0].mid
        return True if d >= 0.015 else False if d <= -0.015 else None


# ---- 18: big print ------------------------------------------------------------------------------------------------------
class BigPrint(_Hist):
    """Someone just bought through the offers: the best offer jumped JUMP or more within two seconds while the best
    bid held (or the mirror image for a seller). Whoever pays up like that usually knows something: follow them."""
    JUMP = 0.03
    def signal(self, market, ctx, now, p, det):
        old = self.ago(market.slug, now, 1.5)
        if old is None or now - old.t > 3: return None
        if market.best_ask - old.ba >= self.JUMP and market.best_bid >= old.bb: return True
        if old.bb - market.best_bid >= self.JUMP and market.best_ask <= old.ba: return False
        return None


# ---- 19: fee router -----------------------------------------------------------------------------------------------------
class FeeRouter(_New):
    """The model's side with a 3c edge before the fee, and then one decision the other bots do not make: how to get
    in. If the fee for taking is under a cent (prices far from 50c) it takes. If not, and there is room inside the
    bid-offer gap, it rests one cent inside for WAIT seconds and pays nothing; only if that fails and the edge
    still covers the fee does it take."""
    EDGE = 0.03; WAIT = 20.0
    def on_tick(self, market, ctx, now):
        if market.asset not in self.assets or market.dur not in self.durs: return
        slug = market.slug; a = self.acct; st = self.mem.setdefault(slug, {"rest": None})
        left = market.remaining(now); pos = a.positions.get(slug)
        if pos and abs(pos.qty) > 1e-9 or not self.ready(market, now) or not (50 <= left <= 240):
            if st["rest"]: a.cancel_all(slug); st["rest"] = None
            return
        p, det = self.fair(market, ctx, now)
        if p is None: return
        bb, ba = market.best_bid, market.best_ask; up = p >= 0.5
        price = ba if up else 1 - bb; raw = (p if up else 1 - p) - price; f = fee(a.theta, price)
        if raw < self.EDGE or not (0.08 <= price <= 0.92):
            if st["rest"]: a.cancel_all(slug); st["rest"] = None
            return
        dollars = STAKE * a.bank()
        if f < 0.01 or (st["rest"] and now - st["rest"] >= self.WAIT and raw - f >= 0.01):
            a.cancel_all(slug); st["rest"] = None
            self.bet(market, up, dollars, f"{self.name} take {'up' if up else 'down'} {price:.2f} fee {f * 100:.1f}c")
        elif not st["rest"] and ba - bb >= 0.02:
            px = round(bb + 0.01, 2) if up else round(ba - 0.01, 2); cost = px if up else 1 - px
            if a.place(market, "buy" if up else "sell", px, round(dollars / cost, 2), post_only=True, tag=f"{self.name} rest inside {px:.2f} saves {f * 100:.1f}c", fixed=True): st["rest"] = now
        elif not st["rest"] and raw - f >= 0.01:
            self.bet(market, up, dollars, f"{self.name} take {'up' if up else 'down'} {price:.2f} no room to rest")


# ---- 20: the coin out of line -------------------------------------------------------------------------------------------
class OutlierCoin(_Group):
    """Uses the four coins to check each other. For each coin: the market's price minus the model's. The average of
    that across the coins is the model's own error; a coin whose gap is OUT away from the average is the one the
    market has mispriced. Bet it back toward the others."""
    OUT = 0.05
    def signal(self, market, ctx, now, p, det):
        o = self.others(market, ctx, now)
        if len(o) < 2: return None
        gaps = [(m.best_bid + m.best_ask) / 2 - q for m, q, _ in o]; mine = (market.best_bid + market.best_ask) / 2 - p
        d = mine - statistics.mean(gaps)
        return True if d <= -self.OUT else False if d >= self.OUT else None


N = {"edge": None}          # the rule decides; the model is not asked for an edge
BOTS = {
    "new2_01_phantom_move_fade": ("NEW2 1: the contract moved 10c in ten seconds while the coin did not: bets it comes back", PhantomFade, {**N, "t_hi": 270, "t_lo": 40, "band": (0.15, 0.85)}),
    "new2_02_change_of_weather": ("NEW2 2: a sudden storm in the coin buys the trailing side at 30c or less; a sudden calm buys the leader up to 85c", VolShift, {**N, "t_hi": 200, "t_lo": 45, "band": (0.05, 0.85)}),
    "new2_03_round_number_wall": ("NEW2 3: a round price stands between the coin and the line: bets the coin does not get through it", RoundWall, {**N, "t_hi": 150, "t_lo": 40, "band": (0.50, 0.85)}),
    "new2_04_window_range_break": ("NEW2 4: in the second half the coin breaks its high or low of the window after a minute inside it: bets with the break", RangeBreak, {**N, "t_hi": 150, "t_lo": 30, "band": (0.20, 0.85)}),
    "new2_05_tick_count": ("NEW2 5: of the last 30 seconds in which the coin's price changed, 72% went one way: bets that way", TickCount, {**N, "t_hi": 240, "t_lo": 45, "band": (0.25, 0.75)}),
    "new2_06_odd_one_out": ("NEW2 6: three coins are clearly past their lines and the fourth is still on its line: bets it follows them", OddOneOut, {**N, "t_hi": 220, "t_lo": 60, "band": (0.30, 0.65)}),
    "new2_07_turn_of_the_hour": ("NEW2 7: first window of a clock hour only: bets against a big run in the quarter hour before it", HourTurn, {**N, "t_hi": 290, "t_lo": 180, "band": (0.30, 0.60)}),
    "new2_08_climbing_bid": ("NEW2 8: one resting order on the model's side that starts 7c under fair and climbs a cent every 12 seconds until someone sells to it", ClimbingBid, {}),
    "new2_09_learner": ("NEW2 9: four simple rules vote each window; it follows whichever has been right most lately and keeps score on all four", Bandit, {**N, "t_hi": 200, "t_lo": 60, "band": (0.25, 0.75)}),
    "new2_10_bet_by_the_odds": ("NEW2 10: the model's side at a 2c edge, with the size set by the odds (quarter Kelly) instead of a fixed stake", Kelly, {"edge": 0.02, "band": (0.10, 0.90)}),
    "new2_11_early_cashier": ("NEW2 11: buys a clear leader at 70-88c and rests an order to sell at 97c instead of waiting for the bell", EarlyCashier, {"edge": 0.02, "t_hi": 240, "t_lo": 90, "band": (0.70, 0.88)}),
    "new2_12_lottery_ticket": ("NEW2 12: small bets on a side at 10c or less that the model gives twice its price; the fee there is under a cent", Lottery, {**N, "t_hi": 240, "t_lo": 30, "band": (0.02, 0.10)}),
    "new2_13_liveliest_coin": ("NEW2 13: each window trades only the coin that is liveliest against its own normal, on the model's side at a 3c edge", HotCoin, {}),
    "new2_14_v_shape": ("NEW2 14: the contract fell 15c and has won half of it back (or the mirror image): bets the turn continues", VShape, {**N, "t_hi": 220, "t_lo": 40, "band": (0.20, 0.80)}),
    "new2_15_one_way_window": ("NEW2 15: the coin has not been on the other side of the line since the window's first half minute: bets it stays, up to 82c", OneWay, {**N, "t_hi": 170, "t_lo": 60, "band": (0.55, 0.82)}),
    "new2_16_too_sure_too_soon": ("NEW2 16: one side is priced at 78c or more in the window's first 75 seconds: takes the other side cheaply", TooSure, {**N, "t_hi": 290, "t_lo": 225, "band": (0.08, 0.25)}),
    "new2_17_makers_come_back": ("NEW2 17: the bid-offer gap blew out and closed again: bets the way the middle moved when the market makers came back", MakerReturn, {**N, "t_hi": 270, "t_lo": 30, "band": (0.15, 0.85)}),
    "new2_18_big_print": ("NEW2 18: someone bought through 3c of offers in two seconds (or sold through bids): follows them", BigPrint, {**N, "t_hi": 270, "t_lo": 30, "band": (0.15, 0.85)}),
    "new2_19_fee_router": ("NEW2 19: the model's side at a 3c edge, choosing how to get in: takes when the fee is under a cent, otherwise rests a cent inside the gap first", FeeRouter, {}),
    "new2_20_coin_out_of_line": ("NEW2 20: the four coins check each other: the coin whose price is 5c out of line with the other coins' is bet back toward them", OutlierCoin, {**N, "t_hi": 220, "t_lo": 45, "band": (0.15, 0.85)}),
}
LABELS = {k: v[0] for k, v in BOTS.items()}


def build_new2_registry(engine=None):
    def make(k, cls, kw):
        def f(acct):
            if issubclass(cls, Picker): return cls(acct, k, engine=engine, **kw)
            return cls(acct, k, **kw)
        return f
    return {k: make(k, cls, kw) for k, (_, cls, kw) in BOTS.items()}
