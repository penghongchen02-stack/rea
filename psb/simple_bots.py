"""The 50 simple bot ideas, as paper bots where the data allows.

Every bot here trades a Polymarket US book against one signal the engine has:
the international Polymarket price for the same outcome (`View.fair`), the US
book itself, the clock, the game's start time, and the bot's own results. Ideas
that need data this project does not have (sportsbook feeds, injuries, weather,
lineups, game clocks, team ratings) are listed in IDEAS with the missing input
and are not traded. "Proxy" bots stand in for an idea with the data we have, and
say so in their description.

Defaults for every simple bot: $10 per entry, at most 3 entries and 3% of the
bankroll per market, prices 5c-95c.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import fees
from .bots import SIDES, Bot, Rest, Take, View, other
from .markets import category_of


@dataclass(frozen=True)
class Idea:
    n: int
    title: str
    status: str          # "running", "proxy", "needs data", "removed", "existing bots"
    note: str
    key: str | None = None


EPS = 1e-9   # prices are decimals: 0.60 - 0.50 is 0.0999..., which must count as 10c


def _mid(v: View) -> float | None:
    b, a = v.book.bid, v.book.ask
    return (b + a) / 2 if b is not None and a is not None else None


def _side_mid(v: View, side: str) -> float | None:
    m = _mid(v)
    if m is None:
        return None
    return m if side == "long" else 1 - m


def _spread(v: View) -> float | None:
    b, a = v.book.bid, v.book.ask
    return a - b if b is not None and a is not None else None


def _hours_to_start(v: View) -> float:
    return (v.m.start - v.now).total_seconds() / 3600


class Simple(Bot):
    """Buy-only taker with a per-market entry cap; subclasses say when and which side."""
    idea = 0
    title = ""
    kinds = ("moneyline",)
    max_market_frac = 0.03
    clip = 10.0
    max_entries = 3
    cats: frozenset[str] | None = None      # None: every sport
    live_only = False
    pregame_only = False
    price_floor, price_cap = 0.05, 0.95

    def wants(self, m, live: bool) -> bool:
        if m.kind not in self.kinds:
            return False
        return not (self.live_only and not live) and not (self.pregame_only and live)

    def pick(self, v: View, st: dict) -> tuple[str, float] | None:
        """(side, minimum edge after fee to accept), or None."""
        raise NotImplementedError

    def size(self, v: View, st: dict, side: str) -> float:
        return self.clip

    def remember(self, v: View, st: dict) -> None:
        """Keep the first and last seen US mid and fair value (for move-based ideas)."""
        mid = _mid(v)
        st.setdefault("fair0", v.fair_long)
        if mid is not None:
            st.setdefault("mid0", mid)
        st["mid_prev"], st["mid_last"] = st.get("mid_last"), mid

    def step(self, v: View, st: dict) -> list[Take | Rest]:
        self.remember(v, st)
        if self.cats is not None and v.category not in self.cats:
            return []
        if (st.get("fills_by_tag") or {}).get("entry", 0) >= self.max_entries:
            return []
        got = self.pick(v, st)
        if got is None:
            return []
        side, min_edge = got
        a, e = v.ask(side), v.taker_edge(side)
        if a is None or e is None or e < min_edge or not self.price_floor <= a <= self.price_cap:
            return []
        dollars = min(self.room(v, st), self.size(v, st, side))
        if dollars < 1:
            return []
        return [Take(side, dollars, round(min(v.fair(side) - min_edge, self.price_cap), 4), "entry")]


def best_edge_side(v: View) -> str | None:
    es = [(v.taker_edge(s), s) for s in SIDES if v.taker_edge(s) is not None]
    return max(es)[1] if es else None


# ---------------------------------------------------------------------------
# Price gaps


class S01Lag(Simple):
    key, idea, title = "s01-us-intl-lag", 1, "US vs international lag"
    live_only = True

    def pick(self, v, st):
        s = best_edge_side(v)
        return (s, 0.02) if s else None


class S02BookProxy(Simple):
    key, idea, title = "s02-sportsbook-proxy", 2, "Sportsbook comparison (proxy)"
    pregame_only = True

    def pick(self, v, st):
        s = best_edge_side(v)
        return (s, 0.02) if s else None


class S03ClosingLine(Simple):
    key, idea, title = "s03-closing-line", 3, "Closing-line chaser"
    pregame_only = True

    def pick(self, v, st):
        if not 6 <= _hours_to_start(v) <= 24:
            return None
        s = best_edge_side(v)
        return (s, 0.01) if s else None


class S07SequentialPair(Bot):
    """Buy one side at or below fair; later buy the other side when the pair
    costs at most $0.98 after fees (a single US book can never offer both
    sides below $1.00 at once)."""
    key, idea, title = "s07-underpriced-pair", 7, "Underpriced pair"
    max_market_frac = 0.03
    max_pair = 0.98

    def step(self, v, st):
        legs = st.get("legs") or {}
        held = [s for s in SIDES if (legs.get(s) or {}).get("contracts")]
        if not held:
            s = best_edge_side(v)
            if s is None or (v.taker_edge(s) or -1) < 0 or not 0.05 <= (v.ask(s) or 1) <= 0.95:
                return []
            return [Take(s, min(10.0, self.room(v, st)), v.fair(s), "first")]
        if len(held) == 2:
            return []
        first = held[0]
        n, vwap = self.leg(st, first)
        hedge = other(first)
        a = v.ask(hedge)
        if a is None or vwap + a + fees.taker_fee(1, a, v.taker_theta) > self.max_pair:
            return []
        return [Take(hedge, n * a, a, "second")]


# ---------------------------------------------------------------------------
# Market making


class Maker(Bot):
    """Rest a bid on each side `edge` under fair; the side that closes a held
    position rests no higher than max_pair minus what the held side cost."""
    kinds = ("moneyline",)
    edge = 0.02
    clip = 10.0
    max_inventory = 40.0     # $ cost of the held side
    max_pair = 0.985
    min_spread = 0.0
    min_traded = 0.0
    pregame_only = False

    def wants(self, m, live):
        return m.kind in self.kinds and not (self.pregame_only and live)

    def quote_ok(self, v: View) -> bool:
        sp = _spread(v)
        if sp is None or sp < self.min_spread:
            return False
        return (v.book.shares_traded or 0) >= self.min_traded

    def step(self, v, st):
        if not self.quote_ok(v):
            return []
        held = v.held_side()
        acts = []
        for side in SIDES:
            px = round(v.fair(side) - self.edge, 2)
            closing = held == other(side)
            if closing:
                _, entry = self.leg(st, held)
                px = min(px, round(self.max_pair - entry - 0.005, 2))
                dollars = abs(v.held) * px
            else:
                held_cost = abs(v.held) * (self.leg(st, side)[1] or 0) if held == side else 0.0
                if held_cost >= self.max_inventory:
                    continue
                dollars = min(self.clip, self.room(v, st))
            ask = v.ask(side)
            if not 0.05 <= px <= 0.95 or (ask is not None and px >= ask) or dollars < 1:
                continue
            acts.append(Rest(side, px, dollars, "quote"))
        return acts


class S09TwoSided(Maker):
    key, idea, title = "s09-two-sided-maker", 9, "Two-sided quotes"


class S10QuietMaker(Maker):
    key, idea, title = "s10-quiet-maker", 10, "Quiet-market maker"
    min_spread = 0.04


class S11PregameMaker(Maker):
    key, idea, title = "s11-pregame-maker", 11, "Pre-game-only maker"
    pregame_only = True


class S12VolumeMaker(Maker):
    key, idea, title = "s12-volume-maker", 12, "Volume maker"
    min_traded = 5000.0


class S13InventoryMaker(Maker):
    key, idea, title = "s13-inventory-maker", 13, "Inventory-limited maker"
    max_inventory = 10.0


# ---------------------------------------------------------------------------
# Live game


class S14OverreactionFade(Simple):
    """The US price moved 4c+ since the last poll while the international price
    moved under 1c: buy the side the US market just marked down."""
    key, idea, title = "s14-overreaction-fade", 14, "Comeback fade"
    live_only = True

    def pick(self, v, st):
        prev, now = st.get("mid_prev"), st.get("mid_last")
        if prev is None or now is None or v.prev_fair_long is None:
            return None
        if abs(v.fair_long - v.prev_fair_long) >= 0.01 - EPS or abs(now - prev) < 0.04 - EPS:
            return None
        return ("short" if now > prev else "long", 0.01)


class S15Momentum(Simple):
    """International price rose 3c+ since the last poll: buy that side while the
    US ask is still at or below it."""
    key, idea, title = "s15-momentum", 15, "Momentum follower"
    live_only = True
    jump = 0.03
    edge = 0.0             # minimum edge after the fee

    def pick(self, v, st):
        if v.prev_fair_long is None:
            return None
        d = v.fair_long - v.prev_fair_long
        if abs(d) < self.jump - EPS:
            return None
        return ("long" if d > 0 else "short", self.edge)


class S16GarbageTime(Simple):
    key, idea, title = "s16-garbage-time", 16, "Garbage-time closer"
    live_only = True
    price_cap = 0.98

    def pick(self, v, st):
        for s in SIDES:
            if v.fair(s) >= 0.95 and (v.ask(s) or 1) <= 0.97:
                return (s, 0.005)
        return None


class S18TennisMomentum(S15Momentum):
    key, idea, title = "s18-tennis-momentum", 18, "Tennis break-point (proxy)"
    cats = frozenset({"tennis"})
    jump = 0.05


class S19TennisSetFade(Simple):
    """Live tennis: a player whose price fell 15c+ from where we first saw it,
    bought only while the US ask is under the international price."""
    key, idea, title = "s19-tennis-set-fade", 19, "Tennis set-winner fade (proxy)"
    cats = frozenset({"tennis"})
    live_only = True
    drop = 0.15
    edge = 0.01            # minimum edge after the fee

    def fast_watch(self, m, st):
        # Only after a normal poll has recorded the first price (fair0), and while
        # an entry is still allowed.
        if self.cats is not None and category_of(m.event_slug) not in self.cats:
            return False
        if not st or st.get("fair0") is None:
            return False
        return (st.get("fills_by_tag") or {}).get("entry", 0) < self.max_entries

    def fast_signal(self, fair_long, st):
        return abs(fair_long - st["fair0"]) >= self.drop - EPS

    def pick(self, v, st):
        f0 = st.get("fair0")
        if f0 is None:
            return None
        if f0 - v.fair_long >= self.drop - EPS:
            return ("long", self.edge)
        if v.fair_long - f0 >= self.drop - EPS:
            return ("short", self.edge)
        return None


# ---------------------------------------------------------------------------
# Sizing and risk overlays, all on one base signal: 1c+ edge after the fee.


class S39Flat(Simple):
    key, idea, title = "s39-flat-10", 39, "Flat-$10 baseline"
    max_entries = 1

    def pick(self, v, st):
        s = best_edge_side(v)
        return (s, 0.01) if s else None


class S38Kelly(S39Flat):
    key, idea, title = "s38-kelly", 38, "Quarter-Kelly sizer"

    def size(self, v, st, side):
        a = v.ask(side) or 1
        e = v.fair(side) - a
        return max(0.0, v.bankroll * 0.25 * e / max(1 - a, 1e-6))


class S40DailyStop(S39Flat):
    key, idea, title = "s40-daily-stop", 40, "Daily stop-loss"
    needs_stats = True
    stop = 50.0

    def pick(self, v, st):
        if v.stats and v.stats["pnl_today"] <= -self.stop:
            return None
        return super().pick(v, st)


class S41StreakCooler(S39Flat):
    key, idea, title = "s41-streak-cooler", 41, "Win-streak cooler"
    needs_stats = True

    def size(self, v, st, side):
        last = (v.stats or {}).get("last_results") or []
        return self.clip / 2 if len(last) >= 3 and all(x > 0 for x in last[-3:]) else self.clip


class S42SportBudget(S39Flat):
    key, idea, title = "s42-sport-budget", 42, "Per-sport budget"
    needs_stats = True
    budget = 150.0

    def pick(self, v, st):
        if ((v.stats or {}).get("open_by_cat") or {}).get(v.category, 0.0) >= self.budget:
            return None
        return super().pick(v, st)


# ---------------------------------------------------------------------------
# Simple statistical rules


class S46Favourite(Simple):
    key, idea, title = "s46-favourite", 46, "Favourite-longshot bot"

    def pick(self, v, st):
        for s in SIDES:
            if 0.70 <= (v.ask(s) or 1) <= 0.85:
                return (s, 0.005)
        return None


class S47MeanReversion(Simple):
    """The US price moved 10c+ from where we first saw it while the international
    price moved under 3c: buy the side the US market marked down."""
    key, idea, title = "s47-mean-reversion", 47, "Mean-reversion bot"

    def pick(self, v, st):
        m0, now, f0 = st.get("mid0"), st.get("mid_last"), st.get("fair0")
        if m0 is None or now is None or f0 is None:
            return None
        if abs(now - m0) < 0.10 - EPS or abs(v.fair_long - f0) >= 0.03 - EPS:
            return None
        return ("short" if now > m0 else "long", 0.01)


# ---------------------------------------------------------------------------
# Ensemble


class S50Ensemble(Simple):
    """Bet only when at least 3 of these simple bots would buy the same side now."""
    key, idea, title = "s50-ensemble", 50, "Ensemble bot"
    members = (S01Lag, S15Momentum, S16GarbageTime, S39Flat, S46Favourite, S47MeanReversion,
               S14OverreactionFade)

    def pick(self, v, st):
        votes = {"long": 0, "short": 0}
        for cls in self.members:
            b = cls()
            if not b.wants(v.m, v.live) or (b.cats is not None and v.category not in b.cats):
                continue
            got = b.pick(v, st)
            if got and (v.taker_edge(got[0]) or -1) >= got[1]:
                votes[got[0]] += 1
        side = max(votes, key=votes.get)
        return (side, 0.0) if votes[side] >= 3 else None


RUNNING = (S01Lag(), S02BookProxy(), S03ClosingLine(), S07SequentialPair(), S09TwoSided(),
           S10QuietMaker(), S11PregameMaker(), S12VolumeMaker(), S13InventoryMaker(),
           S14OverreactionFade(), S15Momentum(), S16GarbageTime(), S18TennisMomentum(),
           S19TennisSetFade(), S38Kelly(), S39Flat(), S40DailyStop(), S41StreakCooler(),
           S42SportBudget(), S46Favourite(), S47MeanReversion(), S50Ensemble())

_PROXY = {2: "International Polymarket price stands in for sportsbook odds.",
          18: "No point-by-point data: a 5c live price jump in tennis stands in for a break.",
          19: "No set scores: a 15c fall from the first price seen stands in for a lost set.",
}
_RUNNING_NOTE = {
    1: "Live: US ask 2c+ under the international price after the fee.",
    3: "6-24 hours before start: 1c+ edge after the fee.",
    7: "Buy one side at or under fair, then the other when the pair costs $0.98 or less.",
    9: "Bids 2c under fair on both sides; closes pairs at $0.985 or less.",
    10: "As 9, only where the US spread is 4c or wider.",
    11: "As 9, before the game only.",
    12: "As 9, only on markets that traded 5,000+ contracts.",
    13: "As 9, holding at most $10 per market.",
    14: "Live: US price moved 4c+ in one poll while international moved under 1c; buy the side marked down.",
    15: "Live: international price jumped 3c+; buy that side at or under fair.",
    16: "Live: buy a 95c+ favourite at 97c or less.",
    38: "1c+ edge, sized at a quarter of the Kelly stake (at most 3% of bankroll per market).",
    39: "One $10 bet per market with 1c+ edge after the fee.",
    40: "As 39, but stops for the day after losing $50 on games settled today.",
    41: "As 39, half size after three winning games in a row.",
    42: "As 39, at most $150 open per sport.",
    46: "Buy a favourite priced 70-85c with 0.5c+ edge.",
    47: "US price moved 10c+ since first seen while international moved under 3c; buy the side marked down.",
    50: "Buy when 3+ of bots 1, 14, 15, 16, 39, 46, 47 agree.",
}
# Removed 2026-11-18: built and run, but no bet in their first hours. See git
# history for their code.
_REMOVED = {
    20: ("Esports map-1 fade", "Removed: no bets (stand-in for a lost map: a 15c fall from the first price)."),
    21: ("Red-card bot", "Removed: no bets (stand-in for a red card: an 8c live jump in soccer)."),
    27: ("College basketball home bot", "Removed: no bets (home team at or under fair)."),
    28: ("Soccer draw bot", "Removed: no bets (draw with 1c+ edge before the game)."),
    35: ("Overnight-gap bot", "Removed: no bets (03-10 UTC, 2c+ edge)."),
    36: ("Weekend-volume bot", "Removed: no bets (Friday-Sunday only, tight spread)."),
    37: ("Early-line bot", "Removed: no bets (12h+ before start, wide spread)."),
    45: ("Home-underdog bot", "Removed: no bets (home underdog at 35-45c)."),
}
_NEEDS = {
    4: ("Same-game consistency", "Compares a game's winner, spread and total markets with each other; the engine prices each market alone."),
    5: ("Spread-to-winner check", "Needs a spread-to-win-probability model and the two markets side by side."),
    6: ("Total-lines check", "Polymarket US lists one main total per game here, so there are no neighbouring lines to compare."),
    8: ("Draw market check", "Compares a soccer game's three markets with each other; the engine prices each alone."),
    17: ("Late-game total", "Needs the live score and game clock."),
    22: ("Halftime reset", "Needs the live score and game clock."),
    23: ("NBA rest-day bot", "Needs each team's schedule (back-to-back games)."),
    24: ("NFL weather unders", "Needs a weather feed."),
    25: ("MLB starting-pitcher bot", "Needs probable-pitcher announcements (and MLB is out of season)."),
    26: ("NHL goalie bot", "Needs starting-goalie announcements."),
    29: ("Tennis surface bot", "Needs players' records by surface."),
    30: ("Tennis fatigue bot", "Needs players' recent match durations."),
    31: ("UFC short-notice fade", "Needs fight-booking dates."),
    32: ("Esports patch bot", "Needs game-patch dates and team results by patch."),
    33: ("Injury-news bot", "Needs an injury news feed."),
    34: ("Lineup-release bot", "Needs a starting-lineup feed."),
    43: ("Elo rating bot", "Needs a history of results to build team ratings."),
    44: ("Poisson soccer model", "Needs expected-goals data per team."),
    48: ("Rest-and-travel model", "Needs team schedules and travel distances."),
}


def _ideas() -> list[Idea]:
    out = []
    for b in RUNNING:
        status = "proxy" if b.idea in _PROXY else "running"
        out.append(Idea(b.idea, b.title, status, _PROXY.get(b.idea) or _RUNNING_NOTE.get(b.idea, ""), b.key))
    for n, (title, note) in _NEEDS.items():
        out.append(Idea(n, title, "needs data", note))
    for n, (title, note) in _REMOVED.items():
        out.append(Idea(n, title, "removed", note))
    out.append(Idea(49, "Whale-follower", "existing bots",
                    "The 7 original bots and their v2 versions copy winning wallets."))
    return sorted(out, key=lambda i: i.n)


IDEAS = _ideas()
assert [i.n for i in IDEAS] == list(range(1, 51)), "every idea 1-50 is listed once"
