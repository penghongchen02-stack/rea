"""Entry rules transcribed from each PR&R sports report.

These are only the parts of each strategy that can be written down ahead of
time: which markets, which prices, which hours. None of the reports discloses
the signal that picks the side (a sportsbook feed, a game model, a live read),
and the reports' "dominance" filters are computed from the wallet's own later
allocation, so they cannot be applied before the fact and are left out.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .markets import ESPORTS


@dataclass(frozen=True)
class Candidate:
    category: str
    market_type: str | None
    side: str          # "BUY" or "SELL"
    price: float
    hour_utc: int


@dataclass(frozen=True)
class Strategy:
    key: str
    rules: str
    accept: Callable[[Candidate], bool]


def _band(c: Candidate, lo: float, hi: float) -> bool:
    return lo <= c.price < hi


def _ferrari(c: Candidate) -> bool:
    # Tennis, MLB, soccer, UFC; NHL excluded; NBA only at 3x dominance (not knowable) -> excluded.
    return c.category in {"tennis", "mlb", "soccer", "ufc"}


def _swisstony(c: Candidate) -> bool:
    return (c.side == "BUY" and c.category not in {"nba", "other"}
            and not _band(c, 0.40, 0.50) and c.hour_utc not in {18, 20, 21})


def _gambling(c: Candidate) -> bool:
    return (c.category in {"soccer", "mlb", "nfl", "cbb"}
            and not _band(c, 0.40, 0.50) and c.hour_utc not in {10, 12, 13})


def _sinenooneei(c: Candidate) -> bool:
    eligible = (c.category == "tennis"
                or (c.category == "mlb" and c.market_type == "totals")
                or c.category in ESPORTS)
    return (eligible and c.side == "BUY" and _band(c, 0.10, 0.80)
            and 8 <= c.hour_utc < 22 and c.hour_utc != 16)


def _kch123(c: Candidate) -> bool:
    # Report hours (4-6pm, 8pm, 10pm weak) give no time zone; read as UTC.
    return (c.side == "BUY" and c.category in {"nfl", "cfb", "cbb"}
            and _band(c, 0.20, 0.70) and c.hour_utc not in {16, 17, 18, 20, 22})


def _geiyecapixie(c: Candidate) -> bool:
    return c.category == "lol"


def _randomgamebet(c: Candidate) -> bool:
    return c.category in ESPORTS and c.hour_utc not in {6, 7}


STRATEGIES: dict[str, Strategy] = {
    s.key: s
    for s in [
        Strategy("ferrarichampions2026", "tennis/MLB/soccer/UFC only; no NHL, no NBA", _ferrari),
        Strategy("swisstony", "BUY; no NBA; skip $0.40-0.50; skip 18,20,21 UTC", _swisstony),
        Strategy("gamblingisallyourneed", "soccer/MLB/NFL/CBB; skip $0.40-0.50; skip 10,12,13 UTC", _gambling),
        Strategy("sinenooneei", "tennis, MLB totals, esports; BUY $0.10-0.80; 08-22 UTC except 16", _sinenooneei),
        Strategy("kch123", "BUY NFL/CFB/CBB at $0.20-0.70; skip 16-18,20,22 UTC", _kch123),
        Strategy("geiyecapixie", "League of Legends only", _geiyecapixie),
        Strategy("randomgamebet", "CS/LoL/Dota/Valorant; skip 06-07 UTC", _randomgamebet),
    ]
}
