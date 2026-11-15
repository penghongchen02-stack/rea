"""Ten tryout bots built from the study of the losing trades (user, 11-15 01:20Z). Names start with "new5_".

What the study of all 128,367 trades found (README, "What the losing trades have in common" and its second pass),
and the rule each finding gives:

  finding                                                               rule
  the first buy in a window lost 1.2% of its stake, later buys 2-4%     one buy a window, never another
  buys at 10-40c lost 8-13%; at 50-70c they made 0.7-1.5%               buy the side that is ahead, at 50-70c
  the coin level with its line at the buy: -9.5%                        only when one side is clearly ahead
  first quarter of the window: 81% of the net loss                      nothing in the first quarter
  same side as a big move of the last 15 minutes: -23.4%; against +5.8% never with that move
  sold out before the close: sold at 31c what then won 34% of the time  hold to the close, no selling
  85-99% of a dashboard's money on one side of a window                 do not stand where the crowd stands
  22% of the bank staked in an average window ended in ruin             1% of the bank a window

Every bot here makes one taker buy of 1% of its bank and holds it. They differ in which of the rules they apply, so
the tryout shows which rules carry the result. Two warnings from earlier work that these bots do not escape: against
real prices the leader bought by taking returned -3.2c a contract after the fee (README, research behind the new3
bots), and the +0.7-1.5% above is a mix of taking and resting fills. Expect them near break-even, not clearly ahead.
"""
from .newbots import Picker
from .newbots3 import _Pick


def before(ctx, market):
    """The coin's move in the 15 minutes before the window, in units of its usual quarter-hour move; None if unknown."""
    a = ctx["spot"].sec(market.asset, market.start - 900); b = ctx["spot"].sec(market.asset, market.start)
    if not a or not b: return None
    return (b / a - 1) / max(ctx["spot"].sigma(market.asset) * 900 ** 0.5, 1e-12)


class Rules(Picker):
    """The side that is ahead (the model's z beyond LEAD), unless that is the same way as a move of BIG times the
    usual size in the 15 minutes before the window. The price band and the part of the window come from the settings."""
    LEAD = 0.3; BIG = 1.0
    def signal(self, market, ctx, now, p, det):
        z = det["z"]
        if abs(z) < self.LEAD: return None
        up = z > 0; mv = before(ctx, market)
        if mv is not None and abs(mv) >= self.BIG and (mv > 0) == up: return None
        return up


class Leader(Rules):
    BIG = 1e9                       # the side that is ahead, whatever came before


class Against(Picker):
    """The one group that made money (+5.8%): the side against a big move of the last 15 minutes. Here only while
    that side is not behind in the window (z no worse than -LEAD)."""
    LEAD = 0.3; BIG = 1.5
    def signal(self, market, ctx, now, p, det):
        mv = before(ctx, market)
        if mv is None or abs(mv) < self.BIG: return None
        up = mv < 0
        return up if (det["z"] if up else -det["z"]) >= -self.LEAD else None


def crowd(engine, slug, where="bonelab"):
    """(share of the dollars that the bots of dashboard `where` hold on Up in this window, how many bots hold any)."""
    u = next((x for x in engine.universes if x.name == where), None)
    if u is None: return None, 0
    up = dn = 0.0; n = 0
    for a in list(u.accounts.values()):
        p = a.positions.get(slug)
        if not p or p.closed or abs(p.qty) < 1e-9: continue
        n += 1
        if p.qty > 0: up += p.qty * p.avg
        else: dn += -p.qty * (1 - p.avg)
    return (up / (up + dn) if up + dn > 0 else None), n


class CrowdBargain(Picker):
    """Bonelab's bots (about 32 in a window, 92% of their money on one side) are holding a side that costs CHEAP or
    less, the kind of buy that lost 8-13%: take the other side, the one that is ahead."""
    CHEAP = 0.40; MIN = 8; SHARE = 0.8
    def signal(self, market, ctx, now, p, det):
        s, n = crowd(self.engine, market.slug)
        if s is None or n < self.MIN: return None
        if s >= self.SHARE and market.best_ask <= self.CHEAP: return False
        if s <= 1 - self.SHARE and 1 - market.best_bid <= self.CHEAP: return True
        return None


class AwayFromCrowd(Rules):
    """The rules, but not in a window where Bonelab's bots already have most of their money on the same side."""
    MIN = 8; SHARE = 0.6
    def signal(self, market, ctx, now, p, det):
        up = super().signal(market, ctx, now, p, det)
        if up is None: return None
        s, n = crowd(self.engine, market.slug)
        if s is not None and n >= self.MIN and (s if up else 1 - s) >= self.SHARE: return None
        return up


