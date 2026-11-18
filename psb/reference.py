"""Reference fair value for a Polymarket US market from international Polymarket.

This stands in for the signal the reports infer (a sportsbook feed, a game
model, a live read). It is a price from a related venue, not a forecast.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta

from .polydata import GAMMA_API, USER_AGENT, FetchError, get_json
from .usmarket import UsMarket

CLOB_API = "https://clob.polymarket.com"
MAX_START_GAP = timedelta(hours=3)
# A midpoint is only a usable fair value when the book is tight; an empty book
# (bid 0.01 / ask 0.99) has a meaningless 0.50 midpoint.
MAX_SPREAD = 0.04


# Polymarket US splits some international leagues: ITF men's and women's events
# are "itfme"/"itfwo" on US and one "itf" league internationally.
INTL_LEAGUE = {"itfme": "itf", "itfwo": "itf"}


def intl_league(m: UsMarket) -> str:
    return INTL_LEAGUE.get(m.league, m.league)


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _names_match(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a) and bool(b) and (a in b or b in a)


def _outcomes(im: dict) -> list[str]:
    try:
        return list(json.loads(im["outcomes"]))
    except (KeyError, TypeError, ValueError):
        return []


def _intl_names(event: dict) -> list[str]:
    names = [x.strip() for x in re.split(r"\s+vs\.?\s+", event.get("title") or "", flags=re.I)]
    for im in event.get("markets") or []:
        if im.get("sportsMarketType") == "moneyline":
            names += [o for o in _outcomes(im) if o not in ("Yes", "No")]
            names.append(im.get("groupItemTitle") or "")
    return [n for n in names if n]


def same_game(m: UsMarket, event: dict) -> bool:
    """Both US participants must appear in the international event, and start
    times must agree. Unconfirmed matches are rejected rather than guessed."""
    slug = event.get("slug") or ""
    if not slug.startswith(intl_league(m) + "-") or not event.get("startTime"):
        return False
    start = datetime.fromisoformat(event["startTime"].replace("Z", "+00:00"))
    if abs(start - m.start) > MAX_START_GAP:
        return False
    names = _intl_names(event)
    return len(m.participants) == 2 and all(
        any(_names_match(p, n) for n in names) for p in m.participants)


def _strip_city_code(name: str) -> str:
    """US titles prefix a city code ("NY Knicks", "TOR Raptors"); search works on the name."""
    head, _, rest = name.partition(" ")
    return rest if rest and head.isupper() and len(head) <= 4 else name


def search_queries(m: UsMarket) -> list[str]:
    qs = []
    if len(m.team_names) >= 2:
        qs.append(" ".join(m.team_names[:2]))
    if m.participants:
        qs.append(" ".join(_strip_city_code(p) for p in m.participants))
        qs.append(" ".join(m.participants))
    return list(dict.fromkeys(q for q in qs if q))


def find_event(m: UsMarket) -> dict | None:
    """International event for the same game, or None when it cannot be confirmed."""
    tried: set[str] = set()
    try:
        for q in search_queries(m):
            found = get_json(f"{GAMMA_API}/public-search",
                             [("q", q), ("limit_per_type", "8"), ("events_status", "active")])
            for e in found.get("events") or []:
                slug = e.get("slug") or ""
                if not slug.startswith(intl_league(m) + "-") or slug in tried:
                    continue
                tried.add(slug)
                full = get_json(f"{GAMMA_API}/events", [("slug", slug)])
                if full and same_game(m, full[0]):
                    return full[0]
    except FetchError:
        return None
    return None


@dataclass(frozen=True)
class Link:
    """US long side == international outcome `index` of `market`, or its complement."""
    market: dict
    index: int
    complement: bool

    @property
    def token_id(self) -> str | None:
        try:
            return json.loads(self.market["clobTokenIds"])[self.index]
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    def from_mid0(self, mid0: float) -> float:
        p = mid0 if self.index == 0 else 1 - mid0
        return 1 - p if self.complement else p


def _line(im: dict) -> float | None:
    try:
        return float(im["line"])
    except (KeyError, TypeError, ValueError):
        return None


def link(m: UsMarket, event: dict) -> Link | None:
    """Which international outcome the US long side corresponds to."""
    for im in event.get("markets") or []:
        if im.get("closed"):
            continue
        outs, mtype = _outcomes(im), im.get("sportsMarketType")
        if m.kind == "moneyline":
            if mtype == "moneyline" and len(outs) == 2 and outs != ["Yes", "No"]:
                for i, name in enumerate(outs):
                    if _names_match(name, m.long_name):
                        return Link(im, i, False)
            if mtype == "moneyline" and outs == ["Yes", "No"]:
                # Per-outcome soccer markets; the draw title also names both teams.
                title = im.get("groupItemTitle") or im.get("question") or ""
                is_draw = "draw" in title.lower()
                if (m.is_draw and is_draw) or (
                        not m.is_draw and not is_draw and _names_match(title, m.long_name)):
                    return Link(im, 0, False)
        elif m.kind == "spreads" and mtype == "spreads" and len(outs) == 2:
            ln = _line(im)
            if ln is None or m.line is None:
                continue
            # The international line applies to outcome 0. Team T covering L is
            # the complement of the opponent covering -L (half-point lines, no push).
            if _names_match(outs[0], m.long_name) and abs(ln - m.line) < 1e-9:
                return Link(im, 0, False)
            if _names_match(outs[1], m.long_name) and abs(ln + m.line) < 1e-9:
                return Link(im, 0, True)
        elif m.kind == "totals" and mtype == "totals" and outs == ["Over", "Under"]:
            ln = _line(im)
            if ln is not None and m.line is not None and abs(ln - m.line) < 1e-9:
                return Link(im, 0, False)
    return None


def _tight_mid(bid, ask) -> float | None:
    try:
        bid, ask = float(bid), float(ask)
    except (TypeError, ValueError):
        return None
    if not 0 < bid <= ask < 1 or ask - bid > MAX_SPREAD + 1e-9:
        return None
    return (bid + ask) / 2


def gamma_fair(ln: Link) -> float | None:
    """Fair value from the event snapshot (bestBid/bestAsk refer to outcome 0)."""
    if not ln.market.get("acceptingOrders", True):
        return None
    mid0 = _tight_mid(ln.market.get("bestBid"), ln.market.get("bestAsk"))
    return ln.from_mid0(mid0) if mid0 is not None else None


def _post_json(url: str, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise FetchError(f"POST {url}: {e}") from e


def live_mids(links: list[Link]) -> dict[str, float]:
    """Current midpoints from the international order books, keyed by token id."""
    tokens = sorted({t for t in (ln.token_id for ln in links) if t})
    out: dict[str, float] = {}
    for i in range(0, len(tokens), 50):
        chunk = tokens[i:i + 50]
        try:
            books = _post_json(f"{CLOB_API}/books", [{"token_id": t} for t in chunk])
        except FetchError:
            continue
        for b in books or []:
            bids = [float(x["price"]) for x in b.get("bids") or []]
            asks = [float(x["price"]) for x in b.get("asks") or []]
            mid = _tight_mid(max(bids) if bids else None, min(asks) if asks else None)
            if mid is not None:
                out[b["asset_id"]] = mid
    return out


def live_fair(ln: Link, mids: dict[str, float]) -> float | None:
    """The token's own midpoint is outcome `index`'s probability."""
    t = ln.token_id
    if t is None or t not in mids:
        return None
    return 1 - mids[t] if ln.complement else mids[t]
