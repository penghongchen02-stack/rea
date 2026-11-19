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


# --- Ten versions (paper + Live_ copies in livesim.py) -----------------------------
# Five add a take-profit and a stop-loss: the other player is bought (locking the
# result) once the held underdog is `tp` above what we paid, or `sl` below it.
# Five change only the entry, held to the end like underdog-1.

from .exits import ProfitLock  # noqa: E402


def _ud(key: str, title: str, tp: float | None = None, sl: float | None = None, **settings):
    bases = (ProfitLock, Underdog) if tp or sl else (Underdog,)
    extra = {}
    if tp:
        extra.update(trail_rise=tp, trail_drop=0.0)   # lock as soon as it is tp up
    if sl:
        extra.update(stop_loss=sl)
    return type(f"UD_{key.replace('-', '_')}", bases,
                {"key": key, "title": title, **extra, **settings})


class _NoTennis(Underdog):
    def wants(self, m, live):
        from .markets import category_of
        return category_of(m.event_slug) != "tennis" and super().wants(m, live)


UD_CLASSES = (
    _ud("ud-tp10-sl10", "Underdog: take profit +10c, stop 10c", tp=0.10, sl=0.10),
    _ud("ud-tp15-sl10", "Underdog: take profit +15c, stop 10c", tp=0.15, sl=0.10),
    _ud("ud-tp20-sl15", "Underdog: take profit +20c, stop 15c", tp=0.20, sl=0.15),
    _ud("ud-tp5-sl5", "Underdog: take profit +5c, stop 5c", tp=0.05, sl=0.05),
    _ud("ud-tp25-sl20", "Underdog: take profit +25c, stop 20c", tp=0.25, sl=0.20),
    _ud("ud-jump1", "Underdog: 1c jump (more bets)", jump=0.01),
    _ud("ud-jump25", "Underdog: 2.5c jump (bigger moves)", jump=0.025),
    _ud("ud-2usd", "Underdog: $2 a buy", clip=2.0, max_market_frac=0.006),
    _ud("ud-20to45", "Underdog: 20-45c only (no long shots)", price_floor=0.20, price_cap=0.45),
    type("UD_ud_no_tennis", (_NoTennis,), {"key": "ud-no-tennis",
                                          "title": "Underdog: every sport except tennis"}),
)
UD_VERSIONS = tuple(c() for c in UD_CLASSES)
