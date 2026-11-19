"""Seven tennis bots that do not depend on reacting fast.

Makers rest bids under the international price and wait for sellers, so a
slower read only means a later quote, not a missed race. Pre-match value bots
buy before the first serve, when prices barely move. Tennis only (ITF, ATP,
WTA), $10 a bet, paper money. Built to trade a lot: makers keep re-quoting
until they hold $100 of one player, and pre-match bots buy up to 10 times a
match, $60 at most.

  tn-maker-2c       rest a bid 2c under fair on both players, before and during play
  tn-maker-1c       the same, 1c under (more fills, less edge)
  tn-maker-3c       the same, 3c under (fewer fills, more edge)
  tn-maker-quiet    2c under, only where the US spread is 4c+ (#10 on tennis)
  tn-maker-pre      2c under, before the match only
  tn-pre-1c         before the match, buy a player 1c+ cheaper than fair after the fee
  tn-pre-2c         the same, 2c+

Only the two pre-match takers have Live_ copies (livesim.py): a Live_ copy
models how a buy order fills, and makers do not send buy orders.
"""
from __future__ import annotations

from .simple_bots import Maker, Simple, best_edge_side

TENNIS = frozenset({"tennis"})


class TennisMaker(Maker):
    max_inventory = 100.0    # keep quoting until $100 of one player is held

    def step(self, v, st):
        if v.category not in TENNIS:
            return []
        return super().step(v, st)


class TennisPregame(Simple):
    cats = TENNIS
    pregame_only = True
    edge = 0.01
    max_entries = 10
    max_market_frac = 0.06   # $60 a match

    def pick(self, v, st):
        s = best_edge_side(v)
        return (s, self.edge) if s else None


def _make(base, key: str, title: str, **settings):
    return type(f"T_{key.replace('-', '_')}", (base,), {"key": key, "title": title, **settings})


TnPre1 = _make(TennisPregame, "tn-pre-1c", "Tennis pre-match value, 1c edge", edge=0.01)
TnPre2 = _make(TennisPregame, "tn-pre-2c", "Tennis pre-match value, 2c edge", edge=0.02)

TENNIS_BOTS = (
    _make(TennisMaker, "tn-maker-2c", "Tennis maker, 2c under fair", edge=0.02)(),
    _make(TennisMaker, "tn-maker-1c", "Tennis maker, 1c under fair", edge=0.01)(),
    _make(TennisMaker, "tn-maker-3c", "Tennis maker, 3c under fair", edge=0.03)(),
    _make(TennisMaker, "tn-maker-quiet", "Tennis maker, 2c under, 4c+ spreads", edge=0.02,
          min_spread=0.04)(),
    _make(TennisMaker, "tn-maker-pre", "Tennis maker, 2c under, before the match",
          edge=0.02, pregame_only=True)(),
    TnPre1(),
    TnPre2(),
)
