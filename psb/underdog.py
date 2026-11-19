"""Underdog bot: #15 x06's quick momentum buy, on the underdog only, in every live game.

#15 x06 buys a side the moment its international price jumps 1.5c+ between
two polls, while the US price is still at least 0.5c under it after the fee.
This bot does the same in every sport, but only for the underdog (the side
the international price puts under 50c): it buys the underdog as soon as it
starts coming back, $1 a buy, up to 3 buys a game, held to the end. Draw
markets are skipped: a draw is not an underdog team.

"underdog-1" is the paper bot; "Live_underdog" (livesim.py) is its Live_ copy.
"""
from __future__ import annotations

from .simple_bots import EPS, Simple


class Underdog(Simple):
    key, idea, title = "underdog-1", 0, "Underdog momentum $1, all sports"
    live_only = True
    clip = 1.0
    max_entries = 3
    max_market_frac = 0.003     # $3 a game: 3 buys of $1
    price_cap = 0.50            # underdogs only
    jump = 0.015                # #15 x06's settings
    edge = 0.005

    def wants(self, m, live: bool) -> bool:
        return not m.is_draw and super().wants(m, live)

    def pick(self, v, st):
        if v.prev_fair_long is None:
            return None
        d = v.fair_long - v.prev_fair_long
        side = "long" if d > 0 else "short"   # the side that just jumped up
        if abs(d) < self.jump - EPS or v.fair(side) >= 0.5:
            return None
        return (side, self.edge)


UNDERDOG = (Underdog(),)
