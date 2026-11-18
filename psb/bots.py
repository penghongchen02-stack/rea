"""The seven bots, each following its PR&R report playbook.

Dollar amounts in the reports assume $100K-$5M books. Here every bot has a
$1,000 paper bankroll, so sizes keep the reports' ratios (probe vs anchor,
dominant vs hedge, per-market cap as a share of bankroll) at a smaller scale.

What no report discloses is the signal that picks the side: a sportsbook feed,
a game-outcome model, a live read of the match. Every bot uses the matching
international Polymarket price as that signal (`View.fair`).

Polymarket US holds one side per market: buying the other side cashes out the
held side. "Both sides" in the reports therefore means buying one side, then
later buying the other side, which closes the position at the paired cost.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import fees
from .markets import ESPORTS, category_of
from .strategies import STRATEGIES, Candidate
from .usmarket import Book, UsMarket

SIDES = ("long", "short")


def other(side: str) -> str:
    return "short" if side == "long" else "long"


@dataclass
class View:
    """What a bot sees for one market on one poll."""
    m: UsMarket
    book: Book
    fair_long: float           # international fair value of the long side
    prev_fair_long: float | None
    live: bool
    now: datetime
    bankroll: float
    cash: float                # available cash for this bot
    held: float                # signed contracts: >0 long, <0 short
    taker_theta: float
    # Other lines of the same game and market type (e.g. another total) where
    # this bot already holds, held, or rests an order. Only filled in for bots
    # that set `one_line_per_game`.
    siblings: int = 0

    @property
    def hour(self) -> int:
        return self.now.hour

    @property
    def category(self) -> str:
        return category_of(self.m.event_slug)

    def fair(self, side: str) -> float:
        return self.fair_long if side == "long" else 1 - self.fair_long

    def prev_fair(self, side: str) -> float | None:
        if self.prev_fair_long is None:
            return None
        return self.prev_fair_long if side == "long" else 1 - self.prev_fair_long

    def ask(self, side: str) -> float | None:
        lv = self.book.side_asks(side)
        return lv[0][0] if lv else None

    def bid(self, side: str) -> float | None:
        return self.book.side_best_bid(side)

    def taker_edge(self, side: str) -> float | None:
        """Fair value minus the price to buy now, after the US taker fee."""
        a = self.ask(side)
        if a is None:
            return None
        return self.fair(side) - a - fees.taker_fee(1, a, self.taker_theta)

    def held_side(self) -> str | None:
        if self.held > 1e-9:
            return "long"
        if self.held < -1e-9:
            return "short"
        return None

    def accepts(self, key: str, side: str, price: float) -> bool:
        return STRATEGIES[key].accept(Candidate(self.category, self.m.kind, "BUY", price, self.hour))


@dataclass(frozen=True)
class Take:
    """Buy `side` now, walking the book up to `limit`, for at most `dollars`."""
    side: str
    dollars: float
    limit: float
    tag: str


@dataclass(frozen=True)
class Rest:
    """Rest a bid for `side` at `price` for `dollars`; replaced every poll."""
    side: str
    price: float
    dollars: float
    tag: str


@dataclass
class Fill:
    side: str
    price: float
    contracts: float
    tag: str


class Bot:
    key: str = ""
    rules: str = ""                 # entry filters (strategies.py) to use; default: key
    one_line_per_game: bool = False
    trades_live: bool = True
    trades_pregame: bool = True
    kinds: tuple[str, ...] = ("moneyline",)
    max_market_frac: float = 0.10   # per-market cap as a share of bankroll

    @property
    def rules_key(self) -> str:
        return self.rules or self.key

    def wants(self, m: UsMarket, live: bool) -> bool:
        if m.kind not in self.kinds:
            return False
        return self.trades_live if live else self.trades_pregame

    def step(self, v: View, st: dict) -> list[Take | Rest]:
        raise NotImplementedError

    def on_fill(self, st: dict, f: Fill, now: datetime) -> None:
        legs = st.setdefault("legs", {})
        leg = legs.setdefault(f.side, {"contracts": 0.0, "cost": 0.0})
        leg["contracts"] += f.contracts
        leg["cost"] += f.contracts * f.price
        st.setdefault("first_fill", now.isoformat())
        st.setdefault("fills_by_tag", {}).setdefault(f.tag, 0)
        st["fills_by_tag"][f.tag] += 1

    @staticmethod
    def leg(st: dict, side: str) -> tuple[float, float]:
        leg = (st.get("legs") or {}).get(side) or {}
        n = leg.get("contracts", 0.0)
        return n, (leg.get("cost", 0.0) / n if n else 0.0)

    def spent(self, st: dict) -> float:
        return sum(l["cost"] for l in (st.get("legs") or {}).values())

    def room(self, v: View, st: dict) -> float:
        return max(0.0, min(v.cash, v.bankroll * self.max_market_frac - self.spent(st)))


# ---------------------------------------------------------------------------
# Paired both-sides bots: FERRARICHAMPIONS2026, RANDOMGAMEBET, GAMBLINGISALLYOURNEED


def dominance_from_edge(edge: float) -> float:
    """Stand-in for the reports' dominance ratio: larger fair-value edge, more tilt."""
    if edge >= 0.05:
        return 3.0
    if edge >= 0.03:
        return 2.0
    if edge >= 0.015:
        return 1.5
    return 1.0


