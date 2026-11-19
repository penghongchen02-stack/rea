"""Eight bots for gaps the other bots leave (g01-g12 of the "what did I miss" list),
each with a Live_ copy in livesim.py. Paper money, $10 a buy unless stated.

Not built, because Polymarket US offers nothing to trade or price for them
(checked 2026-11-19): esports single maps (only match winners are listed),
cricket (its US matches have no international market to price them), and
season/tournament futures, which a two-game hedge would also need.

  g01-fights      UFC, boxing, bare-knuckle: 1c+ value before the fight, #15 x06's
                  1.5c jump once it starts
  g02-darts       darts (PDC and Modus), the same two rules
  g04-mlb         baseball winners and totals, the same two rules
  g06-lines-maker rest bids 2c under fair on spreads and totals (makers elsewhere
                  quote winners only)
  g08-draw        soccer draw markets only, 1c+ under fair
  g09-take-profit #15 x06, cashed out (the other side bought) once 10c up
  g11-reserve     #15 x06, but past 70% of the money at risk only on 3c+ edges
  g12-clock       copies #15 x06 on tennis 05-17 UTC and on NBA/NHL 23-05 UTC
"""
from __future__ import annotations

from .exits import ProfitLock
from .fifty import Copier
from .simple_bots import EPS, Maker, S15Momentum, Simple, best_edge_side


class TwoRule(Simple):
    """Before play: buy a 1c+ gap. During play: #15 x06 (1.5c jump, 0.5c edge)."""
    leagues: tuple[str, ...] = ()
    jump = 0.015

    def wants(self, m, live):
        return m.league in self.leagues and super().wants(m, live)

    def pick(self, v, st):
        if not v.live:
            s = best_edge_side(v)
            return (s, 0.01) if s else None
        if v.prev_fair_long is None:
            return None
        d = v.fair_long - v.prev_fair_long
        if abs(d) < self.jump - EPS:
            return None
        return ("long" if d > 0 else "short", 0.005)


class G01Fights(TwoRule):
    key, title = "g01-fights", "Fights: UFC, boxing, bare-knuckle"
    leagues = ("ufc", "boxing", "bkfc")
    extra_leagues = ("boxing", "bkfc")


class G02Darts(TwoRule):
    key, title = "g02-darts", "Darts: PDC and Modus"
    leagues = ("pdcdarts", "modus")
    extra_leagues = ("modus",)


class G04Mlb(TwoRule):
    key, title = "g04-mlb", "Baseball: winners and totals"
    leagues = ("mlb",)
    kinds = ("moneyline", "totals")


class G06LinesMaker(Maker):
    key, title = "g06-lines-maker", "Spreads and totals maker, 2c under fair"
    kinds = ("spreads", "totals")


class G08Draw(Simple):
    key, title = "g08-draw", "Soccer draws only, 1c+ under fair"

    def wants(self, m, live):
        return m.is_draw and super().wants(m, live)

    def pick(self, v, st):
        s = best_edge_side(v)
        return (s, 0.01) if s else None


class G09TakeProfit(ProfitLock, S15Momentum):
    key, idea, title = "g09-take-profit", 0, "#15 x06, cashed out at +10c"
    jump, edge = 0.015, 0.005
    trail_rise, trail_drop = 0.10, 0.0     # lock as soon as it is 10c above cost


class G11Reserve(S15Momentum):
    key, idea, title = "g11-reserve", 0, "#15 x06 with a 30% cash reserve for 3c+ edges"
    jump, edge = 0.015, 0.005

    def pick(self, v, st):
        got = super().pick(v, st)
        if got and v.cash < 0.30 * v.bankroll:
            return (got[0], 0.03)
        return got


class G12Clock(Copier):
    key, title = "g12-clock", "Clock: tennis by day, NBA/NHL by night (#15 x06)"

    def choose(self, v, st):
        h = v.now.hour
        if 5 <= h < 17 and v.category == "tennis":
            return "s15-x06"
        if (h >= 23 or h < 5) and v.category in ("nba", "nhl"):
            return "s15-x06"
        return None


GAP_CLASSES = (G01Fights, G02Darts, G04Mlb, G06LinesMaker, G08Draw, G09TakeProfit,
               G11Reserve, G12Clock)
GAPS = tuple(c() for c in GAP_CLASSES)
