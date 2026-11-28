#!/usr/bin/env python3
"""pairbot: both-sides spread capture on live SPORTS markets, Polymarket US. PAPER ONLY.

There is no order code in this file. It reads the exchange's public market data
stream (order books and trades) and keeps paper orders in memory.

The idea (after the RandomGameBet report, adapted to Polymarket US):
  * On Polymarket US a market is one instrument. Buying the long side at p and the
    short side at q pays exactly $1 between them, so a pair costing p + q < $1 locks
    in 1 - p - q whoever wins.
  * Taking prices costs a fee on each side that is larger than the spread, so this
    bot only RESTS orders (no fee): a bid for the long side below the market and a
    bid for the short side (an offer, in long-side prices) above it.
  * When one side fills, the other order is moved so the pair locks at least
    TARGET of profit, and it waits there until it fills or the game ends.

How a paper resting order is judged filled (strict, from real trades only):
  * a real trade prints THROUGH our price            -> filled;
  * real trades print AT our price                   -> first they use up the size
    that was already resting there when we joined (we are last in line), and only
    what trades beyond that fills us.
  A price merely appearing in the book never fills anything.

Version 2 (2026-11-27), after studying a large in-play market-maker wallet:
  * Quotes are centred on a FAIR PRICE, not on the US book: the live international
    Polymarket price for the same outcome, streamed in real time. When that price
    moves, the quotes move at once, so a quote is less often left behind to be hit
    by someone who already knows the game has changed.
  * A cancelled paper order can still be filled for ORDER_DELAY_S afterwards, as a
    real cancel takes that long to reach the exchange.
  * A side that is held alone is closed at the market (paying the taker fee) once
    the fair price has moved STOP against it, instead of riding to the final result.

Simulation check copy (2026-11-28), same rules as pairbot-now plus:
  * PAIR_STRICT=1 (worst case): a trade AT our price never fills us, only a trade
    THROUGH it; run with PAIR_ORDER_DELAY=1.0 so orders and cancels take 1 s.
  * Every fill logs "markout" events: the fair price of the side we bought 5, 30 and
    60 s later. If it usually falls right after we are filled, the fills are the ones
    an informed trader would give us, and real results would be worse than paper.

Event driven: every book, trade or fair-price message is handled the moment it arrives.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# ----------------------------------------------------------------------------- settings
BANKROLL = float(os.environ.get("PAIR_BANKROLL", "500"))
CLIP_USD = float(os.environ.get("PAIR_CLIP", "5"))          # dollars per first leg
HALF_WIDTH = float(os.environ.get("PAIR_HALF_WIDTH", "0.015"))  # first legs rest this far either side of the mid
TARGET = float(os.environ.get("PAIR_TARGET", "0.025"))      # least profit per paired share (pair cost <= 1 - TARGET)
STOP = float(os.environ.get("PAIR_STOP", "0.08"))          # close a lone side when fair has moved this far against it (0 = never)
MAX_DISLOCATION = 0.05           # no new first legs while the US mid is this far from the fair price
INTL_MAX_SPREAD = 0.04           # the international price counts as fair only when its own spread is this tight
TAKER_THETA = 0.0695             # Polymarket US fee for taking a price through the API
MIN_PX, MAX_PX = 0.30, 0.80      # no new first legs outside this (2026-11-27: narrowed from 0.15-0.85; RN1 puts little money under 30c)
MAX_SPREAD = 0.06                # no new first legs on a book wider than this
MIN_LEVEL_QTY = 2.0              # ignore resting orders smaller than this many contracts
REQUOTE_HOLD_S = 0.25            # least time between two requotes of one market
MAX_OPEN_LEGS = int(os.environ.get("PAIR_MAX_OPEN", "40"))  # unpaired positions held at once
MAX_OPEN_USD = BANKROLL * 0.5    # dollars in unpaired positions at once
STRICT = os.environ.get("PAIR_STRICT", "1") == "1"    # only trades through our price fill us
MARK_HORIZONS = (5, 30, 60)      # seconds after a fill at which the fair price is logged
ORDER_DELAY_S = float(os.environ.get("PAIR_ORDER_DELAY", "0.3"))   # a real order takes about this long to reach the book
PREGAME_S = 300                  # start quoting this long before the listed start
ESPORTS = {"cs2", "dota2", "valorant", "lol", "r6"}
# 2026-11-27: also trade the main spread and the main total of each game, like RN1 (was game winners only).
WITH_LINES = os.environ.get("PAIR_LINES", "1") == "1"
KINDS = {"moneyline", "spreads", "totals"} if WITH_LINES else {"moneyline"}
# No new first legs once a game is this many minutes past its listed start (added
# 2026-11-27). The worst losses were sides bought seconds before a game was decided.
# These are clock minutes, so they include breaks; each stops well before the usual end.
LATE_MIN = {"cbb": 85, "wcbb": 85, "nba": 105, "nfl": 150, "cfb": 165, "mlb": 140,
            "nhl": 110, "ahl": 110, "khl": 110, "shl": 110, "snhl": 110,
            "atp": 60, "wta": 60, "itfme": 60, "itfwo": 60, "pdcdarts": 20,
            "ufc": 5, "boxing": 5, "bkfc": 5, "modus": 20,
            "eurolg": 85, "eurocup": 85, "vtb": 85, "jpbl": 85, "lkl": 85}
LATE_DEFAULT = 80      # soccer and anything not listed: about the 65th minute of a match
if os.environ.get("PAIR_LATE_OFF") == "1":
    LATE_MIN, LATE_DEFAULT = {}, 10 ** 6
EPS = 1e-9


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def floor_tick(x: float, tick: float) -> float:
    return round(math.floor(x / tick + EPS) * tick, 6)


def ceil_tick(x: float, tick: float) -> float:
    return round(math.ceil(x / tick - EPS) * tick, 6)


# ----------------------------------------------------------------------------- paper orders
@dataclass
class Order:
    """A paper resting order. `px` is always a LONG-side price:
    side 'long'  = bid to buy the long side at px;
    side 'short' = bid to buy the short side at 1 - px, which rests as an offer at px."""
    side: str
    px: float
    shares: int
    queue_ahead: float          # contracts already resting at px when we joined
    placed: float               # monotonic time
    leg: str                    # 'first' or 'hedge'
    filled: int = 0
    live_from: float = 0.0      # wall-clock time from which real trades can fill it
    dead_at: float = 0.0        # set when cancelled: real trades stamped before this can still fill it

    @property
    def left(self) -> int:
        return self.shares - self.filled

    def cost_px(self) -> float:
        """What one share costs us on our own side."""
        return self.px if self.side == "long" else round(1 - self.px, 6)


@dataclass
class Market:
    slug: str
    league: str
    start: datetime
    tick: float = 0.01
    name: str = ""
    bid: float | None = None
    ask: float | None = None
    bids: dict = field(default_factory=dict)     # price -> size
    asks: dict = field(default_factory=dict)
    state: str | None = None
    orders: dict = field(default_factory=dict)   # side -> Order
    held_side: str | None = None                 # the unpaired leg, if any
    held_shares: int = 0
    held_cost: float = 0.0                       # dollars paid for the unpaired leg
    held_at: str | None = None
    last_quote: float = 0.0
    pairs: int = 0
    dying: list = field(default_factory=list)   # cancelled orders whose cancel is still on its way
    token: str | None = None                     # international token for the long outcome (or its complement)
    complement: bool = False
    fair: float | None = None                    # fair price of the LONG side, from the international book

    @property
    def mid(self) -> float | None:
        return None if self.bid is None or self.ask is None else (self.bid + self.ask) / 2

    def late(self) -> bool:
        """Too far into the game to open a new side."""
        return (now_utc() - self.start).total_seconds() / 60 > LATE_MIN.get(self.league, LATE_DEFAULT)


class Book:
    """All paper state, and the rules. No network in here, so it can be tested."""

    def __init__(self, log, record):
        self.markets: dict[str, Market] = {}
        self.cash = BANKROLL                      # bankroll + realized results
        self.log, self.record = log, record
        self.marks: list[dict] = []               # fills waiting for their markout times
        self.stats = {"book_msgs": 0, "trade_msgs": 0, "fair_msgs": 0, "first_fills": 0, "pairs": 0,
                      "quotes": 0, "requotes": 0, "settled_unpaired": 0, "stopped": 0,
                      "late_fills": 0, "too_early": 0}

    # -- exposure
    def open_legs(self) -> int:
        return sum(1 for m in self.markets.values() if m.held_shares)

    def open_usd(self) -> float:
        return sum(m.held_cost for m in self.markets.values())

    # -- market data in
    def on_book(self, m: Market, md: dict, mono: float, live: bool) -> None:
        self.stats["book_msgs"] += 1
        def side(raw):
            out = {}
            for x in raw or []:
                try:
                    px, qty = float(x["px"]["value"]), float(x["qty"])
                except (KeyError, TypeError, ValueError):
                    continue
                if qty >= MIN_LEVEL_QTY and 0 < px < 1:
                    out[round(px, 6)] = qty
            return out
        m.bids, m.asks, m.state = side(md.get("bids")), side(md.get("offers")), md.get("state")
        m.bid = max(m.bids) if m.bids else None
        m.ask = min(m.asks) if m.asks else None
        self.quote(m, mono, live)

    def on_trade(self, m: Market, px: float, qty: float, taker_buys: bool, mono: float,
                 traded_at: float | None = None) -> None:
        """A real trade at long-side price px. A taker who SELLS can fill our long bid;
        a taker who BUYS can fill our short bid (our offer)."""
        self.stats["trade_msgs"] += 1
        self._trade_qty = qty
        side = "short" if taker_buys else "long"
        # An order whose cancel had not yet arrived when the trade happened is still hit.
        for d in [d for d in m.dying if d.side == side]:
            if traded_at is not None and d.live_from <= traded_at < d.dead_at and d.left > 0 \
                    and not m.held_shares:
                got = self._match(d, px, qty, taker_buys)
                if got > 0:
                    self.stats["late_fills"] += 1
                    self.fill(m, d, got, mono)
                    return
        o = m.orders.get(side)
        if o is None or o.left <= 0:
            return
        if traded_at is not None and traded_at < o.live_from:
            self.stats["too_early"] += 1
            return   # this trade happened before our order could have been in the book
        got = self._match(o, px, qty, taker_buys)
        if got > 0:
            self.fill(m, o, got, mono)

    @staticmethod
    def _match(o: Order, px: float, qty: float, taker_buys: bool) -> int:
        through = px > o.px + EPS if taker_buys else px < o.px - EPS
        if through:
            return min(o.left, int(math.floor(qty + EPS)))   # sized copy: only what traded can fill us
        if STRICT:
            return 0
        if abs(px - o.px) <= EPS:
            use = min(o.queue_ahead, qty)       # those ahead of us are served first
            o.queue_ahead -= use
            return min(o.left, int(math.floor(qty - use + EPS)))
        return 0

    def cancel(self, m: Market, keep: tuple = ()) -> None:
        """Cancel the market's resting orders; each stays hittable until the cancel lands."""
        dead = time.time() + ORDER_DELAY_S
        for side, o in list(m.orders.items()):
            if o in keep:
                continue
            if o.leg == "first" and o.left > 0:
                o.dead_at = dead
                m.dying.append(o)
            del m.orders[side]
        if len(m.dying) > 6:
            now = time.time()
            m.dying = [d for d in m.dying if d.dead_at > now - 5]

    def on_fair(self, m: Market, fair: float | None, mono: float, live: bool) -> None:
        """The international price moved (or went away)."""
        self.stats["fair_msgs"] += 1
        m.fair = fair
        if m.held_shares:
            self.maybe_stop(m, mono)
        self.quote(m, mono, live, urgent=True)

    def maybe_stop(self, m: Market, mono: float) -> None:
        """Close a lone side at the market once fair has moved STOP against it."""
        if STOP <= 0 or m.fair is None or not m.held_shares or m.bid is None or m.ask is None:
            return
        avg = m.held_cost / m.held_shares
        fair_side = m.fair if m.held_side == "long" else 1 - m.fair
        if avg - fair_side < STOP - EPS:
            return
        # Closing = buying the other side at its ask; one tick worse, for the order's trip.
        other_px = (1 - m.bid if m.held_side == "long" else m.ask) + m.tick
        other_px = min(other_px, 0.99)
        n = m.held_shares
        fee = round(TAKER_THETA * n * other_px * (1 - other_px), 2)
        pnl = n * (1 - avg - other_px) - fee
        self.cash += pnl
        self.stats["stopped"] += 1
        self.record({"t": iso(now_utc()), "ev": "stopped", "slug": m.slug, "league": m.league,
                     "side": m.held_side, "px": round(avg, 4), "exit_px": round(1 - other_px, 4),
                     "fair": round(fair_side, 4), "shares": n, "fee": fee, "profit": round(pnl, 4),
                     "opened": m.held_at})
        self.log(f"STOP {m.slug} {m.held_side} {n} bought {avg:.3f}, fair now {fair_side:.3f} -> {pnl:+.2f}")
        m.held_side, m.held_shares, m.held_cost, m.held_at = None, 0, 0.0, None
        m.orders.clear()
        m.dying.clear()

    # -- fills
    def fill(self, m: Market, o: Order, n: int, mono: float) -> None:
        o.filled += n
        cpx = o.cost_px()
        if m.fair is not None:
            f0 = m.fair if o.side == "long" else 1 - m.fair
            self.marks.append({"slug": m.slug, "league": m.league, "side": o.side, "leg": o.leg,
                               "px": cpx, "shares": n, "fair0": round(f0, 4), "t0": time.monotonic(),
                               "done": set()})
        t = iso(now_utc())
        if o.leg == "first":
            self.stats["first_fills"] += 1
            m.held_side, m.held_shares = o.side, m.held_shares + n
            m.held_cost += n * cpx
            m.held_at = m.held_at or t
            self.record({"t": t, "ev": "first", "slug": m.slug, "league": m.league, "side": o.side,
                         "px": cpx, "shares": n, "order_shares": o.shares,
                         "trade_qty": getattr(self, "_trade_qty", None),
                         "min_in": round((now_utc() - m.start).total_seconds() / 60, 1)})
            self.log(f"FIRST LEG {m.slug} {o.side} {n} at {cpx:.3f}")
            m.orders.clear()                    # the other first-leg quote is cancelled
            m.dying.clear()
            self.place_hedge(m, mono)
            return
        # hedge fill: n shares of the held leg are now paired
        avg = m.held_cost / m.held_shares
        profit = n * (1 - avg - cpx)
        self.cash += profit
        m.held_shares -= n
        m.held_cost -= n * avg
        self.stats["pairs"] += 1
        m.pairs += 1
        self.record({"t": t, "ev": "pair", "slug": m.slug, "league": m.league, "first_side": m.held_side,
                     "first_px": round(avg, 4), "hedge_px": cpx, "pair_cost": round(avg + cpx, 4),
                     "shares": n, "profit": round(profit, 4), "opened": m.held_at,
                     "order_shares": o.shares, "trade_qty": getattr(self, "_trade_qty", None)})
        self.log(f"PAIR {m.slug} {n} shares, cost {avg + cpx:.3f}, profit {profit:+.2f}")
        if m.held_shares <= 0:
            m.held_side, m.held_shares, m.held_cost, m.held_at = None, 0, 0.0, None
            m.orders.clear()

    def check_marks(self) -> None:
        """Log the fair price of each filled side 5, 30 and 60 s after the fill."""
        if not self.marks:
            return
        now = time.monotonic()
        keep = []
        for k in self.marks:
            m = self.markets.get(k["slug"])
            for h in MARK_HORIZONS:
                if h in k["done"] or now - k["t0"] < h:
                    continue
                k["done"].add(h)
                if m is None or m.fair is None:
                    continue
                f = m.fair if k["side"] == "long" else 1 - m.fair
                self.record({"t": iso(now_utc()), "ev": "markout", "h": h, "slug": k["slug"],
                             "league": k["league"], "leg": k["leg"], "side": k["side"], "px": k["px"],
                             "shares": k["shares"], "fair0": k["fair0"], "fair": round(f, 4),
                             "move": round(f - k["fair0"], 4)})
            if len(k["done"]) < len(MARK_HORIZONS):
                keep.append(k)
        self.marks = keep

    # -- quoting
    def place(self, m: Market, side: str, px: float, shares: int, leg: str, mono: float) -> None:
        book = m.bids if side == "long" else m.asks
        m.orders[side] = Order(side, px, shares, float(book.get(px, 0.0)), mono, leg,
                               live_from=time.time() + ORDER_DELAY_S)

    def place_hedge(self, m: Market, mono: float) -> None:
        """Rest the other side where the pair locks at least TARGET."""
        if not m.held_shares:
            return
        avg = m.held_cost / m.held_shares
        if m.held_side == "long":   # need to buy short: an offer at a long price >= avg + TARGET
            px = ceil_tick(avg + TARGET, m.tick)
            if m.bid is not None and px <= m.bid:
                px = round(m.bid + m.tick, 6)     # stay passive, never cross
            if px < 1:
                self.place(m, "short", px, m.held_shares, "hedge", mono)
        else:                       # holding short at avg: buy long at <= 1 - avg - TARGET
            px = floor_tick(1 - avg - TARGET, m.tick)
            if m.ask is not None and px >= m.ask:
                px = round(m.ask - m.tick, 6)
            if px > 0:
                self.place(m, "long", px, m.held_shares, "hedge", mono)

    def quote(self, m: Market, mono: float, live: bool, urgent: bool = False) -> None:
        if m.held_shares:           # a leg is waiting for its hedge: keep that order as it is
            if not m.orders:
                self.place_hedge(m, mono)
            return
        ok = (live and m.fair is not None and not m.late() and m.state in (None, "MARKET_STATE_OPEN")
              and m.bid is not None and m.ask is not None
              and m.ask - m.bid <= MAX_SPREAD + EPS and MIN_PX <= m.bid and m.ask <= MAX_PX
              and abs(m.mid - m.fair) <= MAX_DISLOCATION + EPS
              and self.open_legs() < MAX_OPEN_LEGS and self.open_usd() + CLIP_USD <= MAX_OPEN_USD
              and self.cash - self.open_usd() >= CLIP_USD)
        if not ok:
            if m.orders:
                self.cancel(m)
            return
        want_bid = floor_tick(m.fair - HALF_WIDTH, m.tick)
        want_ask = ceil_tick(m.fair + HALF_WIDTH, m.tick)
        if want_bid >= m.ask:        # stay passive: never cross the US book
            want_bid = round(m.ask - m.tick, 6)
        if want_ask <= m.bid:
            want_ask = round(m.bid + m.tick, 6)
        if want_bid <= 0 or want_ask >= 1 or 1 - (want_ask - want_bid) > 1 - TARGET + EPS:
            if m.orders:
                self.cancel(m)
            return
        cur_b, cur_a = m.orders.get("long"), m.orders.get("short")
        keep_b = cur_b is not None and abs(cur_b.px - want_bid) <= EPS
        keep_a = cur_a is not None and abs(cur_a.px - want_ask) <= EPS
        if keep_b and keep_a:
            return
        if (cur_b or cur_a) and mono - m.last_quote < REQUOTE_HOLD_S and not urgent:
            return                   # do not chase every flicker of the US book
        self.stats["requotes" if (cur_b or cur_a) else "quotes"] += 1
        m.last_quote = mono
        self.cancel(m, keep=tuple(o for o, k in ((cur_b, keep_b), (cur_a, keep_a)) if k))
        n_long = int(CLIP_USD // want_bid)
        n_short = int(CLIP_USD // (1 - want_ask))
        if not keep_b and n_long >= 1:
            self.place(m, "long", want_bid, n_long, "first", mono)
        if not keep_a and n_short >= 1:
            self.place(m, "short", want_ask, n_short, "first", mono)

    # -- end of game
    def settle(self, m: Market, long_value: float) -> None:
        if m.held_shares:
            v = long_value if m.held_side == "long" else 1 - long_value
            pnl = m.held_shares * v - m.held_cost
            self.cash += pnl
            self.stats["settled_unpaired"] += 1
            self.record({"t": iso(now_utc()), "ev": "unpaired_settled", "slug": m.slug, "league": m.league,
                         "side": m.held_side, "px": round(m.held_cost / m.held_shares, 4),
                         "shares": m.held_shares, "value": v, "profit": round(pnl, 4), "opened": m.held_at})
            self.log(f"UNPAIRED SETTLED {m.slug} {m.held_side} {m.held_shares} -> {pnl:+.2f}")
            m.held_side, m.held_shares, m.held_cost, m.held_at = None, 0, 0.0, None
        m.orders.clear()
        m.dying.clear()


# ----------------------------------------------------------------------------- the live runner
HOME = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HOME, "state.json")
EVENTS = os.path.join(HOME, "events.jsonl")
LOG = os.path.join(HOME, "pairbot.log")
PSB = os.environ.get("PAIR_PSB", "/root/sportbot/psb-live50")   # reused: market list + request signing
KEYS = os.environ.get("PAIR_KEYS", os.path.expanduser("~/.bot7live/keys.json"))
HOST, PATH = "wss://api.polymarket.us", "/v1/ws/markets"
INTL_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"   # public, no sign-in


def log(msg: str) -> None:
    line = f"{iso(now_utc())} {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def record(ev: dict) -> None:
    with open(EVENTS, "a") as f:
        f.write(json.dumps(ev) + "\n")


class Runner:
    def __init__(self):
        sys.path.insert(0, PSB)
        from psb import paper, reference, usmarket
        from psb.live import Signer
        self.usmarket, self.reference = usmarket, reference
        self.events: dict[str, dict | None] = {}     # US event slug -> international event
        self.intl_ok = False
        self.intl_top: dict[str, tuple] = {}         # token -> (best bid, best ask)
        self.intl_reconnects = 0
        self.leagues = [lg for lg in paper.DEFAULT_LEAGUES + paper.EXTRA_LEAGUES if lg not in ESPORTS]
        k = json.loads(open(KEYS).read())
        self.signer = Signer(k["key_id"], k["secret_key"])   # signs the stream's opening GET only
        del k
        self.book = Book(log, record)
        self.lock = threading.RLock()
        self.found: dict[str, Market] = {}
        self.handler_max = 0.0
        self.handler_sum, self.handler_n = 0.0, 0
        self.reconnects = 0
        self.load()

    # -- state on disk
    def load(self) -> None:
        if not os.path.exists(STATE):
            return
        d = json.loads(open(STATE).read())
        self.book.cash = d["cash"]
        self.book.stats.update(d.get("stats", {}))
        for s, x in d.get("held", {}).items():
            m = Market(s, x["league"], datetime.fromisoformat(x["start"]), x.get("tick", 0.01), x.get("name", ""))
            m.held_side, m.held_shares, m.held_cost, m.held_at = x["side"], x["shares"], x["cost"], x.get("at")
            m.token, m.complement = x.get("token"), bool(x.get("complement"))
            self.book.markets[s] = m
        log(f"resumed: cash {self.book.cash:.2f}, {len(self.book.markets)} unpaired legs")

    def save(self) -> None:
        held = {s: {"league": m.league, "start": m.start.isoformat(), "tick": m.tick, "name": m.name,
                    "side": m.held_side, "shares": m.held_shares, "cost": m.held_cost, "at": m.held_at,
                    "token": m.token, "complement": m.complement}
                for s, m in self.book.markets.items() if m.held_shares}
        tmp = STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"cash": self.book.cash, "bankroll": BANKROLL, "stats": self.book.stats, "held": held,
                       "saved": iso(now_utc())}, f)
        os.replace(tmp, STATE)

    # -- background: which games are on, and results
    def discover_forever(self) -> None:
        i = 0
        while True:
            lg = self.leagues[i % len(self.leagues)]
            i += 1
            try:
                now = now_utc()
                ms = self.usmarket.discover(lg, WITH_LINES, starts=(now - timedelta(hours=8), now + timedelta(hours=2)))
                fresh = {}
                for um in ms:
                    if um.kind not in KINDS:
                        continue
                    old = self.found.get(um.slug)
                    if old is not None and old.token:
                        fresh[um.slug] = old           # already matched
                        continue
                    if um.event_slug not in self.events:
                        self.events[um.event_slug] = self.reference.find_event(um)
                    ev = self.events[um.event_slug]
                    ln = self.reference.link(um, ev) if ev else None
                    mk = Market(um.slug, lg, um.start, um.tick or 0.01, um.long_name or "")
                    if ln is not None and ln.token_id:
                        mk.token, mk.complement = ln.token_id, bool(ln.complement)
                        fresh[um.slug] = mk            # only games with a fair price are traded
                with self.lock:
                    for s in [s for s, m in self.found.items() if m.league == lg]:
                        del self.found[s]
                    self.found.update(fresh)
            except Exception as e:
                log(f"discover {lg}: {type(e).__name__}: {str(e)[:120]}")
            time.sleep(0.2 if i <= len(self.leagues) else 300 / len(self.leagues))

    def settle_forever(self) -> None:
        while True:
            time.sleep(120)
            try:
                with self.lock:
                    slugs = [s for s, m in self.book.markets.items() if m.held_shares]
                for s in slugs:
                    v = self.usmarket.settlement(s)
                    if v is not None:
                        with self.lock:
                            self.book.settle(self.book.markets[s], v)
            except Exception as e:
                log(f"settle: {type(e).__name__}: {str(e)[:120]}")

    def wanted(self) -> dict[str, Market]:
        """Markets to stream now: games on or about to start, plus any unpaired leg."""
        now = now_utc()
        with self.lock:
            out = {s: m for s, m in self.found.items() if m.start <= now + timedelta(seconds=PREGAME_S)}
        for s, m in self.book.markets.items():
            if m.held_shares:
                out.setdefault(s, m)
        return out

    # -- stream
    async def run(self) -> None:
        await asyncio.gather(self.run_us(), self.run_intl())

    # -- international fair price
    def fair_of(self, m: Market) -> float | None:
        top = self.intl_top.get(m.token) if (m.token and self.intl_ok) else None
        if not top or top[0] is None or top[1] is None or top[1] - top[0] > INTL_MAX_SPREAD + EPS:
            return None
        mid = (top[0] + top[1]) / 2
        return round(1 - mid if m.complement else mid, 6)

    def push_fair(self, token: str, mono: float) -> None:
        now = now_utc()
        for m in self.by_token.get(token, ()):
            f = self.fair_of(m)
            if f is None and m.fair is None:
                continue
            if f is not None and m.fair is not None and abs(f - m.fair) < 0.0025:
                continue          # under a quarter of a cent: nothing to do
            self.book.on_fair(m, f, mono, m.start <= now + timedelta(seconds=PREGAME_S))

    async def run_intl(self) -> None:
        import websockets
        self.by_token: dict[str, list] = {}
        while True:
            try:
                with self.lock:
                    self.by_token = {}
                    for m in self.book.markets.values():
                        if m.token:
                            self.by_token.setdefault(m.token, []).append(m)
                toks = sorted(self.by_token)
                if not toks:
                    await asyncio.sleep(2)
                    continue
                async with websockets.connect(INTL_WS, ping_interval=None, max_size=None) as ws:
                    await ws.send(json.dumps({"assets_ids": toks, "type": "market"}))
                    self.intl_ok = True
                    last_ping = next_check = time.monotonic()
                    last_msg = time.monotonic()
                    while True:
                        mono = time.monotonic()
                        if mono - last_ping > 9:
                            await ws.send("PING")
                            last_ping = mono
                        if mono - next_check > 15:
                            next_check = mono
                            with self.lock:
                                now_toks = {m.token for m in self.book.markets.values() if m.token}
                            if now_toks - set(toks):
                                break          # new games: reconnect with the full list
                        if mono - last_msg > 60:
                            raise RuntimeError("no international message for 60 s")
                        try:
                            raw = await asyncio.wait_for(ws.recv(), 1.0)
                        except asyncio.TimeoutError:
                            continue
                        last_msg = time.monotonic()
                        if raw == "PONG":
                            continue
                        d = json.loads(raw)
                        with self.lock:
                            for x in (d if isinstance(d, list) else [d]):
                                self.intl_event(x, last_msg)
            except Exception as e:
                log(f"international stream: {type(e).__name__}: {str(e)[:140]}; reconnecting")
            self.intl_ok = False
            self.intl_reconnects += 1
            with self.lock:   # no fair price: pull every first-leg quote
                for m in self.book.markets.values():
                    if m.fair is not None:
                        self.book.on_fair(m, None, time.monotonic(), True)
            await asyncio.sleep(0.5)

    def intl_event(self, x: dict, mono: float) -> None:
        def num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        kind = x.get("event_type")
        if kind == "price_change":
            for c in x.get("price_changes") or []:
                tok = c.get("asset_id")
                if tok in self.by_token:
                    top = (num(c.get("best_bid")), num(c.get("best_ask")))
                    if top != self.intl_top.get(tok):
                        self.intl_top[tok] = top
                        self.push_fair(tok, mono)
        elif kind == "book":
            tok = x.get("asset_id")
            if tok in self.by_token:
                bids = [num(b.get("price")) for b in x.get("bids") or []]
                asks = [num(a.get("price")) for a in x.get("asks") or []]
                self.intl_top[tok] = (max(bids) if bids else None, min(asks) if asks else None)
                self.push_fair(tok, mono)

    async def run_us(self) -> None:
        delay = 1.0
        while True:
            t0 = time.monotonic()
            try:
                await self.session()
            except Exception as e:
                log(f"stream: {type(e).__name__}: {str(e)[:160]}; reconnecting")
            for m in self.book.markets.values():   # paper orders do not survive a gap in the data
                if not m.held_shares:
                    m.orders.clear()
                    m.dying.clear()
            self.reconnects += 1
            delay = 0.3 if time.monotonic() - t0 > 30 else min(30.0, delay * 2)
            await asyncio.sleep(delay)

    async def session(self) -> None:
        import websockets
        async with websockets.connect(HOST + PATH, extra_headers=self.signer.headers("GET", PATH),
                                      max_size=None, ping_interval=10, ping_timeout=10) as ws:
            want = self.wanted()
            slugs = sorted(want, key=lambda s: want[s].start)[:500]   # 5 book + 5 trade subscriptions
            with self.lock:
                for s in slugs:
                    if s not in self.book.markets:
                        self.book.markets[s] = want[s]
            for kind, typ in (("b", "SUBSCRIPTION_TYPE_MARKET_DATA"), ("t", "SUBSCRIPTION_TYPE_TRADE")):
                for i in range(0, len(slugs), 100):
                    await ws.send(json.dumps({"subscribe": {"requestId": f"{kind}{i}", "subscriptionType": typ,
                                                            "marketSlugs": slugs[i:i + 100]}}))
            sub = set(slugs)
            log(f"stream connected: {len(slugs)} markets (books and trades)")
            next_check = time.monotonic() + 20
            next_save = time.monotonic() + 60
            last_msg = time.monotonic()
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), 0.5)
                except asyncio.TimeoutError:
                    raw = None
                mono = time.monotonic()
                if raw is not None:
                    last_msg = mono
                    with self.lock:
                        self.handle(raw, mono)
                    d = time.monotonic() - mono
                    self.handler_sum += d
                    self.handler_n += 1
                    self.handler_max = max(self.handler_max, d)
                elif mono - last_msg > 90 and sub:
                    raise RuntimeError("no message for 90 s")
                if mono >= next_save:
                    next_save = mono + 60
                    self.save()
                    self.status()
                if mono >= next_check:
                    next_check = mono + 20
                    now_want = set(self.wanted())
                    with self.lock:
                        for s in list(self.book.markets):   # forget finished games we hold nothing in
                            if s not in now_want and not self.book.markets[s].held_shares:
                                del self.book.markets[s]
                    if now_want - sub:
                        log(f"{len(now_want - sub)} new games: reconnecting to add them")
                        return

    def handle(self, raw: str, mono: float) -> None:
        self.book.check_marks()
        d = json.loads(raw)
        md = d.get("marketData")
        if md is not None:
            m = self.book.markets.get(md.get("marketSlug"))
            if m is not None:
                live = m.start <= now_utc() + timedelta(seconds=PREGAME_S)
                self.book.on_book(m, md, mono, live)
            return
        t = d.get("trade")
        if t is not None:
            m = self.book.markets.get(t.get("marketSlug"))
            if m is None:
                return
            try:
                px, qty = float(t["price"]["value"]), float(t["quantity"]["value"])
            except (KeyError, TypeError, ValueError):
                return
            side = (t.get("taker") or {}).get("side")
            try:   # nanosecond timestamps: keep microseconds
                tt = t["tradeTime"]
                at = datetime.fromisoformat(tt[:26].rstrip("Z") + "+00:00").timestamp()
            except (KeyError, ValueError):
                at = None
            if side in ("ORDER_SIDE_BUY", "ORDER_SIDE_SELL"):
                self.book.on_trade(m, px, qty, side == "ORDER_SIDE_BUY", mono, at)

    def status(self) -> None:
        b = self.book
        quoting = sum(1 for m in b.markets.values() if m.orders and not m.held_shares)
        avg = 1e6 * self.handler_sum / max(1, self.handler_n)
        fair = sum(1 for m in b.markets.values() if m.fair is not None)
        late = sum(1 for m in b.markets.values() if m.late())
        log(f"status: cash {b.cash:.2f} | markets {len(b.markets)} ({fair} with a fair price, international "
            f"stream {'on' if self.intl_ok else 'OFF'}, {self.intl_reconnects} reconnects; {late} too late in the game), quoting {quoting}, unpaired {b.open_legs()} "
            f"(${b.open_usd():.2f}) | {json.dumps(b.stats)} | reaction: avg {avg:.0f} microseconds per message, "
            f"slowest {1000 * self.handler_max:.1f} ms | reconnects {self.reconnects}")
        self.handler_max, self.handler_sum, self.handler_n = 0.0, 0.0, 0


def main() -> None:
    log(f"pairbot-sized (fills capped at traded size; strict fills {'ON' if STRICT else 'OFF'}, order delay {ORDER_DELAY_S:.1f}s, markouts on) "
        f"PAPER ONLY (no order code): bankroll ${BANKROLL:.0f}, ${CLIP_USD:.0f} per leg, first legs "
        f"{HALF_WIDTH * 100:.1f}c either side of the international fair price, pairs lock {TARGET * 100:.1f}c+, "
        f"lone side closed at {STOP * 100:.0f}c against (0 = never), no new sides late in a game, sports only")
    r = Runner()
    threading.Thread(target=r.discover_forever, daemon=True).start()
    threading.Thread(target=r.settle_forever, daemon=True).start()
    time.sleep(20)   # let the first games be found
    asyncio.run(r.run())


if __name__ == "__main__":
    main()