class Ferrari(Bot):
    """Report playbook: enter the dominant side first (the side the model favors,
    confidence >= 50%), sized by dominance (1x, 1.5x, 2.5x, 5x base clip); hedge
    the other side within 15 minutes only while the paired cost stays < $1.00
    (target < $0.97); otherwise let the dominant leg ride. Tennis, MLB, soccer,
    UFC, NBA regular season; moneyline, totals and spreads; walk the book."""
    key = "ferrarichampions2026"
    kinds = ("moneyline", "spreads", "totals")
    base_frac = 0.01            # base clip = 1% of bankroll ($10 of $1,000)
    hedge_window_s = 900
    max_pair = 1.00

    def step(self, v: View, st: dict) -> list[Take | Rest]:
        acts: list[Take | Rest] = []
        dom = st.get("dominant")
        if dom is None:
            side = "long" if v.fair_long >= 0.5 else "short"
            price = v.bid(side) or v.ask(side)
            if price is None or not v.accepts(self.rules_key, side, price):
                return []
            mult = self.dominant_mult(v, side)
            dollars = min(self.room(v, st), v.bankroll * self.base_frac * mult)
            if dollars < 1:
                return []
            st["planned_mult"] = mult
            # 35% of the reference book's fills were taker fills: join the bid,
            # take only when the ask is at or below fair after fee.
            te = v.taker_edge(side)
            if te is not None and te >= 0:
                acts.append(Take(side, dollars, v.fair(side), "dominant"))
            elif v.bid(side) is not None:
                acts.append(Rest(side, v.bid(side), dollars, "dominant"))
            return acts
        n_dom, vwap = self.leg(st, dom)
        hedge = other(dom)
        n_hedge, _ = self.leg(st, hedge)
        age = (v.now - datetime.fromisoformat(st["dominant_at"])).total_seconds()
        if n_dom <= 0 or age > self.hedge_window_s:
            return []   # window expired: the dominant leg rides as pure directional
        # Hedge clip = base clip in dollars; never more contracts than the dominant leg.
        _, h_vwap = self.leg(st, hedge)
        left_usd = v.bankroll * self.base_frac - n_hedge * h_vwap
        if left_usd <= 0.5 or n_hedge >= n_dom:
            return []
        max_hedge_px = round(self.max_pair - vwap - 0.001, 4)   # paired cost < $1.00
        gold_px = round(0.97 - vwap, 2)                         # target < $0.97
        a = v.ask(hedge)
        if a is not None and a <= max_hedge_px:
            acts.append(Take(hedge, min(left_usd, (n_dom - n_hedge) * a), max_hedge_px, "hedge"))
        elif gold_px > 0:
            acts.append(Rest(hedge, gold_px, min(left_usd, (n_dom - n_hedge) * gold_px), "hedge"))
        return acts

    def dominant_mult(self, v: View, side: str) -> float:
        edge = v.fair(side) - (v.ask(side) or 1)
        return {1.0: 1.0, 1.5: 1.5, 2.0: 2.5, 3.0: 5.0}[dominance_from_edge(edge)]

    def on_fill(self, st: dict, f: Fill, now: datetime) -> None:
        super().on_fill(st, f, now)
        if f.tag == "dominant" and "dominant" not in st:
            st["dominant"], st["dominant_at"] = f.side, now.isoformat()


