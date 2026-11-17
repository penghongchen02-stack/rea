"""Fifty numbered versions of fast_pickoff (user, 11-15 09:40Z), to see which part of it is the profit. Builder
source "fp"; names start with "fp_01" ... "fp_50". Thirty-five Bitcoin-only versions ("fpb_01" ... "fpb_35", 11-17) follow them.

fast_pickoff (paperbot/fastpick.py) was +$665 after 1,429 windows, but its copies with one setting changed have gone
both ways (half-second look +$179, cheap side only -$16, 15-minute -$56). Each version here is fast_pickoff with one
thing changed, so a version that beats number 1 points at that one thing. Number 1 changes nothing and is the
yardstick: every version sees the same jumps in the same windows at the same moment.

Three things are the same for all fifty and differ from the original:
  - the bet is a fixed share of the bank (the average of the dashboard's 0.1%-2% range, about $6.30 on $1,000), not a
    random draw from that range, so two versions differ by their rule and not by the luck of the draw;
  - the tryout's losing-coin rule leaves them alone (number 50 is the exception), so a version keeps the coins it
    was built with for its whole record;
  - each has its own $1,000 and its own view of the book: one version's fill takes nothing from another's.

Versions 43-46 are the test of whether the profit is real: the original books its fill at the quote it saw at the
moment of the jump. 43 fills only if that quote is still there on the engine's next pass (up to a second later), 44
and 45 pay one and two cents more, 46 does both. If the profit is only in the fills no real order could get, these
four show it.
"""
import math, collections
from .fastpick import FastPick

ALL = ("btc", "eth", "sol", "xrp")


class Pick(FastPick):
    """fast_pickoff with switches. Defaults are the original's rule."""
    MULT = 1.0             # bet size against the standard bet
    MIN_PRICE = 0.0        # the side must cost at least this
    SIDES = ("buy", "sell")
    STILL = False          # book the fill only if the quote is still there when the engine books it
    SLIP = 0.0             # pay this much more than the quote seen
    SHOTS = 1; GAP = 30.0  # bets a window, and the seconds between them
    FADE = None            # only against a move of the last quarter hour of FADE times its usual size (negative: only with it)
    CONFIRM = None         # the coin must also have moved the same way over this many seconds
    EXIT = True            # the protective exit of the original
    def __init__(self, account, engine, name):
        super().__init__(account, engine); self.name = name; self.shots = collections.Counter(); self.last = {}

    def dollars(self, acct):
        if acct.size_mode != "pct": return self.ORDER_USD * self.MULT
        lo, hi = acct.size_pct; b = acct.bank()
        return min(b * (hi - lo) / math.log(hi / lo) * self.MULT, acct.window_pct * b)

    def allow(self, market, side, ts):
        if side not in self.SIDES: return False
        if (market.best_ask if side == "buy" else 1 - market.best_bid) < self.MIN_PRICE: return False
        if self.CONFIRM:
            tk = self.ticks[market.asset]; then = None
            for t, p in tk:
                if t <= ts - self.CONFIRM: then = p
                else: break
            if then is None or (tk[-1][1] > then) != (side == "buy"): return False
        if self.FADE is not None:
            sp = self.engine.spot; a = sp.sec(market.asset, market.start - 900); b = sp.sec(market.asset, market.start)
            if not a or not b: return False
            mv = (b / a - 1) / max(sp.sigma(market.asset) * 900 ** 0.5, 1e-12)
            if abs(mv) < abs(self.FADE): return False
            against = (mv < 0) == (side == "buy")
            if against != (self.FADE > 0): return False
        return True

    def book(self, market, side, px, q, now):
        if self.STILL:
            if side == "buy" and (market.best_ask is None or market.best_ask > px + 1e-9): return None
            if side == "sell" and (market.best_bid is None or market.best_bid < px - 1e-9): return None
        if self.SLIP: px = round(min(0.99, px + self.SLIP) if side == "buy" else max(0.01, px - self.SLIP), 2)
        self.shots[market.slug] += 1; self.last[market.slug] = now
        return px, q

    def manage(self, market, ctx, now):
        if self.EXIT: super().manage(market, ctx, now)

    def on_tick(self, market, ctx, now):
        super().on_tick(market, ctx, now)
        s = market.slug
        if self.SHOTS > 1 and s in self.done and 0 < self.shots[s] < self.SHOTS and now - self.last.get(s, now) > self.GAP: self.done.discard(s)


