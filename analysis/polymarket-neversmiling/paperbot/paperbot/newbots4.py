"""Twenty more tryout bots (user, 11-14 23:20Z), built on what has held up so far. Names start with "new4_".

What the first three batches and the research (README, "Research behind the new3 bots") showed:
  - The market's prices are right and the taker fee is a pure cost: no rule that predicts direction from what the
    price already shows has beaten it.
  - What has earned is acting on something the price does not show yet: fast_pickoff (+$658 over 1,271 windows, the
    only long record: it hits a quote that has not caught up with a Coinbase jump) and the head start (+$167: it
    works out a window's line before the exchange publishes it).
  - Near-certain late bets, where the fee is under half a cent, are all slightly ahead: the committee (43 of 44),
    the Sharky copy (56 of 59), stairstep, v_loose_rest, the last-45-seconds bot.
  - The learner is ahead (142 of 216), and a bot's result depends heavily on the coin.
  - Resting versions of taking bots lost: their orders fill when the price is moving against them.
  - Betting against a big quarter hour has the best evidence of the direction rules, but the bots built on it
    traded three to five times in two hours.

So: nine speed bots (1-9), five near-certain bots (10-14), three learners (15-17), and three reversal bots that
trade more often or are joined to the speed rule (18-20). None is a resting version of a taking bot.

The speed bots share fast_pickoff's limit: a paper fill at the quote seen when the bot decided is the best case; a
real order has to reach the exchange before faster traders.
"""
import math, time, collections
from .newbots import Picker, STAKE, fee
from .newbots2 import TickCount, Bandit
from .newbots3 import _Pick, FadeQuarter, quarter
from .fastpick import FastPick

ALL = ("btc", "eth", "sol", "xrp")


# ---- speed: fast_pickoff's settings, one change each --------------------------------------------------------------------
class PickSmall(_Pick): JUMP_SD = 2.0; MAX_PRICE = 0.40
class PickBig(_Pick): JUMP_SD = 3.5
class Pick15(_Pick): DUR = 900; LEFT = (60, 840)
class PickEdge(_Pick): EDGE = 0.05
class PickHalf(_Pick): LOOK = 0.5


class PickFade(_Pick):
    """fast_pickoff that only follows a jump against the coin's last quarter hour (when that moved at least its usual
    size): the two things with the most evidence, a stale quote and a reversal, at once."""
    def allow(self, market, side, ts):
        sp = self.engine.spot; a = sp.sec(market.asset, market.start - 900); b = sp.sec(market.asset, market.start)
        if not a or not b: return False
        mv = b / a - 1
        if abs(mv) < sp.sigma(market.asset) * 900 ** 0.5: return False
        return (side == "buy") == (mv < 0)


class PickFollow(_Pick):
    """A Bitcoin jump usually reaches the other coins a moment later. When Bitcoin jumps JUMP_SD and another coin's
    own price has not moved yet (under QUIET of its usual move) and its quote has not moved either, take Bitcoin's
    side in that coin, at up to MAX_PRICE."""
    LEAD = "btc"; MAX_PRICE = 0.60; QUIET = 1.0
    def ago(self, asset, ts):
        then = None
        for t, p in self.ticks[asset]:
            if t <= ts - self.LOOK: then = p
            else: break
        return then
    def on_spot(self, asset, px, ts):
        t0 = time.perf_counter()
        if asset not in self.ticks: return
        self.ticks[asset].append((ts, px)); self.stats["ticks"] += 1
        acct = self.acct; mk = [m for m in list(self.engine.mf.markets.values()) if m.kind == "updown" and m.dur == self.DUR and m.asset != self.LEAD
                                and m.asset in self.ticks and m.best_bid is not None and m.best_ask is not None]
        for m in mk:
            if asset in (self.LEAD, m.asset): self.mids.setdefault(m.slug, collections.deque(maxlen=120)).append((ts, (m.best_bid + m.best_ask) / 2))
        then = self.ago(asset, ts)
        if asset != self.LEAD or then is None: return
        jump = (px - then) / max(self.engine.spot.sigma(asset) * px * math.sqrt(self.LOOK), 1e-12)
        if abs(jump) < self.JUMP_SD: return
        for m in mk:
            left = m.end - ts
            if m.slug in self.done or not (self.LEFT[0] <= left <= self.LEFT[1]) or ts - m.book_ts > 3 or m.state not in (None, "MARKET_STATE_OPEN"): continue
            tk = self.ticks[m.asset]; cur = tk[-1][1] if tk else None; was = self.ago(m.asset, ts)
            if not cur or not was or abs(cur - was) > self.QUIET * self.engine.spot.sigma(m.asset) * cur * math.sqrt(self.LOOK): continue
            bb, ba = m.best_bid, m.best_ask; mid = (bb + ba) / 2; old = None
            for t, v in self.mids.get(m.slug, ()):
                if t <= ts - 0.8: old = v
                else: break
            if old is None or abs(mid - old) > self.STALE: continue
            side, lim, unit = ("buy", ba, ba) if jump > 0 else ("sell", bb, 1 - bb)
            if not (0.10 <= unit <= self.MAX_PRICE): continue
            lv = [x for x in m.avail(side, acct.liq_key) if x[0] == lim]
            q = round(min(self.ORDER_USD / max(unit, 0.01), lv[0][1] if lv else 0.0), 2)
            if q < m.min_qty: self.stats["no_size"] += 1; continue
            k = (acct.liq_key, side, lim); m.consumed[k] = m.consumed.get(k, 0.0) + q
            ms = (time.perf_counter() - t0) * 1000.0
            self.done.add(m.slug); self.lat.append(ms); self.stats["signals"] += 1
            self.queue.append((m.slug, side, lim, q, ts, f"follow-btc-{'up' if side == 'buy' else 'down'} j{jump:+.1f} {ms:.2f}ms"))
        self.stats["us_total"] += int((time.perf_counter() - t0) * 1e6)