class RandomGameBet(Bot):
    """Report playbook: live esports only (LoL, Dota 2, Valorant, CS2), skip
    06-07 UTC. Take the market ask on the dominant side (model side), then fill
    the hedge during the live match whenever it brings the paired cost below
    $0.975 (ideally < $0.97). Dominance splits 50/50, 60/40, 75/25, 85/15 with
    clips $10-25 up to $100-500 at $150K; |edge| < 3% means spread capture only."""
    key = "randomgamebet"
    trades_pregame = False
    max_pair = 0.975
    precheck = 0.98
    # (dominant $, hedge $) per dominance tier at a $1,000 bankroll
    clips = {1.0: (10, 10), 1.5: (15, 10), 2.0: (30, 10), 3.0: (50, 9)}

    def step(self, v: View, st: dict) -> list[Take | Rest]:
        dom = st.get("dominant")
        if dom is None:
            side = "long" if v.fair_long >= 0.5 else "short"
            a = v.ask(side)
            if a is None or not v.accepts(self.rules_key, side, a):
                return []
            # Report pre-check: skip when the expected paired cost (this ask plus
            # the other side at fair value) exceeds $0.98; no hedge could follow.
            if a + v.fair(other(side)) > self.precheck:
                return []
            edge = v.fair(side) - a
            tier = dominance_from_edge(edge) if edge >= 0.03 else 1.0
            d_usd, h_usd = self.clips[tier]
            st["plan"] = [d_usd, h_usd]
            return [Take(side, min(self.room(v, st), d_usd), a + 0.03, "dominant")]  # 3 ticks
        n_dom, vwap = self.leg(st, dom)
        hedge = other(dom)
        _, h_usd = st.get("plan", [10, 10])
        spent_h = self.leg(st, hedge)[0] * self.leg(st, hedge)[1]
        if n_dom <= 0 or spent_h >= h_usd:
            return []
        max_px = round(self.max_pair - vwap, 4)
        if max_px <= 0:
            return []
        a = v.ask(hedge)
        if a is not None and a <= max_px:
            return [Take(hedge, h_usd - spent_h, max_px, "hedge")]
        return [Rest(hedge, round(min(max_px, 0.97 - vwap), 2), h_usd - spent_h, "hedge")]

    def on_fill(self, st: dict, f: Fill, now: datetime) -> None:
        super().on_fill(st, f, now)
        if f.tag == "dominant" and "dominant" not in st:
            st["dominant"] = f.side


class Gambling(Bot):
    """Report: always-on pseudo market maker. Quotes both sides around fair value
    so pairs close near the observed median paired cost ($0.968), tilts toward
    the side the model favors (dominance >= 2x is the profitable subset), and
    manages positions with an exit engine rather than holding everything.
    Report filters: skip $0.40-0.50, skip 10, 12, 13 UTC; best soccer, MLB, NFL, CBB."""
    key = "gamblingisallyourneed"
    kinds = ("moneyline", "spreads", "totals")
    half_spread = 0.016          # each side ~1.6c under fair -> pair ~ $0.968
    clip_frac = 0.01

    def step(self, v: View, st: dict) -> list[Take | Rest]:
        acts: list[Take | Rest] = []
        held = v.held_side()
        for side in SIDES:
            px = round(v.fair(side) - self.half_spread, 2)
            best_ask = v.ask(side)
            if px <= 0 or (best_ask is not None and px >= best_ask):
                continue
            closing = held == other(side)
            if not closing and not v.accepts(self.rules_key, side, px):
                continue
            edge = v.fair(side) - (best_ask or 1)
            mult = 2.0 if dominance_from_edge(max(edge, 0) + self.half_spread) >= 2.0 else 1.0
            dollars = v.bankroll * self.clip_frac * mult
            if closing:
                dollars = abs(v.held) * px   # exit engine: close what we hold
            elif self.room(v, st) < dollars:
                continue
            acts.append(Rest(side, px, dollars, "quote"))
        return acts


# ---------------------------------------------------------------------------
# Probe-then-anchor bots: SWISSTONY, SINENOONEEI, KCH123


