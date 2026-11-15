"""Twenty tryout bots that read CF Benchmarks instead of Coinbase (user, 11-15 17:35Z). Builder source "cf"; names
"cf_01" ... "cf_20".

Every other bot here takes the coin's price from Coinbase's trade feed, which shows a move up to about a second before
the benchmark does. These twenty never see Coinbase: the price model, the jump test and every rule read only the
benchmark that Polymarket US publishes and settles on. What is published is the rolling 60-second average of the
benchmark, one value a second, so on top of the publishing delay it follows a move by about half a minute; a
"jump" of it is the price now against the price a minute ago, not a fresh move. Nothing faster is available without a
second source. The point of the group is to show what is left of each method when Coinbase's head start is taken away.

1-8 are fast_pickoff's rule (paperbot/fastpicks.py) driven by the benchmark; 9-20 are methods that were ahead in
the tryout or TOT, unchanged except for the price source.
"""
import time, math, threading, logging
from .fair import phi
from .fastpicks import Pick
from .totbots import pk, nc, N, TC, BE
from .newbots import Picker
from .newbots2 import TickCount, Bandit, Kelly
from .newbots4 import Pinned
from .newbots5 import Rules, Leader, Against

log = logging.getLogger("cf")


class CFSpot:
    """feeds.SpotFeed's interface served from the benchmark series alone (feeds.IndexFeed)."""
    def __init__(self, idx):
        self.idx = idx; self.listeners = []; self.connected = True; self.msgs = 0
        self.seen = {}; self.got = {}; self.off = {}; self._sig = {}
        threading.Thread(target=self.run, daemon=True, name="cf-spot").start()

    def run(self):
        while True:
            try:
                for a in self.idx.assets:
                    its, v = self.idx.last(a)
                    if not its or its == self.seen.get(a): continue
                    now = time.time(); self.seen[a] = its; self.got[a] = now; self.msgs += 1
                    # listeners get the value's own second moved onto this clock, so consecutive values are exactly one
                    # second apart however unevenly the polls deliver them
                    o = self.off.get(a); d = now - its; self.off[a] = d if o is None or d < o or d - o > 5 else o
                    for fn in list(self.listeners):
                        try: fn(a, v, its + self.off[a])
                        except Exception as e: log.warning("cf listener: %s", e)
            except Exception as e: log.warning("cf spot: %s", e)
            time.sleep(0.1)

    def last(self, a):
        its, v = self.idx.last(a)
        return (v, self.got.get(a) or its) if v else (None, None)

    def sec(self, a, ts):
        for t in range(int(ts), int(ts) - 15, -1):
            v = self.idx.value_at(a, t)
            if v is not None: return v
        return None
    at = sec

    def mean(self, a, t0, t1):
        vals = [v for v in (self.sec(a, t) for t in range(int(t0), int(t1))) if v is not None]
        return sum(vals) / len(vals) if vals else None

    def step(self, a):
        """The usual size of the published value's one-second change, as a fraction (cached 10 s; None if unknown)."""
        now = time.time(); hit = self._sig.get(a)
        if hit and now - hit[0] < 10: return hit[1]
        with self.idx.lock: items = list((self.idx.series.get(a) or {}).items())[-1200:]
        r = [math.log(items[i][1] / items[i - 1][1]) for i in range(1, len(items)) if items[i][0] - items[i - 1][0] == 1 and items[i - 1][1] > 0]
        val = None
        if len(r) >= 60:
            m = sum(r) / len(r); val = max(math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1)), 1e-7)
        self._sig[a] = (now, val); return val

    def sigma(self, a, seconds=1200):
        """The raw price's per-second volatility implied by the average's steps (a 60-second average moves 1/sqrt(60)
        as much a second as the price under it); 5e-5 when unknown."""
        s = self.step(a)
        return max(s * math.sqrt(60), 1e-5) if s else 5e-5


class CFEngine:
    """The engine as these bots see it: everything the same, but .spot is the benchmark."""
    def __init__(self, engine): self._e = engine; self.spot = CFSpot(engine.idx)
    def __getattr__(self, k): return getattr(self._e, k)


