"""br177 with one improvement each, for the tryout (user, 11-12 08:35Z). Every variant is the Bonereaper-lab bot br177
(BTC and ETH 5-minute windows, a side priced 40-60c with 2-5 minutes left and a 3c edge after fees, random $0.10-$100
orders up to $100 a window, held to settlement with a protective exit) with exactly one change, the same random seed
as the lab's br177 (so order sizes match and only the change differs), and a plain copy to compare against. Names say
what changed. The champion's br177 is not touched.
"""
import math, collections, statistics
from .bonelab import BoneVar, VARIANTS
from .fair import p_up

_KW = VARIANTS["br177"][1]; _SEED = 100 + list(VARIANTS).index("br177")
STEADY_USD = 10.0; STEADY_CAP = 40.0      # the steady copy: every order $10, at most $40 in one window
COLD_N = 8; COLD_WINS = 2; COLD_SIZE = 0.25; BANK_FRAC = 0.05; DAY_STOP = 0.10; GIVEBACK = 0.5; QUIET = 0.7


class _Maker:
    """Account wrapper for the resting-orders variant: an entry becomes one post-only order at the touch (a maker fill
    pays no fee), replacing the one resting in that window; exits and everything else go to the real account."""
    def __init__(self, acct): object.__setattr__(self, "_a", acct)
    def __getattr__(self, k): return getattr(self._a, k)
    def __setattr__(self, k, v): setattr(self._a, k, v)
    def take(self, market, side, qty, limit=None, tag="", fixed=False):
        if tag.startswith("lc-exit"): return self._a.take(market, side, qty, limit=limit, tag=tag, fixed=fixed)
        px = market.best_bid if side == "buy" else market.best_ask
        if px is None: return None
        self._a.cancel_all(market.slug)
        return self._a.place(market, side, px, qty, post_only=True, tag=tag + " rest", fixed=fixed)


class Br177Var(BoneVar):
    """mode: None (plain) or one of cold, cap, signal, btc, daystop, giveback, quiet, noexit, maker, blend, trend,
    balance, trend_noexit (the lab's direction guards, bonelab.BoneVar, one at a time), trend_noexit_steady (trend,
    held to the close, and fixed $10 orders up to $40 a window instead of random sizes)."""
    def __init__(self, account, name, mode=None):
        kw = dict(_KW)
        if mode == "btc": kw["assets"] = ("btc",)
        if mode == "blend": kw["shrink"] = 0.8                    # 80% model, 20% the market's own price
        super().__init__(account, name, seed=_SEED, **kw)
        self.mode = mode; self.base_cap = self.cap if mode != "trend_noexit_steady" else STEADY_CAP; self._mult = 1.0; self._edge = None
        self._sig = collections.defaultdict(lambda: collections.deque(maxlen=900))
        self._decided = True
        self.trend = mode in ("trend", "trend_noexit", "trend_noexit_steady", "guards"); self.balance = mode in ("balance", "guards")
        self.hold = mode in ("noexit", "trend_noexit", "trend_noexit_steady", "guards")       # the lab's guards, one at a time here
        if mode == "maker": self.acct = _Maker(account)

    # ---- what the account's own settled windows say (known at entry) ------------
    def day(self, now):
        """(running total today, lowest point, highest point) of settled P/L, from the account's records."""
        day0 = now - now % 86400; run = low = high = 0.0
        for r in self.acct.settlements:
            if r.get("ts", 0) >= day0: run += r.get("pnl_total", 0.0) - r.get("fees", 0.0); low = min(low, run); high = max(high, run)   # after fees
        return run, low, high

    def multiplier(self, market, det, now):
        a = self.acct; m = self.mode
        if m == "cold":
            last = [r for r in a.settlements if r.get("fills")][-COLD_N:]
            return COLD_SIZE if len(last) >= COLD_N and sum(1 for r in last if r["pnl_total"] - r.get("fees", 0.0) > 0) <= COLD_WINS else 1.0
        if m == "daystop": return 0.0 if self.day(now)[1] <= -DAY_STOP * a.start_cash else 1.0
        if m == "giveback":
            run, _, high = self.day(now)
            return COLD_SIZE if high >= 0.05 * a.start_cash and run <= high * (1 - GIVEBACK) else 1.0
        if m == "quiet":
            s = det.get("sigma_1s") or 0.0; q = self._sig[market.asset]
            if s > 0: q.append(s)
            return 0.0 if len(q) >= 120 and s < QUIET * statistics.median(q) else 1.0
        return 1.0

    def contracts(self, price):
        n = super().contracts(price)                       # draws the same random size as plain br177
        if self.mode == "signal" and self._edge is not None:
            n = min(100.0, 15.0 * self._edge / 0.05) / price      # $9 at a 3c edge, $30 at 10c, capped at $100
        if self.mode == "trend_noexit_steady": n = STEADY_USD / price
        return n * self._mult

    def on_tick(self, market, ctx, now):
        if market.asset in self.assets and market.dur in self.durs:
            p, det = p_up(market, ctx["index"], ctx["spot"], now)
            if p is not None and market.best_bid is not None and market.best_ask is not None:
                bb, ba = market.best_bid, market.best_ask; th = self.acct.theta
                if self.shrink < 1.0: p = self.shrink * p + (1 - self.shrink) * (bb + ba) / 2
                self._edge = max(p - ba - th * ba * (1 - ba), (1 - p) - (1 - bb) - th * bb * (1 - bb))
                self._mult = self.multiplier(market, det, now)
            cap = self.base_cap
            if self.mode == "cap": cap = min(cap, BANK_FRAC * self.acct.cash)
            self.cap = cap * (self._mult if self._mult > 0 else 1.0)
        super().on_tick(market, ctx, now)