class ProbeAnchor(Bot):
    probe_usd = 5.0
    min_edge = 0.01              # after fee
    probes_before_anchor = 3
    max_probes = 30
    anchor_price_cap = 1.0
    # A cent of "edge" on a 1-cent long shot is noise: trade 5c-95c only.
    price_floor = 0.05
    price_cap = 0.95
    abort_move = 0.05            # stop adding if fair moves this far against the thesis
    buy_only = True

    def size_scale(self, v: View) -> float:
        return 1.0

    def anchor_dollars(self, v: View, st: dict, side: str) -> float:
        return 0.0

    def step(self, v: View, st: dict) -> list[Take | Rest]:
        if st.get("aborted"):
            return []
        side = st.get("thesis")
        if side is None:
            cands = [(v.taker_edge(s), s) for s in SIDES if v.taker_edge(s) is not None]
            cands = [(e, s) for e, s in cands if e >= self.min_edge]
            if not cands:
                return []
            _, side = max(cands)
            # Live entries need the edge on two polls in a row (reads of two
            # venues are seconds apart while prices move).
            if v.live and (v.prev_fair(side) is None or
                           v.prev_fair(side) - (v.ask(side) or 1) < self.min_edge):
                return []
        a = v.ask(side)
        if (a is None or not self.price_floor <= a <= self.price_cap
                or not v.accepts(self.rules_key, side, a)):
            return []
        if "thesis_fair" in st and st["thesis_fair"] - v.fair(side) >= self.abort_move:
            st["aborted"] = True
            return []
        e = v.taker_edge(side)
        if e is None or e < self.min_edge:
            return []
        scale = self.size_scale(v)
        if scale <= 0:
            return []
        probes = (st.get("fills_by_tag") or {}).get("probe", 0)
        anchored = (st.get("fills_by_tag") or {}).get("anchor", 0) > 0
        room = self.room(v, st)
        if probes >= self.probes_before_anchor and not anchored and a <= self.anchor_price_cap:
            dollars = min(room, self.anchor_dollars(v, st, side) * scale)
            if dollars >= 1:
                st.setdefault("thesis", side)
                return [Take(side, dollars, v.fair(side) - self.min_edge, "anchor")]
        if probes < self.max_probes:
            dollars = min(room, self.probe_usd * scale)
            if dollars >= 1:
                st.setdefault("thesis", side)
                st.setdefault("thesis_fair", v.fair(side))
                return [Take(side, dollars, v.fair(side) - self.min_edge, "probe")]
        return []


class SwissTony(ProbeAnchor):
    """Report: always-on fair-value comparator across all sports; small probes
    (median $9.85) then edge-proportional, Kelly-adjacent fills with a hard
    ceiling; BUY only, hold to resolution; opportunistic hedge later. Report
    filters: no NBA, skip $0.40-0.50, skip 18, 20, 21 UTC."""
    key = "swisstony"
    kinds = ("moneyline", "spreads", "totals")
    probe_usd = 5.0
    probes_before_anchor = 2
    max_probes = 10
    max_market_frac = 0.05
    kelly_frac = 0.25

    def anchor_dollars(self, v: View, st: dict, side: str) -> float:
        a = v.ask(side) or 1
        edge = v.fair(side) - a
        return max(0.0, v.bankroll * self.kelly_frac * edge / max(1 - a, 1e-6))

    def step(self, v: View, st: dict) -> list[Take | Rest]:
        acts = super().step(v, st)
        # Opportunistic hedge, only where it does not lock a loss (the report
        # shows the >$1.00 hedge leg was a net drag; that part is not copied).
        side = st.get("thesis")
        if side and not acts and self.leg(st, other(side))[0] == 0:
            n, vwap = self.leg(st, side)
            a = v.ask(other(side))
            if n > 0 and a is not None and vwap + a + fees.taker_fee(1, a, v.taker_theta) <= 1.0:
                acts.append(Take(other(side), n / 3 * a, a, "hedge"))
        return acts


class Sinenooneei(ProbeAnchor):
    """Report playbook: live directional tennis, MLB totals and tier-1 esports.
    Probe 5-30 small fills, then one anchor 5-20x the average probe once the
    match confirms the thesis; anchor never above $0.80, floor $0.10; never sell.
    Esports half size, pre-match half size, outside 08-22 UTC skip, never 16 UTC."""
    key = "sinenooneei"
    kinds = ("moneyline", "totals")
    probe_usd = 5.0
    probes_before_anchor = 3
    max_probes = 30
    anchor_price_cap = 0.80
    price_floor = 0.10
    max_market_frac = 0.10

    def size_scale(self, v: View) -> float:
        s = 0.5 if v.category in ESPORTS else 1.0
        return s * (1.0 if v.live else 0.5)

    def anchor_dollars(self, v: View, st: dict, side: str) -> float:
        edge = v.fair(side) - (v.ask(side) or 1)
        mult = 20 if edge >= 0.05 else 10 if edge >= 0.03 else 5
        # Confirmation: the match has moved toward the thesis since the first probe.
        if v.fair(side) < st.get("thesis_fair", 0):
            return 0.0
        return self.probe_usd * mult