def view(engine):
    if isinstance(engine, CFEngine): return engine
    v = engine.__dict__.get("_cf_view")
    if v is None: v = engine.__dict__["_cf_view"] = CFEngine(engine)
    return v


class CFPick(Pick):
    """fast_pickoff's rule on the benchmark: LOOK is in published values (0.9 = against the value one second
    before), and a jump is measured against the usual change of the published value over that many seconds."""
    LOOK = 0.9
    def jump_unit(self, asset):
        s = self.engine.spot.step(asset)
        return (s or 1e9) * round(self.LOOK + 0.1)


def cfpick(**attrs):
    def f(a, e, name): return type("CFPick_", (CFPick,), dict(attrs))(a, e, name)
    return lambda a, e, name: on_cf(f)(a, e, name)      # its protective exit must read the benchmark too


class CFSettle(Pick):
    """A pickoff made for the benchmark (user, 11-15 22:40Z). The benchmark cannot be first to see a move, but it is
    the thing the window settles on: the published value is the 60-second average, and its value one second before
    the close is the settlement. Late in a window that value can only move a little more, so on every new published
    value this bot works out where it will end and takes a quote that still prices the other outcome.

    The estimate: the published value, plus its latest one-second change carried forward and fading (if the price
    under it stays where it is, each coming second moves the average a little less). The doubt around it is the
    coin's volatility over the r seconds still to come, doubled for the path the average is still dropping. It
    bets when the settlement is Z of those doubts past the window's line, the side costs no more than MAX_PRICE and
    the edge after the fee is at least EDGE. One bet a window, held to the close."""
    LEFT = (2, 30); Z = 3.0; MAX_PRICE = 0.95; EDGE = 0.02; TREND = True; EXIT = False
    def __init__(self, account, engine, name): super().__init__(account, engine, name); self.prev = {}

    def on_spot(self, asset, px, ts):
        t0 = time.perf_counter()
        if asset not in self.ticks: return
        self.stats["ticks"] += 1; sp = self.engine.spot; its = sp.seen.get(asset); last = self.prev.get(asset); self.prev[asset] = (its, px)
        if its is None: return
        d = px - last[1] if last and its - last[0] == 1 else 0.0
        sig = sp.sigma(asset) * px; acct = self.acct; th = acct.theta
        for m in self.windows(asset, ts):
            bb, ba, K = m.best_bid, m.best_ask, m.p2b
            if bb is None or ba is None or K is None or m.slug in self.done: continue
            left = m.end - ts
            if not (self.LEFT[0] <= left <= self.LEFT[1]) or ts - m.book_ts > 3 or m.state not in (None, "MARKET_STATE_OPEN"): continue
            r = max(0, int(m.end - 1 - its))
            est = px + (d * (r - r * (r + 1) / 120.0) if self.TREND else 0.0)
            sd = sig * math.sqrt(2 * r ** 3 / 10800.0) + 2 * 10 ** (-getattr(m, "decimals", 2))
            z = (est - K) / sd; p = phi(z)
            if z >= self.Z and ba <= self.MAX_PRICE and p - ba - th * ba * (1 - ba) > self.EDGE: side, lim, unit = "buy", ba, ba
            elif z <= -self.Z and 1 - bb <= self.MAX_PRICE and (1 - p) - (1 - bb) - th * bb * (1 - bb) > self.EDGE: side, lim, unit = "sell", bb, 1 - bb
            else: continue
            if not self.allow(m, side, ts): continue
            lv = [x for x in m.avail(side, acct.liq_key) if x[0] == lim]
            q = round(min(self.dollars(acct) / max(unit, 0.01), lv[0][1] if lv else 0.0), 2)
            if q < m.min_qty: self.stats["no_size"] += 1; continue
            k = (acct.liq_key, side, lim); m.consumed[k] = m.consumed.get(k, 0.0) + q
            ms = (time.perf_counter() - t0) * 1000.0
            self.done.add(m.slug); self.lat.append(ms); self.stats["signals"] += 1
            self.queue.append((m.slug, side, lim, q, ts, f"cf-settle-{'up' if side == 'buy' else 'down'} z{z:+.1f} r{r}"))
        self.stats["us_total"] += int((time.perf_counter() - t0) * 1e6)


