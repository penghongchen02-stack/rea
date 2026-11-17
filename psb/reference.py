"""Reference fair value for a Polymarket US market from international Polymarket.

This stands in for the sportsbook feed the reports infer (Pinnacle, Betfair,
OddsJam). It is a price from a related venue, not an independent forecast.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime, timedelta

from .polydata import GAMMA_API, FetchError, get_json
from .usmarket import UsMarket

MAX_START_GAP = timedelta(hours=3)
# A midpoint is only a usable fair value when the book is tight; an empty book
# (bid 0.01 / ask 0.99) has a meaningless 0.50 midpoint.
MAX_SPREAD = 0.04


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _names_match(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a) and bool(b) and (a in b or b in a)


def _intl_names(event: dict) -> list[str]:
    names = [x.strip() for x in re.split(r"\s+vs\.?\s+", event.get("title") or "", flags=re.I)]
    for im in event.get("markets") or []:
        if im.get("sportsMarketType") == "moneyline":
            try:
                names += [o for o in json.loads(im["outcomes"]) if o not in ("Yes", "No")]
            except (KeyError, TypeError, ValueError):
                pass
            names.append(im.get("groupItemTitle") or "")
    return [n for n in names if n]


def same_game(m: UsMarket, event: dict) -> bool:
    """Both US participants must appear in the international event, and start
    times must agree. Unconfirmed matches are rejected rather than guessed."""
    slug = event.get("slug") or ""
    if not slug.startswith(m.league + "-") or not event.get("startTime"):
        return False
    start = datetime.fromisoformat(event["startTime"].replace("Z", "+00:00"))
    if abs(start - m.start) > MAX_START_GAP:
        return False
    names = _intl_names(event)
    return len(m.participants) == 2 and all(
        any(_names_match(p, n) for n in names) for p in m.participants)


def find_event(m: UsMarket) -> dict | None:
    """International event for the same game, or None when it cannot be confirmed."""
    q = " ".join(m.participants or m.team_names or (m.long_name,))
    try:
        found = get_json(f"{GAMMA_API}/public-search",
                         [("q", q), ("limit_per_type", "8"), ("events_status", "active")])
        for e in found.get("events") or []:
            if not (e.get("slug") or "").startswith(m.league + "-"):
                continue
            full = get_json(f"{GAMMA_API}/events", [("slug", e["slug"])])
            if full and same_game(m, full[0]):
                return full[0]
    except FetchError:
        return None
    return None


def fair_value(m: UsMarket, event: dict) -> float | None:
    """Probability that the US long side wins, from international bid/ask midpoints."""
    for im in event.get("markets") or []:
        if im.get("closed") or not im.get("acceptingOrders", True):
            continue
        bid, ask = im.get("bestBid"), im.get("bestAsk")
        if bid is None or ask is None or not 0 < float(bid) <= float(ask) < 1:
            continue
        if float(ask) - float(bid) > MAX_SPREAD + 1e-9:
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
