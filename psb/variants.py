"""Ten higher-volume versions each of the three best early performers: #19
tennis set fade, #15 momentum follower, and swisstony. Each changes only the
named settings of its base bot (thresholds, entries per market, bet size,
sports); everything else, including the 5-95c band and the per-market cap,
is the base bot's. Paper only, $1,000 each, like every other bot.
"""
from __future__ import annotations

from .bots import SwissTony


class _AllSports:
    """Entry filter for variants that trade every sport the engine prices."""

    def allows(self, v, side, price) -> bool:
        return True
from .simple_bots import S15Momentum, S19TennisSetFade

ALL = None
TENNIS = frozenset({"tennis"})
TENNIS_ESPORTS = frozenset({"tennis", "cs", "dota", "valorant", "lol"})


def _make(base, key: str, title: str, all_sports: bool = False, **settings):
    settings.setdefault("rules", base().rules_key)   # keep the base bot's entry filter
    bases = (_AllSports, base) if all_sports else (base,)
    cls = type(f"V_{key.replace('-', '_')}", bases, {"key": key, "title": title, **settings})
    return cls()


S19 = [
    _make(S19TennisSetFade, "s19-x01", "#19 x01: 10c drop", drop=0.10),
    _make(S19TennisSetFade, "s19-x02", "#19 x02: 8c drop", drop=0.08),
    _make(S19TennisSetFade, "s19-x03", "#19 x03: 12c drop, 0.5c edge", drop=0.12, edge=0.005),
    _make(S19TennisSetFade, "s19-x04", "#19 x04: 15c drop, any edge, 5 bets", edge=0.0, max_entries=5),
    _make(S19TennisSetFade, "s19-x05", "#19 x05: 10c drop, all sports", drop=0.10, cats=ALL),
    _make(S19TennisSetFade, "s19-x06", "#19 x06: 8c drop, all sports, 0.5c edge", drop=0.08,
          cats=ALL, edge=0.005),
    _make(S19TennisSetFade, "s19-x07", "#19 x07: 10c drop, 6 bets of $5", drop=0.10, max_entries=6,
          clip=5.0),
    _make(S19TennisSetFade, "s19-x08", "#19 x08: 20c drop", drop=0.20),
    _make(S19TennisSetFade, "s19-x09", "#19 x09: 10c drop, tennis + esports", drop=0.10,
          cats=TENNIS_ESPORTS),
    _make(S19TennisSetFade, "s19-x10", "#19 x10: 12c drop, all sports, 5 bets of $5", drop=0.12,
          cats=ALL, max_entries=5, clip=5.0),
]

S15 = [
    _make(S15Momentum, "s15-x01", "#15 x01: 2c jump", jump=0.02),
    _make(S15Momentum, "s15-x02", "#15 x02: 1.5c jump", jump=0.015),
    _make(S15Momentum, "s15-x03", "#15 x03: 2.5c jump, 0.5c edge", jump=0.025, edge=0.005),
    _make(S15Momentum, "s15-x04", "#15 x04: 2c jump, tennis only", jump=0.02, cats=TENNIS),
    _make(S15Momentum, "s15-x05", "#15 x05: 2c jump, 6 bets of $5", jump=0.02, max_entries=6,
          clip=5.0),
    _make(S15Momentum, "s15-x06", "#15 x06: 1.5c jump, 0.5c edge", jump=0.015, edge=0.005),
    _make(S15Momentum, "s15-x07", "#15 x07: 3c jump, 6 bets", jump=0.03, max_entries=6),
    _make(S15Momentum, "s15-x08", "#15 x08: 2c jump, tennis + esports", jump=0.02,
          cats=TENNIS_ESPORTS),
    _make(S15Momentum, "s15-x09", "#15 x09: 1c jump, 1c edge", jump=0.01, edge=0.01),
    _make(S15Momentum, "s15-x10", "#15 x10: 2c jump, before games too", jump=0.02,
          live_only=False),
]

SWISS = [
    _make(SwissTony, "swisstony-x01", "swisstony x01: 0.5c edge", min_edge=0.005),
    _make(SwissTony, "swisstony-x02", "swisstony x02: $3 probes, 20 per game", probe_usd=3.0,
          max_probes=20, max_market_frac=0.03),
    _make(SwissTony, "swisstony-x03", "swisstony x03: 1.5c edge, 3%", min_edge=0.015,
          max_market_frac=0.03),
    _make(SwissTony, "swisstony-x04", "swisstony x04: 2c edge, all sports", min_edge=0.02,
          max_market_frac=0.03, all_sports=True),
    _make(SwissTony, "swisstony-x05", "swisstony x05: 1c edge, all sports", max_market_frac=0.03,
          all_sports=True),
    _make(SwissTony, "swisstony-x06", "swisstony x06: 0.5c edge, all sports, $3 probes",
          min_edge=0.005, max_market_frac=0.02, probe_usd=3.0, all_sports=True),
    _make(SwissTony, "swisstony-x07", "swisstony x07: 2c edge, half-Kelly anchor", min_edge=0.02,
          kelly_frac=0.5),
    _make(SwissTony, "swisstony-x08", "swisstony x08: probes only, 20 per game",
          probes_before_anchor=10**9, max_probes=20, max_market_frac=0.03),
    _make(SwissTony, "swisstony-x09", "swisstony x09: 1.5c edge, all sports, early anchor",
          min_edge=0.015, max_market_frac=0.03, probes_before_anchor=1, all_sports=True),
    _make(SwissTony, "swisstony-x10", "swisstony x10: 0.3c edge, $2 probes, 2%", min_edge=0.003,
          max_market_frac=0.02, probe_usd=2.0, max_probes=20),
]

VARIANTS = tuple(S19 + S15 + SWISS)
