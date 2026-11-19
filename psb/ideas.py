"""Six more bot ideas, each with a Live_ copy in livesim.py. Paper money.

  fav-fade        any live game: the favourite (50c+) just fell 1.5c+ between
                  polls; buy it while the US price is 0.5c+ under fair
  late-underdog   late in a close game (an elapsed-time proxy: no game clock is
                  read), buy the underdog priced 25-45c, once
  big-jump        #15 with a 5c jump instead of 3c, 0.5c edge
  tn-set-loser    tennis: a pre-match favourite (55c+ before play) fell 20c+
                  during play (a proxy for losing a set; no set scores are read)
  pre-dog-lock    before play, buy an underdog priced 20-45c 1c+ under fair; once
                  live, lock in the profit as soon as it is 15c above cost
  x06-itfme       #15 x06 on ITF men's tennis only
"""
from __future__ import annotations

from datetime import timedelta

from .exits import ProfitLock
from .simple_bots import EPS, S15Momentum, Simple, best_edge_side

# Minutes after the scheduled start that count as "late", per sport. Start
# times are schedules, so this is rough: a late kick-off shifts it.
LATE_AFTER = {"soccer": 70, "nba": 110, "basketball": 110, "cbb": 95, "nhl": 110,
              "nfl": 150, "cfb": 150, "mlb": 140}


class FavFade(Simple):
    key, idea, title = "fav-fade", 0, "Comeback fade: buy the favourite after a 1.5c drop"
    live_only = True
    drop, edge = 0.015, 0.005

    def pick(self, v, st):
        if v.prev_fair_long is None:
            return None
        d = v.fair_long - v.prev_fair_long
        side = "short" if d > 0 else "long"   # the side that just fell
        if abs(d) < self.drop - EPS or v.fair(side) <= 0.5:
            return None
        return (side, self.edge)


class LateUnderdog(Simple):
    key, idea, title = "late-underdog", 0, "Late underdog in a close game"
    live_only = True
    max_entries = 1
    edge = 0.005

    def pick(self, v, st):
        late = LATE_AFTER.get(v.category)
        if late is None or v.now - v.m.start < timedelta(minutes=late):
            return None
        for s in ("long", "short"):
            if 0.25 <= v.fair(s) <= 0.45:
                return (s, self.edge)
        return None


class BigJump(S15Momentum):
    key, idea, title = "big-jump", 0, "Big jump only: #15 with a 5c jump"
    jump, edge = 0.05, 0.005


class TennisSetLoser(Simple):
    key, idea, title = "tn-set-loser", 0, "Tennis: buy the pre-match favourite after a 20c fall"
    cats = frozenset({"tennis"})
    drop, edge, fav = 0.20, 0.01, 0.55

    def pick(self, v, st):
        if not v.live:
            st["pre_fair"] = v.fair_long   # the last price before play
            return None
        pre = st.get("pre_fair")
        if pre is None:
            return None
        for s, p in (("long", pre), ("short", 1 - pre)):
            if p >= self.fav and p - v.fair(s) >= self.drop - EPS:
                return (s, self.edge)
        return None


class PreDogLock(ProfitLock, Simple):
    key, idea, title = "pre-dog-lock", 0, "Pre-game underdog, locked in once 15c up"
    max_entries = 1
    edge = 0.01
    trail_rise, trail_drop = 0.15, 0.0   # lock as soon as it is 15c above cost

    def pick(self, v, st):
        if v.live:
            return None
        s = best_edge_side(v)
        return (s, self.edge) if s and 0.20 <= v.fair(s) <= 0.45 else None


class X06Itfme(S15Momentum):
    key, idea, title = "x06-itfme", 0, "#15 x06 on ITF men's tennis only"
    jump, edge = 0.015, 0.005

    def pick(self, v, st):
        return super().pick(v, st) if v.m.league == "itfme" else None


IDEA_CLASSES = (FavFade, LateUnderdog, BigJump, TennisSetLoser, PreDogLock, X06Itfme)
IDEAS6 = tuple(c() for c in IDEA_CLASSES)
