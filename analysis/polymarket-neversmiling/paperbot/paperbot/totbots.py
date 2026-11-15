"""The fifty bots of the TOT dashboard (user, 11-15 09:40Z; port 8093). Builder source "tot"; names "tot_01" ... "tot_50".

TOT is a second tryout that looks for the best method among the ones the data so far supports. Of the 100 or so bots
tried since 11-12, 23 were ahead on 11-15 09:33Z, and all of them trade one of five mismatches between what can be
known and what the quote says:

  the quote is behind Coinbase   a Coinbase jump the quote has not followed (fast_pickoff +$665, half-second +$179)
  the line is known early        a window's starting line can be worked out before it is published (head start +$202)
  the ticks lean one way         most recent price changes went one way and the quote is mid-priced (tick count +$192)
  the result is nearly decided   most of the settling average has happened and the side still pays (Sharky copy 105
                                 of 109, committee 59 of 61, near-certain 15-minute 7 of 7)
  the longer window              15-minute windows, the cheapest to take in (all the rules +$35, fade the quarter +$37)

Every bot here is one of those methods, as it stands or with one or two settings changed; nothing that lost in the
tryout is repeated unchanged. Each has its own $1,000 and its own view of the book (a fill by one takes nothing from
another), so they do not compete. The records start from zero: a method that was ahead in the tryout has to earn it
again here.
"""
from .fastpicks import Pick
from .newbots import Picker, Committee
from .newbots2 import TickCount, HotCoin, Bandit, OddOneOut, EarlyCashier, Kelly
from .newbots3 import FadeQuarter
from .newbots4 import head, committee, near_certain, Pinned
from .newbots5 import Rules, Against

ALL = ("btc", "eth", "sol", "xrp"); BE = ("btc", "eth"); N = {"edge": None}


def pick(**attrs):
    def f(a, e, name): return type("TotPick", (Pick,), dict(attrs))(a, e, name)
    return f
def hd(durs=None, assets=None, **attrs):
    def f(a, e, name):
        kw = {}
        if durs: kw["durs"] = durs
        if assets: kw["assets"] = assets
        return head(name, **attrs)(a, e, **kw)
    return f
def pk(cls, attrs=None, **kw):
    def f(a, e, name): return (type("Tot" + cls.__name__, (cls,), dict(attrs)) if attrs else cls)(a, name, engine=e, **kw)
    return f
def cm(**kw):
    def f(a, e, name): return committee(name, **kw)(a, e)
    return f
def nc(**kw):
    def f(a, e, name):
        from .newreps import PairMaker
        s = dict(clip=0.02, tilt=0.02, conv=0.43, band=(0.90, 0.98), left=10, flat=0, hold=0.0, only_fav=True, assets=ALL); s.update(kw)
        return PairMaker(a, name, **s)
    return f


