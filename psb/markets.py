"""Owner of market interpretation: sport category, market type and resolution."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# Polymarket sports slugs start with a league code, e.g. "nba-den-nop-2026-11-14".
LEAGUE_CATEGORY = {
    "nba": "nba", "wnba": "nba",
    "nfl": "nfl", "cfb": "cfb", "ncaaf": "cfb",
    "cbb": "cbb", "ncaab": "cbb", "cwbb": "cbb",
    "mlb": "mlb", "nhl": "nhl", "khl": "nhl", "shl": "nhl",
    "atp": "tennis", "wta": "tennis", "tennis": "tennis", "itf": "tennis", "chal": "tennis",
    "itfme": "tennis", "itfwo": "tennis",
    "ufc": "ufc", "mma": "ufc", "pfl": "ufc", "boxing": "boxing",
    "lol": "lol", "cs2": "cs", "csgo": "cs", "cs": "cs", "dota2": "dota", "dota": "dota",
    "val": "valorant", "valorant": "valorant",
    "cricipl": "cricket", "ipl": "cricket", "cric": "cricket", "crint": "cricket",
}
# Polymarket US's own sport tree (GET /v2/sports, saved by scripts/run_paper.sh)
# owns league -> sport for US leagues not named above; international
# Polymarket's /sports list is the fallback.
US_SPORTS_FILE = Path(__file__).resolve().parent.parent / "data" / "us_sports.json"
US_SPORT_CATEGORY = {"soccer": "soccer", "tennis": "tennis", "basketball": "basketball",
                     "ice-hockey": "nhl", "football": "nfl", "baseball": "mlb", "mma": "ufc",
                     "cricket": "cricket", "boxing": "boxing"}
SPORTS_FILE = Path(__file__).resolve().parent.parent / "data" / "sports.json"
SOCCER_TAG, TENNIS_TAG = "100350", "864"
ESPORTS = {"lol", "cs", "dota", "valorant"}


@lru_cache(maxsize=1)
def _league_tags() -> dict[str, frozenset[str]]:
    if not SPORTS_FILE.exists():
        return {}
    rows = json.loads(SPORTS_FILE.read_text())
    return {r["sport"].lower(): frozenset((r.get("tags") or "").split(",")) for r in rows}


@lru_cache(maxsize=1)
def _us_league_sport() -> dict[str, str]:
    if not US_SPORTS_FILE.exists():
        return {}
    try:
        body = json.loads(US_SPORTS_FILE.read_text())
    except ValueError:
        return {}
    return {lg["slug"]: sp["slug"] for sp in body.get("sports") or []
            for lg in sp.get("leagues") or [] if lg.get("slug") and sp.get("slug")}


def category_of(slug: str) -> str:
    league = slug.split("-", 1)[0].lower()
    if league in LEAGUE_CATEGORY:
        return LEAGUE_CATEGORY[league]
    sport = _us_league_sport().get(league)
    if sport:
        # Women's college basketball is not the men's game the reports traded.
        return "wcbb" if league == "wcbb" else US_SPORT_CATEGORY.get(sport, sport)
    tags = _league_tags().get(league, frozenset())
    if SOCCER_TAG in tags:
        return "soccer"
    if TENNIS_TAG in tags:
        return "tennis"
    return "other"


@dataclass(frozen=True)
class Resolution:
    """Final price per outcome index, or None while unresolved/unknown."""
    prices: tuple[float, ...] | None
    market_type: str | None

    def payout(self, outcome_index: int) -> float | None:
        if self.prices is None or outcome_index >= len(self.prices):
            return None
        return self.prices[outcome_index]


def resolution_of(market: dict | None) -> Resolution:
    if not market:
        return Resolution(None, None)
    mtype = market.get("sportsMarketType")
    if not market.get("closed") or market.get("umaResolutionStatus") != "resolved":
        return Resolution(None, mtype)
    try:
        prices = tuple(float(x) for x in json.loads(market["outcomePrices"]))
    except (KeyError, TypeError, ValueError):
        return Resolution(None, mtype)
    # Only settled markets pay exactly 0/1 (or a 50/50 void); anything else is not final.
    if not all(p in (0.0, 0.5, 1.0) for p in prices):
        return Resolution(None, mtype)
    return Resolution(prices, mtype)
