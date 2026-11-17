"""Read-only Polymarket US public market data (https://gateway.polymarket.us).

Only unauthenticated GET endpoints are used. This module has no order path.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime

from .polydata import FetchError, get_json

GATEWAY = "https://gateway.polymarket.us"
# Docs: 25 requests/s shared per IP. Stay well under it.
_MIN_INTERVAL = 1 / 10
_lock = threading.Lock()
_last = 0.0


def _get(path: str, params=None):
    global _last
    with _lock:
        wait = _last + _MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
    return get_json(f"{GATEWAY}{path}", params)


@dataclass(frozen=True)
class UsMarket:
    slug: str            # instrument slug, e.g. "aec-nba-ny-tor-2026-11-17"
    event_slug: str      # e.g. "nba-ny-tor-2026-11-17"
    league: str
    long_name: str       # what a long position wins on, e.g. "Knicks" or "Manchester City"
    team_names: tuple[str, ...]  # every team named in the event
    is_draw: bool
    start: datetime
    tick: float


def _parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def game_markets(league: str, limit: int = 100) -> list[UsMarket]:
    """Open game-winner markets for one US league."""
    body = _get(f"/v2/leagues/{league}/events", [("limit", str(limit))])
    out: list[UsMarket] = []
    for e in body.get("events") or []:
        if e.get("closed") or not e.get("active"):
            continue
        start = e.get("startTime") or e.get("startDate")
        if not start:
            continue
        teams: list[str] = []
        for m in e.get("markets") or []:
            for s in m.get("marketSides") or []:
                name = (s.get("team") or {}).get("safeName")
                if name and name not in teams:
                    teams.append(name)
        for m in e.get("markets") or []:
            mtype = m.get("marketType")
            if mtype not in ("moneyline", "drawable_outcome") or m.get("closed"):
                continue
            longs = [s for s in m.get("marketSides") or [] if s.get("long")]
            if not longs:
                continue
            long_side = longs[0]
            team = (long_side.get("team") or {}).get("safeName")
            is_draw = m["slug"].endswith("-draw")
            name = team or ("Draw" if is_draw else long_side.get("description"))
            if not name:
                continue
            out.append(UsMarket(m["slug"], e["slug"], league, name, tuple(teams), is_draw,
                                _parse_time(start), float(m.get("orderPriceMinTickSize") or 0.01)))
    return out


@dataclass(frozen=True)
class Top:
    bid: float | None
    bid_qty: float
    ask: float | None
    ask_qty: float
    state: str | None


def top_of_book(slug: str) -> Top:
    md = _get(f"/v1/markets/{slug}/book").get("marketData") or {}

    def best(levels, pick):
        lv = [(float(x["px"]["value"]), float(x["qty"])) for x in levels or []]
        return pick(lv, key=lambda t: t[0]) if lv else (None, 0.0)

    bid, bq = best(md.get("bids"), max)
    ask, aq = best(md.get("offers"), min)
    return Top(bid, bq, ask, aq, md.get("state"))


def settlement(slug: str) -> float | None:
    """Long-side settlement price, or None while the market is unsettled."""
    try:
        body = _get(f"/v1/markets/{slug}/settlement")
    except FetchError:
        return None
    value = body.get("settlement")
    return float(value) if value is not None else None