TC = dict(t_hi=240, t_lo=45, band=(0.25, 0.75))
# (short name, the mismatch it trades and its settings, builder)
V = [
    # ---- the quote is behind Coinbase ----
    ("stale_quote_half_second", "a Coinbase jump measured over half a second that the quote has not followed", pick(LOOK=0.5)),
    ("stale_quote_half_second_middle_minutes", "the same, never in a window's first or last minute", pick(LOOK=0.5, LEFT=(60, 240))),
    ("stale_quote_middle_minutes", "a one-second jump the quote has not followed, minutes 2 to 4 only", pick(LEFT=(60, 240))),
    ("stale_quote_small_jump_cheap_side", "smaller jumps (2 times the usual move), only a side at 40c or less", pick(JUMP_SD=2.0, MAX_PRICE=0.40)),
    ("stale_quote_big_jump", "only jumps of 3.5 times the usual move", pick(JUMP_SD=3.5)),
    ("stale_quote_quarter_second", "a jump measured over a quarter of a second", pick(LOOK=0.25)),
    ("stale_quote_half_second_big_jump", "half-second jumps of 3 times the usual move", pick(LOOK=0.5, JUMP_SD=3.0)),
    ("stale_quote_half_second_still_there", "half-second jumps, counted only if the quote is still there up to a second later (what a real order could get)", pick(LOOK=0.5, STILL=True)),
    ("stale_quote_half_second_pays_1c_more", "half-second jumps, paying 1c more than the quote seen", pick(LOOK=0.5, SLIP=0.01)),
    ("stale_quote_middle_minutes_two_bets", "minutes 2 to 4, up to two bets a window", pick(LEFT=(60, 240), SHOTS=2)),
    ("stale_quote_half_and_one_second_agree", "a half-second jump that the last three seconds agree with", pick(LOOK=0.5, CONFIRM=3.0)),
    ("stale_quote_half_second_hold", "half-second jumps, every bet held to the close", pick(LOOK=0.5, EXIT=False)),
    # ---- the line is known early ----
    ("head_start", "works out a window's starting line before it is published and buys the side that is 5c too cheap, in the first 12 seconds", hd()),
    ("head_start_first_8_seconds", "the head start, first 8 seconds only, before the exchange shows the line", hd(LAST=8.0)),
    ("head_start_8c_edge", "the head start, needing 8c", hd(EDGE=0.08)),
    ("head_start_first_8_seconds_8c", "the head start, first 8 seconds and 8c", hd(LAST=8.0, EDGE=0.08)),
    ("head_start_bitcoin_and_ether", "the head start on Bitcoin and Ether, the coins it kept in the tryout", hd(assets=BE)),
    ("head_start_first_5_seconds", "the head start, first 5 seconds only", hd(LAST=5.0)),
    ("head_start_3c_edge", "the head start, needing only 3c", hd(EDGE=0.03)),
    ("head_start_12c_edge", "the head start, needing 12c", hd(EDGE=0.12)),
    # ---- the ticks lean one way ----
    ("tick_count", "72% of the last 30 price changes went one way: bets that way at 25-75c", pk(TickCount, **N, **TC)),
    ("tick_count_80_percent", "the tick count needing 80%", pk(TickCount, {"SHARE": 0.80}, **N, **TC)),
    ("tick_count_65_percent", "the tick count needing 65%", pk(TickCount, {"SHARE": 0.65}, **N, **TC)),
    ("tick_count_bitcoin_and_ether", "the tick count on Bitcoin and Ether", pk(TickCount, **N, assets=BE, **TC)),
    ("tick_count_first_half", "the tick count in the first half of a window only", pk(TickCount, **N, t_hi=240, t_lo=150, band=(0.25, 0.75))),
    # ---- the liveliest coin ----
    ("liveliest_coin", "each window only the coin that is liveliest against its own normal, the model's side at a 3c edge", pk(HotCoin)),
    ("liveliest_coin_5c_edge", "the liveliest coin, needing 5c", pk(HotCoin, edge=0.05)),
    ("liveliest_coin_15_minute", "the liveliest coin in 15-minute windows", pk(HotCoin, durs=(900,), t_hi=720, t_lo=180)),
    # ---- the result is nearly decided ----
    ("near_certain_last_two_minutes", "one resting bid at 90-98c on the near-certain side in the last two minutes (the Sharky copy's rule), four coins", nc(el=180)),
    ("near_certain_15_minute", "the same in 15-minute windows", nc(durs=(900,), el=780)),
    ("committee", "bets only when three late-window bots hold the same side", lambda a, e, name: Committee(a, name, engine=e, pos_mult=15.0)),
    ("committee_two_of_three", "the committee needing two of three, none against", cm(need=2)),
    ("committee_four_coins", "the committee on all four coins", cm(assets=ALL)),
    ("near_certain_93c_and_over", "the near-certain bid at 93-98c only", nc(el=180, band=(0.93, 0.98))),
    ("near_certain_last_minute", "the near-certain bid in the last minute only", nc(el=240)),
    ("pinned_result_97_percent", "last 40 seconds, the model is 97% sure because most of the settling average has happened: takes that side at 75-96c", pk(Pinned, {"SURE": 0.97}, edge=0.0, t_hi=40, t_lo=4, band=(0.75, 0.96))),
    # ---- the longer window, and the reversal ----
    ("all_the_rules_15_minute", "15-minute windows: the side ahead at 50-70c in the second half, never with a big move of the last quarter hour", pk(Rules, **N, durs=(900,), t_hi=450, t_lo=90, band=(0.50, 0.70))),
    ("fade_quarter_hour_15_minute", "15-minute windows: against the last quarter hour when it moved its usual size", pk(FadeQuarter, {"BIG": 1.0}, **N, durs=(900,), t_hi=870, t_lo=600, band=(0.35, 0.60))),
    ("one_buy_mid_window", "the model's side with a 3c edge, one buy in the middle half of a 5-minute window", pk(Picker, edge=0.03, t_hi=225, t_lo=75, band=(0.45, 0.75))),
    ("all_the_rules_15_minute_bitcoin_ether", "all the rules, 15-minute, Bitcoin and Ether", pk(Rules, **N, durs=(900,), assets=BE, t_hi=450, t_lo=90, band=(0.50, 0.70))),
    ("fade_big_quarter_hour_15_minute", "15-minute windows: against a quarter hour of 1.5 times its usual size", pk(FadeQuarter, **N, durs=(900,), t_hi=870, t_lo=600, band=(0.35, 0.62))),
    ("against_quarter_hour_not_behind_15_minute", "15-minute windows: against a big quarter hour, only while that side is not behind", pk(Against, **N, durs=(900,), t_hi=675, t_lo=90, band=(0.40, 0.65))),
    ("one_buy_mid_window_15_minute", "the model's side with a 3c edge, one buy in the middle half of a 15-minute window", pk(Picker, edge=0.03, durs=(900,), t_hi=675, t_lo=225, band=(0.45, 0.75))),
    # ---- the rest of what was ahead ----
    ("learner", "four simple rules vote each window; follows whichever has been right most lately", pk(Bandit, **N, t_hi=200, t_lo=60, band=(0.25, 0.75))),
    ("odd_one_out", "three coins are past their lines and the fourth is still on its line: bets it follows", pk(OddOneOut, **N, t_hi=220, t_lo=60, band=(0.30, 0.65))),
    ("early_cashier", "buys a clear leader at 70-88c and rests an order to sell at 97c", pk(EarlyCashier, edge=0.02, t_hi=240, t_lo=90, band=(0.70, 0.88))),
    ("bet_by_the_odds_4c_edge", "the model's side at a 4c edge, sized by the odds (quarter Kelly)", pk(Kelly, edge=0.04, band=(0.10, 0.90))),
    # ---- two mismatches at once ----
    ("stale_quote_against_the_quarter_hour", "a jump the quote has not followed, only when it goes against the last quarter hour", pick(FADE=1.0)),
    ("stale_quote_half_second_bitcoin_solana", "half-second jumps on Bitcoin and Solana", pick(LOOK=0.5, ASSETS=("btc", "sol"))),
    ("stale_quote_half_second_3c_edge", "half-second jumps, needing a 3c edge", pick(LOOK=0.5, EDGE=0.03)),
]
assert len(V) == 50
BOTS = {f"tot_{i:02d}_{name}": (f"TOT {i}: {what}", f) for i, (name, what, f) in enumerate(V, 1)}
LABELS = {k: v[0] for k, v in BOTS.items()}


def build_tot_registry(engine=None):
    def make(k, f): return lambda acct: f(acct, engine, k)
    return {k: make(k, f) for k, (_, f) in BOTS.items()}