class PickRules(_Pick):
    """fast_pickoff under the rules: nothing in a window's first quarter or last half minute, and never the same
    way as a big move of the last 15 minutes."""
    LEFT = (30, 225)
    def allow(self, market, side, ts):
        sp = self.engine.spot; a = sp.sec(market.asset, market.start - 900); b = sp.sec(market.asset, market.start)
        if not a or not b: return True
        mv = (b / a - 1) / max(sp.sigma(market.asset) * 900 ** 0.5, 1e-12)
        return not (abs(mv) >= 1.0 and (mv > 0) == (side == "buy"))


N = {"edge": None}
BOTS = {
    "new5_01_all_the_rules": ("NEW5 1: every rule from the study: one buy, the side that is ahead, at 50-70c, in the second half of the window, never the same way as a big move of the last 15 minutes, held to the close, 1% of the bank",
                              lambda a, e: Rules(a, "new5_01_all_the_rules", **N, engine=e, t_hi=150, t_lo=30, band=(0.50, 0.70))),
    "new5_02_side_ahead_at_50_to_70c": ("NEW5 2: only the price rule: the side that is ahead at 50-70c (those buys made 0.7-1.5%; buys at 10-40c lost 8-13%), any time after the first quarter",
                                        lambda a, e: Leader(a, "new5_02_side_ahead_at_50_to_70c", **N, engine=e, t_hi=225, t_lo=30, band=(0.50, 0.70))),
    "new5_03_against_the_quarter_hour_not_behind": ("NEW5 3: the one group that made money (+5.8%): against a big move of the last 15 minutes, only while that side is not behind, at 40-65c",
                                                    lambda a, e: Against(a, "new5_03_against_the_quarter_hour_not_behind", **N, engine=e, t_hi=225, t_lo=30, band=(0.40, 0.65))),
    "new5_04_one_buy_mid_window": ("NEW5 4: the model's side with a 3c edge, one buy in the middle half of the window (first buys lost 1.2%, later ones 2-4%), held",
                                   lambda a, e: Picker(a, "new5_04_one_buy_mid_window", edge=0.03, engine=e, t_hi=225, t_lo=75, band=(0.45, 0.75))),
    "new5_05_other_side_of_the_crowds_bargain": ("NEW5 5: Bonelab's bots are holding a side that costs 40c or less: takes the other side, at 55-78c",
                                                 lambda a, e: CrowdBargain(a, "new5_05_other_side_of_the_crowds_bargain", **N, engine=e, t_hi=225, t_lo=30, band=(0.55, 0.78))),
    "new5_06_the_rules_away_from_the_crowd": ("NEW5 6: every rule, and not in a window where Bonelab's bots already have most of their money on the same side",
                                              lambda a, e: AwayFromCrowd(a, "new5_06_the_rules_away_from_the_crowd", **N, engine=e, t_hi=150, t_lo=30, band=(0.50, 0.70))),
    "new5_07_all_the_rules_15_minute": ("NEW5 7: every rule on 15-minute windows",
                                        lambda a, e: Rules(a, "new5_07_all_the_rules_15_minute", **N, engine=e, durs=(900,), t_hi=450, t_lo=90, band=(0.50, 0.70))),
    "new5_08_all_the_rules_bitcoin": ("NEW5 8: every rule on Bitcoin only, the cheapest coin to take in",
                                      lambda a, e: Rules(a, "new5_08_all_the_rules_bitcoin", **N, engine=e, assets=("btc",), t_hi=150, t_lo=30, band=(0.50, 0.70))),
    "new5_09_side_ahead_late": ("NEW5 9: the side that is ahead at 60-90c with 75-90% of the window gone, the one part of the window where buys came out ahead (+0.8%)",
                                lambda a, e: Leader(a, "new5_09_side_ahead_late", **N, engine=e, t_hi=75, t_lo=30, band=(0.60, 0.90))),
    "new5_10_pickoff_under_the_rules": ("NEW5 10: fast_pickoff under the rules: not in the first quarter, never the same way as a big move of the last 15 minutes",
                                        lambda a, e: PickRules(a, e)),
}
LABELS = {k: v[0] for k, v in BOTS.items()}


def build_new5_registry(engine=None):
    def make(f): return lambda acct: f(acct, engine)
    return {k: make(f) for k, (_, f) in BOTS.items()}