class Kch123(ProbeAnchor):
    """Report: buy-only football and college basketball, concentrated bursts of
    many small fills per market (NFL spreads dominate), edge at $0.40-0.70,
    filtered to $0.20-0.70 and away from 16-18, 20, 22 UTC. No anchor fill."""
    key = "kch123"
    kinds = ("moneyline", "spreads", "totals")
    probe_usd = 5.0
    probes_before_anchor = 10**9
    max_probes = 20


# ---------------------------------------------------------------------------
# Live leader with early exits: GEIYECAPIXIE


class Geiyecapixie(Bot):
    """Report playbook: League of Legends game markets only, live, 09-14 UTC.
    Buy the team that takes a clear in-game lead at market, in clips scaled by
    conviction; probe continuously; sell into rallies of +15c over entry in
    tranches, close at $0.95-0.999 near the end, and flatten if the game
    reverses. The opposing 10-20% hedge is not modelled: on Polymarket US it
    would just net against the primary position at a spread cost.
    Stand-in for the game-state signal: the live international price."""
    key = "geiyecapixie"
    trades_pregame = False
    max_market_frac = 0.20
    # (fair value of leader, clip $) at a $1,000 bankroll
    tiers = ((0.90, 100.0), (0.80, 60.0), (0.65, 30.0), (0.55, 10.0))
    probe_usd = 3.0

    def step(self, v: View, st: dict) -> list[Take | Rest]:
        if not 9 <= v.hour < 15:
            return []
        held = v.held_side()
        if held:
            n, vwap = self.leg(st, held)
            bid = v.bid(held)
            peak = max(st.get("peak", 0.0), v.fair(held))
            st["peak"] = peak
            exit_side = other(held)
            limit = 1 - (bid or 0)
            if bid is not None and bid >= 0.95:
                return [Take(exit_side, abs(v.held) * (1 - bid), limit + 1e-9, "terminal_exit")]
            if peak - v.fair(held) >= 0.15:
                return [Take(exit_side, abs(v.held) * (1 - (bid or 0)), 1.0, "reversal_exit")]
            sold = (st.get("fills_by_tag") or {}).get("rally_exit", 0)
            if bid is not None and sold < 3 and bid >= vwap + 0.15 + 0.10 * sold:
                return [Take(exit_side, n / 4 * (1 - bid), limit + 1e-9, "rally_exit")]
        leader = "long" if v.fair_long >= 0.55 else "short" if v.fair_long <= 0.45 else None
        if leader is None or (held and held != leader):
            return []
        prev = v.prev_fair(leader)
        a = v.ask(leader)
        if a is None or a >= 0.95 or prev is None or not v.accepts(self.rules_key, leader, a):
            return []
        # A lead that is still building: fair rising and above the price.
        if v.fair(leader) < prev or v.fair(leader) - a < 0.01:
            return [Take(leader, min(self.probe_usd, self.room(v, st)), a, "probe")]
        clip = next(c for f, c in self.tiers if v.fair(leader) >= f)
        if "t1" in v.m.event_slug.split("-"):
            clip = min(clip, 20.0)   # T1 crowd premium: small clips only
        dollars = min(self.room(v, st), clip)
        if dollars < 1:
            return []
        return [Take(leader, dollars, v.fair(leader), "clip")]


# ---------------------------------------------------------------------------
# Fixed versions ("-v2"). Each keeps its original's report filters and changes
# only what the first day of paper trading showed going wrong. The originals
# keep running unchanged, so the two can be compared on the same games.


