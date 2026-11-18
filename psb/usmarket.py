"""Read-only Polymarket US public market data (https://gateway.polymarket.us).

Only unauthenticated GET endpoints are used. This module has no order path.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from .polydata import FetchError, get_json

GATEWAY = "https://gateway.polymarket.us"
# Docs: 25 requests/s shared per IP. Stay well under it.
_MIN_INTERVAL = 1 / 12
_lock = threading.Lock()
_last = 0.0

# Lower-level periods (halves, quarters, sets, maps) are left out: the bots
# trade full-game lines only.
_PARTIAL = re.compile(r"half|quarter|period|inning|set_\d|_set|map_\d|game_\d|first|1st|2nd|3rd")


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
    kind: str            # "moneyline", "spreads" or "totals"
    long_name: str       # what a long position wins on: team, "Draw" or "Over"
    line: float | None   # spread for the long team (e.g. -3.5) or total (e.g. 216.5)
    team_names: tuple[str, ...]  # team objects on the event's market sides (may be partial)
    participants: tuple[str, ...]  # both sides from the event title
    is_draw: bool
    start: datetime
    tick: float


def participants_of(title: str) -> tuple[str, ...]:
    parts = [p.strip() for p in re.split(r"\s+vs\.?\s+", title or "", flags=re.I)]
    return tuple(p for p in parts if p) if len(parts) == 2 else ()


def _parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _market(m: dict, e: dict, league: str, teams: tuple[str, ...], start: datetime,
            main_spread: float | None, main_total: float | None) -> UsMarket | None:
    if m.get("closed") or not m.get("active", True):
        return None
    mtype = m.get("marketType")
    smt = (m.get("sportsMarketType") or "").lower()
    longs = [s for s in m.get("marketSides") or [] if s.get("long")]
    if not longs:
        return None
    long_side = longs[0]
    team = (long_side.get("team") or {}).get("safeName")
    line = m.get("line")
    if mtype in ("moneyline", "drawable_outcome"):
        kind, is_draw = "moneyline", m["slug"].endswith("-draw")
        name = team or ("Draw" if is_draw else None)
        line = None
    elif mtype == "spreads" and not _PARTIAL.search(smt):
        if line is None or main_spread is None or abs(abs(float(line)) - abs(main_spread)) > 1e-9:
            return None
        kind, is_draw, name, line = "spreads", False, team, float(line)
    elif mtype == "totals" and not _PARTIAL.search(smt):
        if line is None or main_total is None or abs(float(line) - main_total) > 1e-9:
            return None
        if (long_side.get("description") or "").lower() != "over":
            return None
        kind, is_draw, name, line = "totals", False, "Over", float(line)
    else:
        return None
    if not name:
        return None
    return UsMarket(m["slug"], e["slug"], league, kind, name, line, teams,
                    participants_of(e.get("title", "")), is_draw, start,
                    float(m.get("orderPriceMinTickSize") or 0.01))


def main_line(markets: list[dict], mtype: str) -> float | None:
    """Full-game line whose long price is closest to 0.50, when the event does
    not state one (NFL and soccer events carry no eventState)."""
    best: tuple[float, float] | None = None
    for m in markets:
        if m.get("marketType") != mtype or m.get("line") is None or m.get("closed"):
            continue
        if _PARTIAL.search((m.get("sportsMarketType") or "").lower()):
            continue
        longs = [sd for sd in m.get("marketSides") or [] if sd.get("long")]
        try:
            px = float(longs[0]["price"])
        except (IndexError, KeyError, TypeError, ValueError):
            continue
        cand = (abs(px - 0.5), abs(float(m["line"])) if mtype == "spreads" else float(m["line"]))
        if best is None or cand < best:
            best = cand
    return best[1] if best else None


def discover(league: str, with_lines: bool, limit: int = 100,
             starts: tuple[datetime, datetime] | None = None) -> list[UsMarket]:
    """Open full-game markets for one US league: game winners, and with_lines
    adds the main spread (both sides of the main line) and the main total.
    `starts` keeps only events starting inside that window, before any per-event
    request: each line lookup is one more call, and live polling waits on them."""
    body = _get(f"/v2/leagues/{league}/events", [("limit", str(limit))])
    out: list[UsMarket] = []
    for e in body.get("events") or []:
        if e.get("closed") or not e.get("active"):
            continue
        start_s = e.get("startTime") or e.get("startDate")
        if not start_s:
            continue
        start = _parse_time(start_s)
        if starts and not starts[0] <= start <= starts[1]:
            continue
        markets = e.get("markets") or []
        state = e.get("eventState") or {}
        main_spread, main_total = state.get("mainSpreadLine"), state.get("mainTotalLine")
        if with_lines:
            try:
                detail = _get(f"/v1/events/slug/{e['slug']}")
                markets = (detail.get("event") or detail).get("markets") or markets
            except FetchError:
                pass
            if main_spread is None:
                main_spread = main_line(markets, "spreads")
            if main_total is None:
                main_total = main_line(markets, "totals")
        teams: list[str] = []
        for m in markets:
            for s in m.get("marketSides") or []:
                name = (s.get("team") or {}).get("safeName")
                if name and name not in teams:
                    teams.append(name)
        for m in markets:
            um = _market(m, e, league, tuple(teams), start,
                         float(main_spread) if main_spread is not None else None,
                         float(main_total) if main_total is not None else None)
            if um:
                out.append(um)
    return out


@dataclass(frozen=True)
class Book:
    """Long-instrument book. Levels are (price, contracts), best first."""
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    state: str | None
    last_trade: float | None
    shares_traded: float | None

    @property
    def bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None

    def side_asks(self, side: str) -> tuple[tuple[float, float], ...]:
        """Prices to buy `side` now: long buys lift asks, short buys hit bids at 1 - bid."""
        if side == "long":
            return self.asks
        return tuple((round(1 - p, 6), q) for p, q in self.bids)

    def side_best_bid(self, side: str) -> float | None:
        """Best resting price someone already pays for `side`."""
        if side == "long":
            return self.bid
        return round(1 - self.ask, 6) if self.ask is not None else None


def _amount(x) -> float | None:
    if isinstance(x, dict):
        x = x.get("value")
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def book(slug: str) -> Book:
    md = _get(f"/v1/markets/{slug}/book").get("marketData") or {}

    def levels(raw, reverse):
        lv = []
        for x in raw or []:
            px, qty = _amount(x.get("px")), _amount(x.get("qty"))
            if px is not None and qty and 0 < px < 1:
                lv.append((px, qty))
        return tuple(sorted(lv, key=lambda t: t[0], reverse=reverse))

    stats = md.get("stats") or {}
    return Book(levels(md.get("bids"), True), levels(md.get("offers"), False), md.get("state"),
                _amount(stats.get("lastTradePx")), _amount(stats.get("sharesTraded")))


def settlement(slug: str) -> float | None:
    """Long-side settlement price, or None while the market is unsettled."""
    try:
        body = _get(f"/v1/markets/{slug}/settlement")
    except FetchError:
        return None
    value = body.get("settlement")
    return float(value) if value is not None else None
