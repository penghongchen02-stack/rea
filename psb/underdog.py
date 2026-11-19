"""Underdog bot: $1 on the underdog of every live game, any sport.

Like #19 it trades only during games and buys only while the US price is no
higher than the international one after the fee. Instead of waiting for a
15c fall, it buys whichever side the international price makes the
underdog (under 50c), once per game, for $1, and holds to the end. Draw
markets are skipped: a draw is not an underdog team.

"underdog-1" is the paper bot; "Live_underdog" (livesim.py) is its Live_ copy.
"""
from __future__ import annotations

from .bots import SIDES
from .simple_bots import Simple


class Underdog(Simple):
    key, idea, title = "underdog-1", 0, "Underdog $1, all sports"
    live_only = True
    clip = 1.0
    max_entries = 1
    max_market_frac = 0.001     # $1 per game
    price_cap = 0.50            # underdogs only
    edge = 0.0                  # US price at or under international, after the fee

    def wants(self, m, live: bool) -> bool:
        return not m.is_draw and super().wants(m, live)

    def pick(self, v, st):
        for s in SIDES:
            if v.fair(s) < 0.5:
                return (s, self.edge)
        return None


UNDERDOG = (Underdog(),)