# number: (short name, what is changed, the settings)
V = [
    ("exact_copy", "nothing changed: the yardstick for the other 49", {}),
    ("jump_1_5", "acts on smaller jumps: 1.5 times the usual move instead of 2.5", {"JUMP_SD": 1.5}),
    ("jump_2_0", "acts on jumps of 2 times the usual move", {"JUMP_SD": 2.0}),
    ("jump_3_0", "only jumps of 3 times the usual move", {"JUMP_SD": 3.0}),
    ("jump_3_5", "only jumps of 3.5 times the usual move", {"JUMP_SD": 3.5}),
    ("jump_4_5", "only jumps of 4.5 times the usual move", {"JUMP_SD": 4.5}),
    ("look_half_second", "measures the jump over half a second instead of one", {"LOOK": 0.5}),
    ("look_2_seconds", "measures the jump over 2 seconds", {"LOOK": 2.0}),
    ("look_3_seconds", "measures the jump over 3 seconds", {"LOOK": 3.0}),
    ("edge_0c", "takes any edge above zero after the fee instead of 2c", {"EDGE": 0.0}),
    ("edge_1c", "needs a 1c edge after the fee", {"EDGE": 0.01}),
    ("edge_3c", "needs a 3c edge after the fee", {"EDGE": 0.03}),
    ("edge_5c", "needs a 5c edge after the fee", {"EDGE": 0.05}),
    ("edge_8c", "needs an 8c edge after the fee", {"EDGE": 0.08}),
    ("price_up_to_40c", "only buys a side that costs 40c or less", {"MAX_PRICE": 0.40}),
    ("price_up_to_25c", "only buys a side that costs 25c or less", {"MAX_PRICE": 0.25}),
    ("price_40_to_60c", "only buys a side at 40-60c", {"MIN_PRICE": 0.40, "MAX_PRICE": 0.60}),
    ("price_60_to_90c", "only buys a side at 60-90c", {"MIN_PRICE": 0.60}),
    ("price_up_to_97c", "may pay up to 97c instead of 90c", {"MAX_PRICE": 0.97}),
    ("skip_first_minute", "no bets in a window's first minute", {"LEFT": (20, 240)}),
    ("skip_last_minute", "no bets in a window's last minute", {"LEFT": (60, 285)}),
    ("middle_three_minutes", "no bets in the first or the last minute", {"LEFT": (60, 240)}),
    ("first_half_only", "bets only in the first half of a window", {"LEFT": (150, 285)}),
    ("second_half_only", "bets only in the second half of a window", {"LEFT": (20, 150)}),
    ("last_minute_only", "bets only in the last minute", {"LEFT": (5, 60)}),
    ("quote_not_moved_at_all", "the quote must not have moved at all since before the jump (the original allows 1c)", {"STALE": 0.001}),
    ("quote_moved_up_to_3c", "the quote may already have moved 3c", {"STALE": 0.03}),
    ("quote_may_have_moved", "does not ask whether the quote has moved", {"STALE": 1.0}),
    ("bitcoin_only", "Bitcoin only", {"ASSETS": ("btc",)}),
    ("ether_only", "Ether only", {"ASSETS": ("eth",)}),
    ("solana_only", "Solana only", {"ASSETS": ("sol",)}),
    ("xrp_only", "XRP only", {"ASSETS": ("xrp",)}),
    ("bitcoin_and_solana", "Bitcoin and Solana only", {"ASSETS": ("btc", "sol")}),
    ("bitcoin_and_ether", "Bitcoin and Ether only", {"ASSETS": ("btc", "eth")}),
    ("15_minute_windows", "15-minute windows instead of 5-minute", {"DUR": 900, "LEFT": (60, 855)}),
    ("up_only", "only Up bets (after an upward jump)", {"SIDES": ("buy",)}),
    ("down_only", "only Down bets (after a downward jump)", {"SIDES": ("sell",)}),
    ("half_size", "half the bet size", {"MULT": 0.5}),
    ("double_size", "twice the bet size", {"MULT": 2.0}),
    ("no_exit", "never sells out early: every bet is held to the close", {"EXIT": False}),
    ("exit_sooner", "sells out sooner when the result turns doubtful (confidence under 1.5 instead of 1)", {"EXIT_Z": 1.5}),
    ("exit_later", "sells out later (confidence under 0.5)", {"EXIT_Z": 0.5}),
    ("quote_must_still_be_there", "REALISM: the bet counts only if the quote is still there up to a second later", {"STILL": True}),
    ("pays_1c_more", "REALISM: pays 1c more than the quote it saw", {"SLIP": 0.01}),
    ("pays_2c_more", "REALISM: pays 2c more than the quote it saw", {"SLIP": 0.02}),
    ("still_there_and_1c_more", "REALISM: the quote must still be there and it pays 1c more", {"STILL": True, "SLIP": 0.01}),
    ("against_the_quarter_hour", "only jumps against the coin's move of the last 15 minutes (when that was its usual size or more)", {"FADE": 1.0}),
    ("with_the_quarter_hour", "only jumps the same way as the coin's move of the last 15 minutes", {"FADE": -1.0}),
    ("two_bets_a_window", "up to two bets a window, 30 seconds apart, instead of one", {"SHOTS": 2}),
    ("obeys_the_coin_rule", "nothing changed, but the tryout's losing-coin rule may switch its coins off", {"COIN_RULE": True}),
]
assert len(V) == 50
BOTS = {f"fp_{i:02d}_{name}": (f"FAST PICKOFF {i}: {what}", attrs) for i, (name, what, attrs) in enumerate(V, 1)}