def settle(**attrs):
    def f(a, e, name): return type("CFSettle_", (CFSettle,), dict(attrs))(a, e, name)
    return lambda a, e, name: on_cf(f)(a, e, name)


def on_cf(f):
    """Build the bot against the benchmark view and hand it the benchmark as its price on every pass."""
    def g(a, e, name):
        bot = f(a, e, name); tick = bot.on_tick
        bot.on_tick = lambda market, ctx, now: tick(market, {"index": ctx["index"], "spot": e.spot}, now)
        return bot
    return g


def cf_only(f, e):
    """Any builder turned into a benchmark-only one, for the cfbenchmark dashboard (user, 11-15 17:50Z). The builder
    must have been made with the benchmark view `e` as its engine. Its bot gets the benchmark as the price on every
    pass; a fast_pickoff kind is moved onto the benchmark's one value a second (a look of under a second becomes one
    second, the shortest there is) and measures its jump against the benchmark's own usual change."""
    from .fastpick import FastPick
    def g(acct):
        bot = f(acct); tick = bot.on_tick
        bot.on_tick = lambda market, ctx, now: tick(market, {"index": ctx["index"], "spot": e.spot}, now)
        if isinstance(bot, FastPick) and not isinstance(bot, CFPick):
            n = max(1, round(bot.LOOK)); bot.LOOK = n - 0.1
            bot.jump_unit = lambda asset: (e.spot.step(asset) or 1e9) * n
        return bot
    return g


V = [
    # ---- fast_pickoff's rule, benchmark only ----
    ("pickoff", "fast_pickoff's rule on the benchmark: a jump of the published value against one second before, 2.5 times its usual change, the quote not moved", cfpick()),
    ("pickoff_3_second_look", "the jump measured over 3 seconds", cfpick(LOOK=2.9)),
    ("pickoff_5_second_look", "the jump measured over 5 seconds", cfpick(LOOK=4.9)),
    ("pickoff_big_jump", "only jumps of 3.5 times the usual change", cfpick(JUMP_SD=3.5)),
    ("pickoff_small_jump", "jumps of 1.5 times the usual change", cfpick(JUMP_SD=1.5)),
    ("pickoff_5c_edge", "needs a 5c edge after the fee", cfpick(EDGE=0.05)),
    ("pickoff_bitcoin_and_ether", "Bitcoin and Ether only", cfpick(ASSETS=("btc", "eth"))),
    ("pickoff_quote_not_moved_at_all", "the quote must not have moved at all in the last second", cfpick(STALE=0.001)),
    # ---- other methods that were ahead, benchmark only ----
    ("tick_count", "72% of the benchmark's last 30 changes went one way: bets that way at 25-75c", on_cf(pk(TickCount, **N, **TC))),
    ("tick_count_80_percent", "the tick count needing 80%", on_cf(pk(TickCount, {"SHARE": 0.80}, **N, **TC))),
    ("one_buy_mid_window", "the model's side with a 3c edge, one buy in the middle half of a 5-minute window", on_cf(pk(Picker, edge=0.03, t_hi=225, t_lo=75, band=(0.45, 0.75)))),
    ("one_buy_mid_window_5c_edge", "the same, needing 5c", on_cf(pk(Picker, edge=0.05, t_hi=225, t_lo=75, band=(0.45, 0.75)))),
    ("one_buy_mid_window_15_minute", "the model's side with a 3c edge, one buy in the middle half of a 15-minute window", on_cf(pk(Picker, edge=0.03, durs=(900,), t_hi=675, t_lo=225, band=(0.45, 0.75)))),
    ("all_the_rules", "the side ahead at 50-70c in the second half, never with a big move of the last quarter hour", on_cf(pk(Rules, **N, t_hi=150, t_lo=30, band=(0.50, 0.70)))),
    ("side_ahead_at_50_to_70c", "the side that is ahead at 50-70c, any time after the first quarter", on_cf(pk(Leader, **N, t_hi=225, t_lo=30, band=(0.50, 0.70)))),
    ("pinned_result_97_percent", "last 40 seconds, the model is 97% sure from the benchmark already published: takes that side at 75-96c", on_cf(pk(Pinned, {"SURE": 0.97}, edge=0.0, t_hi=40, t_lo=4, band=(0.75, 0.96)))),
    ("near_certain_last_two_minutes", "one resting bid at 90-98c on the near-certain side in the last two minutes, four coins", on_cf(nc(el=180))),
    ("bet_by_the_odds_4c_edge", "the model's side at a 4c edge, sized by the odds (quarter Kelly)", on_cf(pk(Kelly, edge=0.04, band=(0.10, 0.90)))),
    ("learner", "four simple rules vote each window; follows whichever has been right most lately", on_cf(pk(Bandit, **N, t_hi=200, t_lo=60, band=(0.25, 0.75)))),
    ("against_the_quarter_hour", "against a big move of the last 15 minutes, only while that side is not behind, at 40-65c", on_cf(pk(Against, **N, t_hi=225, t_lo=30, band=(0.40, 0.65)))),
]
assert len(V) == 20
BOTS = {f"cf_{i:02d}_{name}": (f"CF BENCHMARK {i}: {what} (no Coinbase)", f) for i, (name, what, f) in enumerate(V, 1)}
LABELS = {k: v[0] for k, v in BOTS.items()}


