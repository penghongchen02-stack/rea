"""Twenty tryout bots built from measurements (user, 11-14 20:30Z), plus a copy of fast_pickoff that obeys the
losing-coin rule. Names start with "new3_". They join the builder source "new".

What was measured before any of these was written (scripts and output: README, "Research behind the new3 bots"):

  F1  Taking at the market's price loses. Over 3,941 five-minute windows (78,536 price samples from every
      dashboard's taker fills, 11-10 to 11-14) buying a side at the touch returned -3.6c a contract after the fee and
      -2.1c before it. No rule tested against those real prices beat the fee with statistical confidence.
  F2  Where the fee is under 0.6c (a side at 7c or less, or 93c or more) taking returned -0.7c.
  F3  Bitcoin is the cheapest coin to take in (-1.7c; Ether -5.0c, Solana -4.2c, XRP -3.7c), and it is the best coin
      of nearly every tryout bot that is ahead.
  F4  Moves reverse. On 21 days of one-minute Coinbase prices (23,960 five-minute windows), betting a window the
      same way as a quarter hour that had moved more than 1.5 times its usual size was 3.9c worse than the plain
      model (t -7, the same in both halves). Against real prices: the side against that move -0.4c, the side with
      it -4.2c; in 15-minute windows +5.7c against (64 windows).
  F5  The worst trades are late and mid-priced: a leader bought at 55-90c in the last fifth of a window lost 12c,
      a favourite at 80-90c there 8.5c; at 90c or more it was -0.5c.
  F6  Chasing a one-minute spike of twice the usual size: -5.5c with it, -1.7c against it.
  F7  Early extremes are overdone. Price history: when one side is at 90% or more early, the other side is worth
      2.4c more than the model says (4.0c in 15-minute windows; both halves). Real prices: a side at 25c or less in
      the first 30% of a window was +0.3c after the fee, the only five-minute cell not below zero.
  F8  fast_pickoff's whole profit is the cheap side: bets under 40c made +$661, at 40c or more -$68; bets in a
      window's first minute lost $82 and after its fourth $69; Bitcoin made +$0.99 a bet, Ether -$0.15.
  F9  The four coins do not predict each other (Bitcoin leading, three coins pulling the fourth): nothing in 21 days.
  F11 Since 11:00Z on 11-14 only 44% of windows closed Up. The new bots' Down bets won far more than their Up bets
      (tick_count: Up 51%, Down 71%), so part of that day's profit is the day, not the rule.

So the bots here do one of four things: narrow a bot that already earns to the part that earns (1-4, 12, 13, 16-18),
avoid the fee by resting (6, 9, 14, 20), trade the two measured effects, reversal and early extremes (5-10, 15), or
move a working rule to the cheapest market (11, 19). Narrowing a bot to its best part after the fact is the easiest
way to fool oneself, so each of those is a test of whether the split holds on new windows, not a result.
"""
import math, time
from .newbots import Picker, _New, STAKE, fee
from .newbots2 import TickCount, FeeRouter, Bandit, secs
from .fastpick import FastPick


# ---- fast_pickoff, narrowed (F8, F3) ------------------------------------------------------------------------------------
class _Pick(FastPick):
    COIN_RULE = True          # these copies obey the tryout's losing-coin rule; the original fast_pickoff does not
class PickBtcSol(_Pick):
    """fast_pickoff on Bitcoin and Solana only: the coins the losing-coin rule left it on 11-14 10:38Z. The original
    books its fills on a path the rule does not reach and kept trading all four; this copy is the bot the rule meant."""
    ASSETS = ("btc", "sol")
class PickCheap(_Pick):
    MAX_PRICE = 0.40
class PickMid(_Pick):
    LEFT = (60, 240)
class PickBtc(_Pick):
    ASSETS = ("btc",)
class PickAll3(_Pick):
    ASSETS = ("btc", "sol", "xrp"); MAX_PRICE = 0.40; LEFT = (60, 240)


# ---- resting instead of taking (F1) ------------------------------------------------------------------------------------
class Resting:
    """Mix-in for a Picker: the bet is a resting order one cent inside the gap (or at the touch when the gap is one
    cent) that lives LIFE seconds, so no fee is paid. The window counts as bet once anything fills."""
    LIFE = 20.0
    def on_tick(self, market, ctx, now):
        st = self.mem.get(market.slug)
        if st and st.get("rest") and (now - st["rest"] > self.LIFE or st.get("done")):
            self.acct.cancel_all(market.slug); st["rest"] = None
        super().on_tick(market, ctx, now)
    def bet(self, market, up, dollars, tag):
        st = self.mem[market.slug]
        if st.get("rest"): return False
        bb, ba = market.best_bid, market.best_ask; inside = ba - bb >= 0.02
        px = round((bb + 0.01) if inside else bb, 2) if up else round((ba - 0.01) if inside else ba, 2)
        cost = px if up else 1 - px
        if not (0.02 <= cost <= 0.98): return False
        if self.acct.place(market, "buy" if up else "sell", px, round(dollars / cost, 2), post_only=True, tag=tag + " resting", fixed=True): st["rest"] = self._now
        return False
    def ready(self, market, now):
        self._now = now; return super().ready(market, now)