def head(name, **attrs):
    """The head start (paperbot/newarbs.HeadStart) with class settings changed."""
    def f(a, e, **kw):
        from .newarbs import HeadStart
        return type("Head_" + name, (HeadStart,), attrs)(a, name, engine=e, **kw)
    return f


# ---- near-certain, where the fee is under half a cent ---------------------------------------------------------------------
def committee(name, need=3, assets=None):
    def f(a, e):
        from .newbots import Committee
        class C(Committee):
            def signal(self, market, ctx, now, p, det):
                qs = [(s.acct.positions.get(market.slug).qty if market.slug in s.acct.positions else 0.0) for s in self.shadow]
                up = sum(1 for q in qs if q > 1e-9); dn = sum(1 for q in qs if q < -1e-9)
                if up >= need and dn == 0: return True
                if dn >= need and up == 0: return False
                return None
        o = C(a, name, engine=e)
        if assets: o.assets = set(assets)
        return o
    return f


class Pinned(Picker):
    """In a window's last seconds most of the 60-second average that settles it has already happened. When the model,
    which counts the seconds already known, is SURE of a side and that side still costs no more than the band's top,
    take it."""
    SURE = 0.985
    def signal(self, market, ctx, now, p, det):
        return True if p >= self.SURE else False if p <= 1 - self.SURE else None


def near_certain(name, **kw):
    def f(a, e):
        from .newreps import PairMaker
        return PairMaker(a, name, clip=0.02, tilt=0.02, conv=0.43, band=(0.90, 0.98), left=10, flat=0, hold=0.0, only_fav=True, **kw)
    return f


# ---- learners ------------------------------------------------------------------------------------------------------------
class Bandit7(Bandit):
    """The learner with three more rules to choose from: the tick count, the side against a big quarter hour, and
    the early underdog (while a side is at 70c or more)."""
    def votes(self, market, ctx, now, p):
        v = super().votes(market, ctx, now, p)
        t = TickCount.signal(self, market, ctx, now, p, None)
        if t is not None: v["ticks"] = t
        q = quarter(ctx, market, 1.0)
        if q is not None: v["fade15"] = q
        mid = (market.best_bid + market.best_ask) / 2
        if mid >= 0.70 or mid <= 0.30: v["underdog"] = mid <= 0.30
        return v
    SHARE = TickCount.SHARE; MIN = TickCount.MIN


class BanditCoin(Bandit):
    """The learner keeping a separate record of each rule for each coin: every bot so far has depended on the coin."""
    def key(self, asset, rule): return f"{asset}:{rule}"


class TwoAgree(TickCount):
    """Bets only when the tick count and the model name the same side and the model has an edge after the fee."""
    def signal(self, market, ctx, now, p, det):
        t = super().signal(market, ctx, now, p, det)
        return t if t is not None and t == (p >= 0.5) else None


