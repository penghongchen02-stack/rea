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
    return f


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


def build_cf_registry(engine=None):
    def make(k, f): return lambda acct: f(acct, view(engine), k)
    return {k: make(k, f) for k, (_, f) in BOTS.items()}