# ---- reversal after a big quarter hour (F4) -----------------------------------------------------------------------------
def quarter(ctx, market, big):
    """True (Up) / False (Down): the side against the coin's move in the 15 minutes before the window, when that
    move was `big` times its usual size; else None."""
    a = ctx["spot"].sec(market.asset, market.start - 900); b = ctx["spot"].sec(market.asset, market.start)
    if not a or not b: return None
    mv = b / a - 1
    return (mv < 0) if abs(mv) >= big * ctx["spot"].sigma(market.asset) * 900 ** 0.5 else None


class FadeQuarter(Picker):
    BIG = 1.5
    def signal(self, market, ctx, now, p, det): return quarter(ctx, market, self.BIG)
class FadeQuarterRest(Resting, FadeQuarter): pass


# ---- early extremes (F7) ------------------------------------------------------------------------------------------------
class EarlyUnderdog(Picker):
    """Early in a window one side is already priced at SURE or more: buy the other side, inside the band."""
    SURE = 0.70
    def signal(self, market, ctx, now, p, det):
        mid = (market.best_bid + market.best_ask) / 2
        return False if mid >= self.SURE else True if mid <= 1 - self.SURE else None
class EarlyUnderdogRest(Resting, EarlyUnderdog): pass
class EarlyUnderdog15(EarlyUnderdog): SURE = 0.75


# ---- 90c and over, in the fourth minute (F2, F5; the Sharky copy earned $0.80 a bet there and $0.24 later) ---------------
class NinetyPlus(Picker):
    def signal(self, market, ctx, now, p, det): return (p >= 0.5) if abs(p - 0.5) >= 0.40 else None


# ---- tick count, narrowed and moved (the best of the second twenty; F11 says how much of that was the day) ---------------
class TickRest(Resting, TickCount): pass
class TickAndFade(TickCount):
    """Two measured things at once: the tick count's side, only when it is also the side against a big quarter hour
    (1 time its usual size)."""
    def signal(self, market, ctx, now, p, det):
        t = super().signal(market, ctx, now, p, det); q = quarter(ctx, market, 1.0)
        return t if t is not None and q is not None and t == q else None


# ---- everything measured, on the cheapest coin, without the fee ----------------------------------------------------------
class House(Resting, Picker):
    """Bitcoin only. The first rule that speaks: the early underdog (first minute), the side against a big quarter
    hour (first two minutes), then the tick count. Resting orders, nothing above 60c, nothing in the last minute."""
    def signal(self, market, ctx, now, p, det):
        el = now - market.start; mid = (market.best_bid + market.best_ask) / 2
        if el <= 60 and (mid >= 0.70 or mid <= 0.30): return mid <= 0.30
        if el <= 120:
            q = quarter(ctx, market, 1.5)
            if q is not None: return q
        return TickCount.signal(self, market, ctx, now, p, det)
    SHARE = TickCount.SHARE; MIN = TickCount.MIN


