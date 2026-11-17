"""Reference fair value for a Polymarket US market from international Polymarket.

This stands in for the sportsbook feed the reports infer (Pinnacle, Betfair,
OddsJam). It is a price from a related venue, not an independent forecast.
"""
from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timedelta

from .polydata import GAMMA_API, FetchError, get_json
from .usmarket import UsMarket

MAX_START_GAP = timedelta(hours=6)


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _names_match(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a) and bool(b) and (a in b or b in a)


def find_event(m: UsMarket) -> dict | None:
    """International event for the same game: same league prefix, close start time."""
    teams = [t for t in m.team_names if t] or [m.long_name]
    q = " ".join(teams)
    try:
        found = get_json(f"{GAMMA_API}/public-search",
                         [("q", q), ("limit_per_type", "8"), ("events_status", "active")])
    except FetchError:
        return None
    for e in found.get("events") or []:
        slug = e.get("slug") or ""
        if not slug.startswith(m.league + "-") or not e.get("startTime"):
            continue
        start = datetime.fromisoformat(e["startTime"].replace("Z", "+00:00"))
        if abs(start - m.start) > MAX_START_GAP:
            continue
        full = get_json(f"{GAMMA_API}/events", [("slug", slug)])
        if full:
            return full[0]
    return None


def fair_value(m: UsMarket, event: dict) -> float | None:
    """Probability that the US long side wins, from international bid/ask midpoints."""
    for im in event.get("markets") or []:
        if im.get("closed") or not im.get("acceptingOrders", True):
            continue
        bid, ask = im.get("bestBid"), im.get("bestAsk")
        if bid is None or ask is None or not 0 < float(bid) <= float(ask) < 1:
            continue
        mid0 = (float(bid) + float(ask)) / 2   # midpoint for outcome index 0
        try:
            outcomes = json.loads(im["outcomes"])
        except (KeyError, TypeError, ValueError):
            continue
        mtype = im.get("sportsMarketType")
        if mtype == "moneyline" and len(outcomes) == 2 and outcomes != ["Yes", "No"]:
            for i, name in enumerate(outcomes):
                if _names_match(name, m.long_name):
                    return mid0 if i == 0 else 1 - mid0
        if outcomes == ["Yes", "No"]:
            # Per-outcome soccer markets; the draw title also names both teams.
            title = im.get("groupItemTitle") or im.get("question") or ""
            is_draw = "draw" in title.lower()
            if m.is_draw and is_draw:
                return mid0
            if not m.is_draw and not is_draw and _names_match(title, m.long_name):
                return mid0
    return None