# Twenty versions of the benchmark's own pickoff, for the cfbenchmark dashboard (user, 11-15 22:40Z): names "cfp_01" ... "cfp_20".
P = [
    ("settles_past_the_line", "last 30 seconds: the benchmark is set to settle 3 doubts past the line and the quote still pays 2c after the fee, at up to 95c", settle()),
    ("last_20_seconds", "last 20 seconds only", settle(LEFT=(2, 20))),
    ("last_10_seconds", "last 10 seconds only", settle(LEFT=(2, 10))),
    ("last_45_seconds", "from 45 seconds out", settle(LEFT=(2, 45))),
    ("last_60_seconds", "from 60 seconds out", settle(LEFT=(2, 60))),
    ("less_sure", "needs only 2 doubts past the line", settle(Z=2.0)),
    ("more_sure", "needs 4 doubts past the line", settle(Z=4.0)),
    ("most_sure", "needs 5 doubts past the line", settle(Z=5.0)),
    ("no_carry_forward", "takes the published value as it stands, without carrying its last change forward", settle(TREND=False)),
    ("up_to_90c", "pays no more than 90c", settle(MAX_PRICE=0.90)),
    ("up_to_97c", "pays up to 97c", settle(MAX_PRICE=0.97)),
    ("up_to_98c", "pays up to 98c", settle(MAX_PRICE=0.98)),
    ("edge_1c", "needs a 1c edge after the fee", settle(EDGE=0.01)),
    ("edge_4c", "needs a 4c edge after the fee", settle(EDGE=0.04)),
    ("bitcoin_only", "Bitcoin only", settle(ASSETS=("btc",))),
    ("bitcoin_and_ether", "Bitcoin and Ether only", settle(ASSETS=("btc", "eth"))),
    ("solana_and_xrp", "Solana and XRP only", settle(ASSETS=("sol", "xrp"))),
    ("15_minute_windows", "15-minute windows", settle(DUR=900)),
    ("quote_must_still_be_there", "REALISM: the bet counts only if the quote is still there up to a second later", settle(STILL=True)),
    ("pays_1c_more", "REALISM: pays 1c more than the quote it saw", settle(SLIP=0.01)),
]
assert len(P) == 20
BOTS.update({f"cfp_{i:02d}_{name}": (f"CF PICKOFF {i}: {what} (no Coinbase)", f) for i, (name, what, f) in enumerate(P, 1)})
LABELS = {k: v[0] for k, v in BOTS.items()}


def build_cf_registry(engine=None):
    def make(k, f): return lambda acct: f(acct, view(engine), k)
    return {k: make(k, f) for k, (_, f) in BOTS.items()}