N = {"edge": None}
BTC = ("btc",); BE = ("btc", "eth")
# name: (what it does and the measurement behind it, how to build it)
BOTS = {
    "fast_pickoff_bitcoin_and_solana_only": ("fast_pickoff on Bitcoin and Solana only, the coins the losing-coin rule left it; the original kept trading all four", lambda a, e: PickBtcSol(a, e)),
    "new3_01_pickoff_cheap_side_only": ("NEW3 1: fast_pickoff that only buys a side under 40c. Its bets under 40c made +$661 and the rest -$68", lambda a, e: PickCheap(a, e)),
    "new3_02_pickoff_minutes_2_to_4": ("NEW3 2: fast_pickoff that skips a window's first and last minute. Those lost $82 and $69; the middle made the profit", lambda a, e: PickMid(a, e)),
    "new3_03_pickoff_bitcoin_only": ("NEW3 3: fast_pickoff on Bitcoin only: +$0.99 a bet there, 52% won, against 23-29% on the other coins", lambda a, e: PickBtc(a, e)),
    "new3_04_pickoff_all_three_filters": ("NEW3 4: fast_pickoff with all three: under 40c, minutes 2 to 4, no Ether (its one losing coin)", lambda a, e: PickAll3(a, e)),
    "new3_05_fade_the_last_quarter_hour": ("NEW3 5: bets against the coin's move of the last 15 minutes when it was 1.5 times the usual size. 21 days of prices: the same way was 3.9c worse than the model", lambda a, e: FadeQuarter(a, "new3_05_fade_the_last_quarter_hour", **N, t_hi=285, t_lo=180, band=(0.35, 0.62))),
    "new3_06_fade_quarter_hour_resting": ("NEW3 6: the same bet as a resting order, so no fee: against real prices it was +1.1c before the fee and -0.4c after", lambda a, e: FadeQuarterRest(a, "new3_06_fade_quarter_hour_resting", **N, t_hi=285, t_lo=150, band=(0.35, 0.62))),
    "new3_07_fade_quarter_hour_15_minute": ("NEW3 7: the same bet in 15-minute windows, where it returned +5.7c after the fee (64 windows only)", lambda a, e: FadeQuarter(a, "new3_07_fade_quarter_hour_15_minute", **N, durs=(900,), t_hi=870, t_lo=600, band=(0.35, 0.62))),
    "new3_08_early_underdog_20_to_30c": ("NEW3 8: one side at 70c or more in the first minute: buys the other at 20-30c, Bitcoin and Ether only. too_sure_too_soon made +$201 at 20c and over, -$87 under, and lost on Solana and XRP", lambda a, e: EarlyUnderdog(a, "new3_08_early_underdog_20_to_30c", **N, assets=BE, t_hi=292, t_lo=240, band=(0.20, 0.30))),
    "new3_09_early_underdog_resting": ("NEW3 9: the early underdog bought with a resting order at 15-30c, all four coins: +1.7c before the fee against real prices", lambda a, e: EarlyUnderdogRest(a, "new3_09_early_underdog_resting", **N, t_hi=292, t_lo=210, band=(0.15, 0.30))),
    "new3_10_early_underdog_15_minute": ("NEW3 10: the early underdog in 15-minute windows (first 30%), at 10-25c: 4.0c better than the model on 21 days of prices", lambda a, e: EarlyUnderdog15(a, "new3_10_early_underdog_15_minute", **N, durs=(900,), t_hi=880, t_lo=630, band=(0.10, 0.25))),
    "new3_11_ninety_plus_in_minute_four": ("NEW3 11: Bitcoin, a side at 90-97c, bought only in the fourth minute. The Sharky copy earned $0.80 a bet there and $0.24 later; the fee is under a cent", lambda a, e: NinetyPlus(a, "new3_11_ninety_plus_in_minute_four", edge=0.0, assets=BTC, t_hi=120, t_lo=60, band=(0.90, 0.97))),
    "new3_12_tick_count_bitcoin_only": ("NEW3 12: tick_count on Bitcoin only: $3.62 a bet there against $0.96-1.95 on the others", lambda a, e: TickCount(a, "new3_12_tick_count_bitcoin_only", **N, assets=BTC, t_hi=240, t_lo=45, band=(0.25, 0.75))),
    "new3_13_tick_count_up_to_60c": ("NEW3 13: tick_count that never pays over 60c: $3.06-3.22 a bet under 60c, $0.64 above", lambda a, e: TickCount(a, "new3_13_tick_count_up_to_60c", **N, t_hi=240, t_lo=45, band=(0.25, 0.60))),
    "new3_14_tick_count_resting": ("NEW3 14: tick_count with a resting order: it paid $57 in fees on $386 of profit", lambda a, e: TickRest(a, "new3_14_tick_count_resting", **N, t_hi=240, t_lo=45, band=(0.25, 0.75))),
    "new3_15_tick_count_and_fade_agree": ("NEW3 15: bets only when the tick count's side is also the side against a big quarter hour", lambda a, e: TickAndFade(a, "new3_15_tick_count_and_fade_agree", **N, t_hi=240, t_lo=45, band=(0.25, 0.75))),
    "new3_16_fee_router_bitcoin_only": ("NEW3 16: fee_router on Bitcoin only: 73% won and $1.50 a bet there; it lost on Solana and XRP", lambda a, e: FeeRouter(a, "new3_16_fee_router_bitcoin_only", assets=BTC)),
    "new3_17_learner_bitcoin_only": ("NEW3 17: the learner on Bitcoin only: 75% won and $2.65 a bet there; it lost on the other three", lambda a, e: Bandit(a, "new3_17_learner_bitcoin_only", **N, engine=e, assets=BTC, t_hi=200, t_lo=60, band=(0.25, 0.75))),
    "new3_18_head_start_bitcoin_only": ("NEW3 18: the head start (the champion's rule) on Bitcoin only: 44% won and $3.52 a bet there, the best of its coins", lambda a, e: _head(a, e)),
    "new3_19_tick_count_15_minute_bitcoin": ("NEW3 19: tick_count moved to Bitcoin's 15-minute windows, the cheapest market to take in (-0.8c after the fee, +0.7c before)", lambda a, e: TickCount(a, "new3_19_tick_count_15_minute_bitcoin", **N, assets=BTC, durs=(900,), t_hi=780, t_lo=180, band=(0.25, 0.70))),
    "new3_20_house_bitcoin_resting": ("NEW3 20: Bitcoin only, resting orders only: the early underdog, then the side against a big quarter hour, then the tick count; nothing over 60c or in the last minute", lambda a, e: House(a, "new3_20_house_bitcoin_resting", **N, assets=BTC, t_hi=292, t_lo=60, band=(0.15, 0.60))),
}
LABELS = {k: v[0] for k, v in BOTS.items()}


def _head(a, e):
    from .newarbs import HeadStart
    return HeadStart(a, "new3_18_head_start_bitcoin_only", engine=e, assets=BTC)


def build_new3_registry(engine=None):
    def make(f): return lambda acct: f(acct, engine)
    return {k: make(f) for k, (_, f) in BOTS.items()}