N = {"edge": None}
BOTS = {
    "new4_01_pickoff_smaller_jumps": ("NEW4 1: fast_pickoff on smaller jumps (2 times the usual move instead of 2.5), cheap side only (under 40c, where all its profit is)", lambda a, e: PickSmall(a, e)),
    "new4_02_pickoff_bigger_jumps": ("NEW4 2: fast_pickoff on bigger jumps only (3.5 times the usual move)", lambda a, e: PickBig(a, e)),
    "new4_03_pickoff_15_minute": ("NEW4 3: fast_pickoff on 15-minute windows, the cheapest market to take in", lambda a, e: Pick15(a, e)),
    "new4_04_pickoff_bigger_edge": ("NEW4 4: fast_pickoff that needs a 5c edge after the fee instead of 2c", lambda a, e: PickEdge(a, e)),
    "new4_05_pickoff_follow_bitcoin": ("NEW4 5: Bitcoin jumps and another coin's price and quote have not moved yet: takes Bitcoin's side in that coin", lambda a, e: PickFollow(a, e)),
    "new4_06_pickoff_half_second": ("NEW4 6: fast_pickoff that measures the jump over half a second instead of one", lambda a, e: PickHalf(a, e)),
    "new4_07_head_start_bigger_edge": ("NEW4 7: the head start (the champion's rule) that needs an 8c edge instead of 5c", head("new4_07_head_start_bigger_edge", EDGE=0.08)),
    "new4_08_head_start_15_minute": ("NEW4 8: the head start on 15-minute windows", lambda a, e: head("new4_08_head_start_15_minute")(a, e, durs=(900,))),
    "new4_09_head_start_before_the_line": ("NEW4 9: the head start only in the first 8 seconds, before the exchange has published the line", head("new4_09_head_start_before_the_line", LAST=8.0)),
    "new4_10_committee_two_of_three": ("NEW4 10: the committee (43 of 44 so far) needing two of its three members instead of all three, none against", committee("new4_10_committee_two_of_three", need=2)),
    "new4_11_committee_all_four_coins": ("NEW4 11: the committee on all four coins instead of Bitcoin and Ether", committee("new4_11_committee_all_four_coins", assets=ALL)),
    "new4_12_pinned_result": ("NEW4 12: last 25 seconds, the model is 98.5% sure because most of the settling average has already happened: takes that side at 80-96c", lambda a, e: Pinned(a, "new4_12_pinned_result", edge=0.0, t_hi=25, t_lo=4, band=(0.80, 0.96))),
    "new4_13_near_certain_15_minute": ("NEW4 13: the Sharky copy's rule (56 of 59) on 15-minute windows: one resting bid at 90-98c on the near-certain side in the last two minutes", near_certain("new4_13_near_certain_15_minute", durs=(900,), assets=ALL, el=780)),
    "new4_14_pinned_result_15_minute": ("NEW4 14: the pinned result on 15-minute windows", lambda a, e: Pinned(a, "new4_14_pinned_result_15_minute", edge=0.0, durs=(900,), t_hi=25, t_lo=4, band=(0.80, 0.96))),
    "new4_15_learner_seven_rules": ("NEW4 15: the learner (142 of 216) with three more rules to pick from: tick count, against a big quarter hour, early underdog", lambda a, e: Bandit7(a, "new4_15_learner_seven_rules", **N, engine=e, t_hi=240, t_lo=60, band=(0.25, 0.75))),
    "new4_16_tick_count_and_model_agree": ("NEW4 16: bets only when the tick count and the model name the same side, with a 1c edge after the fee", lambda a, e: TwoAgree(a, "new4_16_tick_count_and_model_agree", edge=0.01, t_hi=240, t_lo=45, band=(0.25, 0.70))),
    "new4_17_learner_per_coin": ("NEW4 17: the learner keeping a separate score for each coin", lambda a, e: BanditCoin(a, "new4_17_learner_per_coin", **N, engine=e, t_hi=200, t_lo=60, band=(0.25, 0.75))),
    "new4_18_fade_quarter_hour_looser": ("NEW4 18: against the last quarter hour when it moved its usual size (not 1.5 times), Bitcoin and Ether, so it trades several times an hour", lambda a, e: _fade("new4_18_fade_quarter_hour_looser", a, assets=("btc", "eth"), t_hi=285, t_lo=180, band=(0.35, 0.60))),
    "new4_19_fade_quarter_hour_15m_looser": ("NEW4 19: the same in 15-minute windows (+3.6c after the fee against real prices, 176 windows)", lambda a, e: _fade("new4_19_fade_quarter_hour_15m_looser", a, durs=(900,), t_hi=870, t_lo=600, band=(0.35, 0.60))),
    "new4_20_pickoff_against_the_quarter_hour": ("NEW4 20: fast_pickoff that only follows a jump that goes against the last quarter hour's move", lambda a, e: PickFade(a, e)),
}
LABELS = {k: v[0] for k, v in BOTS.items()}


def _fade(name, a, **kw):
    class F(FadeQuarter): BIG = 1.0
    return F(a, name, **N, **kw)


def build_new4_registry(engine=None):
    def make(f): return lambda acct: f(acct, engine)
    return {k: make(f) for k, (_, f) in BOTS.items()}