VARS = {
    "br177_plain": (None, "br177 unchanged, to compare the others against"),
    "br177_quarter_size_bets_during_a_cold_streak": ("cold", "br177, quarter-size bets after winning 2 or fewer of its last 8 windows"),
    "br177_cap_5pct_of_bank_per_window": ("cap", "br177, at most 5% of the bank in one window"),
    "br177_bet_more_on_stronger_signals": ("signal", "br177, bet size follows the edge instead of a random amount"),
    "br177_btc_only": ("btc", "br177, Bitcoin windows only"),
    "br177_stop_for_the_day_after_losing_10pct": ("daystop", "br177, no new bets for the rest of the UTC day after losing 10% of the bank"),
    "br177_cut_size_after_giving_back_half_the_gains": ("giveback", "br177, quarter-size bets once half of the day's peak profit is given back"),
    "br177_skip_quiet_markets": ("quiet", "br177, no bets while the coin's volatility is under 70% of its recent median"),
    "br177_no_early_exit": ("noexit", "br177, every bet held to settlement"),
    "br177_resting_orders": ("maker", "br177, entries rest at the touch (no fee) instead of taking"),
    "br177_blend_odds_with_market_price": ("blend", "br177, odds estimate blended 80/20 with the market's price"),
    "br177_only_bet_with_the_3_minute_trend": ("trend", "br177, Up only while the coin is above its price 3 minutes ago, Down only while below"),
    "br177_no_more_than_3_windows_in_a_row_on_one_side": ("balance", "br177, skips a 4th window in a row on the same side"),
    "br177_hold_to_the_close": ("noexit", "br177, every bet held to settlement (no early exit)"),
    "br177_trend_and_hold_to_the_close": ("trend_noexit", "br177, only with the 3-minute trend and held to settlement"),
    "br177_trend_hold_and_steady_10_dollar_bets": ("trend_noexit_steady", "br177, only with the 3-minute trend, held to settlement, every order $10 and at most $40 a window"),
    "br177_unchanged_for_comparison": (None, "br177 unchanged, run next to the direction fixes"),
}


def build_br177_registry():
    return {k: (lambda acct, k=k, mode=mode: Br177Var(acct, k, mode)) for k, (mode, _) in VARS.items()}