# Thirty-five Bitcoin-only versions (user, 11-17 09:20Z): names "fpb_01" ... "fpb_35". On the tryout Bitcoin made
# two thirds of fast_pickoff's profit (51% of windows won against 23-29% on the other coins), and the Bitcoin part of
# the realism versions was still ahead. Each version here is the Bitcoin-only bot (fp_29) with one thing changed;
# number 1 changes nothing and is the yardstick. 30 and 31 join the changes that were furthest ahead; 32-35 are the
# realism tests.
B = [
    ("exact_copy", "nothing changed: the yardstick for the other 34", {}),
    ("jump_2_0", "acts on jumps of 2 times the usual move instead of 2.5", {"JUMP_SD": 2.0}),
    ("jump_3_0", "only jumps of 3 times the usual move", {"JUMP_SD": 3.0}),
    ("jump_3_5", "only jumps of 3.5 times the usual move", {"JUMP_SD": 3.5}),
    ("jump_4_5", "only jumps of 4.5 times the usual move", {"JUMP_SD": 4.5}),
    ("look_half_second", "measures the jump over half a second instead of one", {"LOOK": 0.5}),
    ("look_2_seconds", "measures the jump over 2 seconds", {"LOOK": 2.0}),
    ("edge_1c", "needs a 1c edge after the fee instead of 2c", {"EDGE": 0.01}),
    ("edge_3c", "needs a 3c edge after the fee", {"EDGE": 0.03}),
    ("edge_5c", "needs a 5c edge after the fee", {"EDGE": 0.05}),
    ("edge_8c", "needs an 8c edge after the fee", {"EDGE": 0.08}),
    ("price_up_to_40c", "only buys a side that costs 40c or less", {"MAX_PRICE": 0.40}),
    ("price_40_to_60c", "only buys a side at 40-60c", {"MIN_PRICE": 0.40, "MAX_PRICE": 0.60}),
    ("price_60_to_90c", "only buys a side at 60-90c", {"MIN_PRICE": 0.60}),
    ("price_up_to_97c", "may pay up to 97c instead of 90c", {"MAX_PRICE": 0.97}),
    ("skip_first_minute", "no bets in a window's first minute", {"LEFT": (20, 240)}),
    ("skip_last_minute", "no bets in a window's last minute", {"LEFT": (60, 285)}),
    ("first_half_only", "bets only in the first half of a window", {"LEFT": (150, 285)}),
    ("second_half_only", "bets only in the second half of a window", {"LEFT": (20, 150)}),
    ("last_minute_only", "bets only in the last minute", {"LEFT": (5, 60)}),
    ("quote_not_moved_at_all", "the quote must not have moved at all since before the jump (the original allows 1c)", {"STALE": 0.001}),
    ("quote_moved_up_to_3c", "the quote may already have moved 3c", {"STALE": 0.03}),
    ("15_minute_windows", "15-minute windows instead of 5-minute", {"DUR": 900, "LEFT": (60, 855)}),
    ("up_only", "only Up bets (after an upward jump)", {"SIDES": ("buy",)}),
    ("down_only", "only Down bets (after a downward jump)", {"SIDES": ("sell",)}),
    ("double_size", "twice the bet size", {"MULT": 2.0}),
    ("no_exit", "never sells out early: every bet is held to the close", {"EXIT": False}),
    ("exit_later", "sells out later (confidence under 0.5 instead of 1)", {"EXIT_Z": 0.5}),
    ("two_bets_a_window", "up to two bets a window, 30 seconds apart, instead of one", {"SHOTS": 2}),
    ("no_exit_quote_not_moved", "never sells out early, and the quote must not have moved at all", {"EXIT": False, "STALE": 0.001}),
    ("no_exit_half_second", "never sells out early, and measures the jump over half a second", {"EXIT": False, "LOOK": 0.5}),
    ("quote_must_still_be_there", "REALISM: the bet counts only if the quote is still there up to a second later", {"STILL": True}),
    ("pays_1c_more", "REALISM: pays 1c more than the quote it saw", {"SLIP": 0.01}),
    ("pays_2c_more", "REALISM: pays 2c more than the quote it saw", {"SLIP": 0.02}),
    ("still_there_and_1c_more", "REALISM: the quote must still be there and it pays 1c more", {"STILL": True, "SLIP": 0.01}),
]
assert len(B) == 35
BOTS.update({f"fpb_{i:02d}_{name}": (f"BITCOIN PICKOFF {i}: {what}", {"ASSETS": ("btc",), **attrs}) for i, (name, what, attrs) in enumerate(B, 1)})
LABELS = {k: v[0] for k, v in BOTS.items()}
KEEPS_COINS = {k: not v[1].get("COIN_RULE") for k, v in BOTS.items()}


def build_fp_registry(engine=None):
    def make(k, attrs):
        cls = type("Pick_" + k.rsplit("_", k.count("_") - 1)[0], (Pick,), dict(attrs))
        return lambda acct: cls(acct, engine, k)
    return {k: make(k, attrs) for k, (_, attrs) in BOTS.items()}