class FerrariV2(Ferrari):
    """Ferrari, fixed: one line per game and market type (the original bought
    over 232.5 and 234.5 and under 220.5-222.5 on one game, and lost on all
    five), and a 2.5x cap on the dominance clip, judged on the edge after the
    taker fee (the original's 5x clips came from a few cents of raw edge)."""
    key = "ferrarichampions2026-v2"
    rules = "ferrarichampions2026"
    one_line_per_game = True

    def dominant_mult(self, v: View, side: str) -> float:
        te = v.taker_edge(side)
        mult = {1.0: 1.0, 1.5: 1.5, 2.0: 2.5, 3.0: 2.5}[dominance_from_edge(max(te or 0.0, 0.0))]
        return mult

    def step(self, v: View, st: dict) -> list[Take | Rest]:
        if st.get("dominant") is None and v.siblings:
            return []
        return super().step(v, st)


class GamblingV2(Gambling):
    """Gambling, fixed: quotes only between 5c and 95c (most of the original's
    loss came from 1-3c and 97-99c quotes, where a cent of error is multiplied
    over hundreds of contracts), and the quote that closes a held position is
    capped so the pair costs at most $0.985 (the original closed pairs at an
    average $1.002, a small sure loss on each)."""
    key = "gamblingisallyourneed-v2"
    rules = "gamblingisallyourneed"
    price_floor = 0.05
    price_cap = 0.95
    max_pair = 0.985

    def step(self, v: View, st: dict) -> list[Take | Rest]:
        acts: list[Take | Rest] = []
        held = v.held_side()
        for side in SIDES:
            px = round(v.fair(side) - self.half_spread, 2)
            closing = held == other(side)
            if closing:
                _, entry = self.leg(st, held)
                px = min(px, round(self.max_pair - entry - 0.005, 2))
            best_ask = v.ask(side)
            if not self.price_floor <= px <= self.price_cap or (best_ask is not None and px >= best_ask):
                continue
            if not closing and not v.accepts(self.rules_key, side, px):
                continue
            edge = v.fair(side) - (best_ask or 1)
            mult = 2.0 if dominance_from_edge(max(edge, 0) + self.half_spread) >= 2.0 else 1.0
            dollars = v.bankroll * self.clip_frac * mult
            if closing:
                dollars = abs(v.held) * px
            elif self.room(v, st) < dollars:
                continue
            acts.append(Rest(side, px, dollars, "quote"))
        return acts


class SwissTonyV2(SwissTony):
    """SwissTony, fixed: needs 2c of edge after the fee instead of 1c (fees were
    $45, about half its loss) and risks at most 3% of bankroll per market
    instead of 5% (six $50 college basketball positions made most of the loss)."""
    key = "swisstony-v2"
    rules = "swisstony"
    min_edge = 0.02
    max_market_frac = 0.03


class Kch123V2(Kch123):
    """kch123, fixed: needs 2c of edge after the fee instead of 1c. It was the
    only profitable original, but fees ($22) were larger than its profit."""
    key = "kch123-v2"
    rules = "kch123"
    min_edge = 0.02


class SinenooneeiV2(Sinenooneei):
    """Sinenooneei, fixed: anchor at most 5x the probe ($25, the original went up
    to 20x = $100), the anchor needs the price to move at least 2c toward the
    thesis (the original anchored on no move at all), and 5% per market."""
    key = "sinenooneei-v2"
    rules = "sinenooneei"
    max_market_frac = 0.05
    confirm_move = 0.02

    def anchor_dollars(self, v: View, st: dict, side: str) -> float:
        if v.fair(side) < st.get("thesis_fair", 1.0) + self.confirm_move:
            return 0.0
        return self.probe_usd * 5


class RandomGameBetV2(RandomGameBet):
    """RandomGameBet, fixed: dominant clip at most $15 (its losses were $30-50
    dominant legs whose hedge never filled) and a stricter pre-check (expected
    paired cost $0.97 instead of $0.98), so it enters only when a hedge is likely."""
    key = "randomgamebet-v2"
    rules = "randomgamebet"
    precheck = 0.97
    clips = {1.0: (10, 10), 1.5: (15, 10), 2.0: (15, 8), 3.0: (15, 6)}


ORIGINALS = (Ferrari(), SwissTony(), Gambling(), Sinenooneei(), Kch123(), Geiyecapixie(),
             RandomGameBet())
FIXED = (FerrariV2(), SwissTonyV2(), GamblingV2(), SinenooneeiV2(), Kch123V2(), RandomGameBetV2())
BOTS: dict[str, Bot] = {b.key: b for b in ORIGINALS + FIXED}
