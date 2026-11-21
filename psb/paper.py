"""Paper trading of the seven report bots on live Polymarket US books.

No order is ever sent: the only network calls are public GETs to Polymarket US
and public reads of international Polymarket.

Loop:
  * every `--discover` seconds, list open full-game markets (winners, main
    spread, main total) in the chosen leagues and link each to the same
    international outcome;
  * every `--fast` seconds, poll markets whose game has started (live bots);
    every `--slow` seconds, poll markets starting within `--horizon` hours;
  * per poll: read fresh international midpoints in one batch, read the US
    book, settle resting paper orders against real US trades, let each bot act.

Fill model:
  Take  walks the US book up to the bot's limit price, pays the US taker fee.
  Rest  a paper bid that fills only when real US trades print at or through its
        price after it was placed (capped at the traded volume), or when the book
        crosses it. Queue position is ignored, so this is optimistic about which
        trades we would have received. Every bot is simulated independently.

Positions use one signed contract count per market, as on Polymarket US:
buying the other side reduces or flips the held side and cashes out the difference.
"""
from __future__ import annotations

import argparse
import json
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collections import deque

from . import fees, learn, livesim, reference, usmarket
from .bots import Fill, Rest, Take, View
from .registry import BOTS
from .markets import category_of

STATE = Path(__file__).resolve().parent.parent / "paper"
# Polymarket US league slugs (GET /v2/sports). Tennis is "atp"/"wta" plus ITF
# men's and women's ("itfme"/"itfwo", one "itf" league internationally). UTR
# events have no international market to price them, so they are left out.
DEFAULT_LEAGUES = [
    # US majors and college
    "nba", "nfl", "cfb", "nhl", "cbb", "mlb", "ufc",
    # soccer
    "epl", "lal", "bun", "sea", "lg1", "ere", "ucl", "uel", "uecl", "uwcl", "eflch", "cdr",
    "mls", "lmx", "bra",
    # basketball and hockey outside the US (checked 2026-11-18: these link and price)
    "eurolg", "eurocup", "vtb", "jpbl", "lkl", "khl", "ahl", "shl", "snhl",
    # tennis, darts, esports
    "atp", "wta", "itfme", "itfwo", "pdcdarts", "cs2", "dota2", "valorant", "lol",
]
# Leagues only the bots that name them (Bot.extra_leagues) may trade, so adding
# them does not change what the other bots trade.
EXTRA_LEAGUES = ["boxing", "bkfc", "modus"]


def allowed(bot, m) -> bool:
    """Whether `bot` may trade market `m` at all (an extra league needs opting in)."""
    return m.league not in EXTRA_LEAGUES or m.league in getattr(bot, "extra_leagues", ())
LIVE_WINDOW = timedelta(hours=8)
POLL_GROUP = 8            # markets per group: one international read, then their US books
PREGAME_PER_POLL = 24     # upcoming markets per poll, so live games never wait long
STATS_CACHE_S = 30.0      # seconds a bot's own results (bot_stats) are reused
LEARN_EVERY_S = 600       # how often learn.learn() re-reads settled history
SERIES_S = 75 * 60        # seconds of per-market samples kept in memory
MAX_FAIR_AGE = 3.0        # seconds: an older international price is not acted on
_BOOK_POOL = ThreadPoolExecutor(max_workers=4)   # usmarket's throttle still caps the rate


def _read_book(slug: str):
    try:
        return usmarket.book(slug)
    except Exception as e:   # returned, so one bad book does not lose the group
        return e


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Position:
    contracts: float = 0.0   # >0 long, <0 short
    cash: float = 0.0        # cash flow so far (negative = spent)
    fees: float = 0.0


@dataclass
class Order:
    bot: str
    slug: str
    side: str
    price: float
    contracts: float
    tag: str
    placed_at: str
    traded_at_place: float | None   # US sharesTraded when placed


def worst_case(cash: float, contracts: float) -> float:
    """Value of a position if it settles against us (long at 0, short at 1)."""
    return cash + min(0.0, contracts)


def cash_needed(pos: Position, side: str, price: float, contracts: float, fee: float) -> float:
    """Extra cash an order locks; negative when it cashes out an opposite position."""
    signed = contracts if side == "long" else -contracts
    long_px = price if side == "long" else 1 - price
    after = worst_case(pos.cash - signed * long_px - fee, pos.contracts + signed)
    return worst_case(pos.cash, pos.contracts) - after


@dataclass
class Ledger:
    bankroll: float = 1000.0
    positions: dict[str, dict[str, Position]] = field(default_factory=dict)
    states: dict[str, dict[str, dict]] = field(default_factory=dict)
    orders: list[Order] = field(default_factory=list)
    fills: list[dict] = field(default_factory=list)
    settled: dict[str, float] = field(default_factory=dict)
    marks: dict[str, float] = field(default_factory=dict)   # last US mid per slug
    started_at: str = ""
    bot_started: dict[str, str] = field(default_factory=dict)   # when each bot joined
    settled_at: dict[str, str] = field(default_factory=dict)    # when we saw each settlement

    @classmethod
    def load(cls, path: Path, bankroll: float) -> "Ledger":
        if not path.exists():
            return cls(bankroll=bankroll, started_at=_iso(_now()))
        d = json.loads(path.read_text())
        return cls(
            bankroll=d["bankroll"],
            positions={b: {m: Position(**p) for m, p in ms.items()} for b, ms in d["positions"].items()},
            states=d["states"], orders=[Order(**o) for o in d["orders"]], fills=d["fills"],
            settled=d["settled"], marks=d["marks"], started_at=d["started_at"],
            bot_started=d.get("bot_started") or {}, settled_at=d.get("settled_at") or {})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), separators=(",", ":")))
        tmp.replace(path)

    def drop_dead_state(self) -> int:
        """Forget bots' scan state on settled markets they never traded.

        A settled market never trades again, and only traded markets' state is
        read afterwards (rule check, paired cost, first-fill hour, siblings).
        Bot-wide keys such as "_market" and "_livesim" are not market slugs."""
        n = 0
        for bot, ms in self.states.items():
            if bot.startswith("_"):
                continue
            held = self.positions.get(bot, {})
            dead = [s for s in ms if s in self.settled and s not in held]
            for s in dead:
                del ms[s]
            n += len(dead)
        return n

    def position(self, bot: str, slug: str) -> Position:
        return self.positions.setdefault(bot, {}).setdefault(slug, Position())

    def state(self, bot: str, slug: str) -> dict:
        return self.states.setdefault(bot, {}).setdefault(slug, {})

    def available_cash(self, bot: str) -> float:
        """Bankroll plus settled P&L, minus worst-case loss of open positions and
        the cash resting orders would lock if they filled."""
        free = self.bankroll
        for slug, p in self.positions.get(bot, {}).items():
            if slug in self.settled:
                free += p.cash + p.contracts * self.settled[slug]
            else:
                free += worst_case(p.cash, p.contracts)
        for o in self.orders:
            if o.bot == bot:
                # An order that closes a held position cashes out instead of
                # spending, so it reserves only the part that would open new risk.
                pos = self.positions.get(bot, {}).get(o.slug) or Position()
                free -= max(0.0, cash_needed(pos, o.side, o.price, o.contracts, 0.0))
        return free

    def record(self, bot: str, slug: str, side: str, price: float, contracts: float,
               fee: float, kind: str, tag: str, fair: float | None, now: datetime) -> Fill:
        pos = self.position(bot, slug)
        signed = contracts if side == "long" else -contracts
        long_px = price if side == "long" else 1 - price
        pos.cash -= signed * long_px + fee
        pos.contracts += signed
        pos.fees += fee
        self.fills.append({"t": _iso(now), "bot": bot, "slug": slug, "side": side,
                           "price": round(price, 4), "contracts": round(contracts, 4),
                           "fee": round(fee, 4), "kind": kind, "tag": tag,
                           "fair": None if fair is None else round(fair, 4)})
        return Fill(side, price, contracts, tag)


@dataclass
class Tracked:
    m: usmarket.UsMarket
    link: reference.Link | None
    last_poll: float = -1e18
    prev_fair_long: float | None = None


class Engine:
    def __init__(self, ledger: Ledger, leagues: list[str], taker_theta: float, log,
                 horizon_h: float):
        self.ledger, self.leagues, self.theta, self.log = ledger, leagues, taker_theta, log
        self.horizon = timedelta(hours=horizon_h)
        self.markets: dict[str, Tracked] = {}
        self.events: dict[str, dict | None] = {}
        self.stats = {"polls": 0, "takes": 0, "maker_fills": 0, "no_cash": 0}
        self.bots = dict(BOTS)   # the live runner narrows this to one bot
        # Shared context some bots read (View.intl/others/market/series/learned).
        self.latest: dict[str, dict] = {}            # slug -> last observation
        self.by_event: dict[str, set[str]] = {}      # event slug -> slugs seen
        self.series: dict[str, deque] = {}           # slug -> recent samples
        self.learned: dict = {}
        self._next_learn = 0.0
        self.removed: dict[str, str] = {}             # bots taken off this run, and why
        self._stats_cache: dict[str, tuple[float, dict]] = {}
        ledger.states.setdefault(learn.MARKET, {}).setdefault("_meta", {"since": _iso(_now())})
        if livesim.migrate_keys(ledger):
            log("renamed live-like copies to Live_19, Live_15, Live_swisstony-v2")
        for key in self.bots:   # a bot added to an existing ledger starts its own clock
            if key not in ledger.bot_started:
                traded = key in ledger.positions or key in ledger.states
                ledger.bot_started[key] = ledger.started_at if traded and ledger.started_at \
                    else _iso(_now())
        # Resting orders live one poll unless re-issued. Orders saved by an earlier
        # run were not watched in between, so they must not fill on that gap's trades.
        if ledger.orders:
            log(f"dropped {len(ledger.orders)} resting orders from the previous run")
            ledger.orders = []

    # -- discovery ---------------------------------------------------------
    def discover(self) -> None:
        """Refresh every league at once (start of a run, tests)."""
        for lg in self.leagues:
            self.discover_league(lg)
        self._next_league = 0
        self._log_markets()

    @property
    def first_pass_done(self) -> bool:
        return getattr(self, "_passes", 0) > 0

    def discover_next(self) -> None:
        """Refresh one league, round-robin, so live polling never waits for a
        whole discovery pass. The caller runs the first pass back to back (one
        league between polls), then spreads later passes over its interval."""
        if not self.leagues:
            return
        i = getattr(self, "_next_league", 0) % len(self.leagues)
        self.discover_league(self.leagues[i])
        self._next_league = i + 1
        if self._next_league == len(self.leagues):
            self._passes = getattr(self, "_passes", 0) + 1
            self._log_markets()

    def discover_league(self, lg: str) -> None:
        now = _now()
        with_lines = any(set(b.kinds) - {"moneyline"} for b in self.bots.values())
        try:
            found = usmarket.discover(lg, with_lines,
                                      starts=(now - LIVE_WINDOW, now + self.horizon))
        except Exception as e:  # an outage keeps what we already track
            self.log(f"discover {lg}: {e}")
            return
        seen = set()
        for m in found:
            if m.start > now + self.horizon or m.start < now - LIVE_WINDOW:
                continue
            if not any(b.wants(m, True) or b.wants(m, False) for b in self.bots.values()):
                continue
            seen.add(m.slug)
            if m.slug in self.markets:
                continue
            if m.event_slug not in self.events:
                self.events[m.event_slug] = reference.find_event(m)
            ev = self.events[m.event_slug]
            self.markets[m.slug] = Tracked(m, reference.link(m, ev) if ev else None)
        for slug, t in list(self.markets.items()):
            if t.m.league == lg and slug not in seen:   # finished, closed, or not the main line
                del self.markets[slug]
                self._cancel(slug)

    def _log_markets(self) -> None:
        now = _now()
        linked = sum(t.link is not None for t in self.markets.values())
        live = sum(t.m.start <= now for t in self.markets.values() if t.link)
        self.log(f"discovered {len(self.markets)} markets, {linked} linked, {live} linked and live")

    # -- one poll ------------------------------------------------------------
    def poll(self, fast: float, slow: float) -> None:
        """Poll due markets: live games first, then at most PREGAME_PER_POLL
        upcoming ones, in groups of POLL_GROUP. Each group reads its international
        prices right before its US books, and a bot acts only while that price is
        under MAX_FAIR_AGE seconds old, so no decision uses a stale fair value."""
        now, mono = _now(), time.monotonic()
        live_due, pre_due = [], []
        for t in self.markets.values():
            live = t.m.start <= now
            if t.link is None or not any(allowed(b, t.m) and b.wants(t.m, live)
                                         for b in self.bots.values()):
                continue
            if mono - t.last_poll >= (fast if live else slow):
                (live_due if live else pre_due).append(t)
        live_due.sort(key=lambda t: t.last_poll)
        pre_due.sort(key=lambda t: t.last_poll)
        due = live_due + pre_due[:PREGAME_PER_POLL]
        if mono >= self._next_learn:
            try:
                self.learned = learn.learn(self.ledger)
            except Exception as e:   # a summary must never stop trading
                self.log(f"learn: {e}")
            self._next_learn = mono + LEARN_EVERY_S
        for i in range(0, len(due), POLL_GROUP):
            self._poll_group(due[i:i + POLL_GROUP])

    def _poll_group(self, group: list[Tracked]) -> None:
        mids = reference.live_mids([t.link for t in group])
        read_at = time.monotonic()
        books = list(_BOOK_POOL.map(_read_book, [t.m.slug for t in group]))
        for t, bk in zip(group, books):
            t.last_poll = time.monotonic()
            if isinstance(bk, Exception):
                self.log(f"book {t.m.slug}: {bk}")
                continue
            fair = reference.live_fair(t.link, mids)
            stale = fair is not None and time.monotonic() - read_at > MAX_FAIR_AGE
            if stale:
                self.stats["stale"] = self.stats.get("stale", 0) + 1
            self.stats["polls"] += 1
            if bk.bid is not None and bk.ask is not None:
                self.ledger.marks[t.m.slug] = (bk.bid + bk.ask) / 2
            else:
                self.ledger.marks.pop(t.m.slug, None)   # no two-sided book: no mark
            now = _now()
            self._settle_orders(t, bk, now)
            if bk.state not in (None, "MARKET_STATE_OPEN") or fair is None or stale:
                self._cancel(t.m.slug)   # no fresh fair value: nothing may rest or trade
                if not stale:
                    t.prev_fair_long = fair
                continue
            live = t.m.start <= now
            ctx = self._observe(t, bk, fair, live, now)
            for key, bot in list(self.bots.items()):
                try:
                    self._step_bot(key, bot, t, bk, fair, live, now, ctx)
                except Exception as e:   # one broken bot must not stop the others
                    self.bots.pop(key, None)
                    self._cancel(t.m.slug, key)
                    self.removed[key] = f"{type(e).__name__}: {e}"
                    self.log(f"removed bot {key} for this run: {type(e).__name__}: {e}")
            t.prev_fair_long = fair

    def _step_bot(self, key, bot, t: "Tracked", bk, fair: float, live: bool, now: datetime,
                  ctx: dict) -> None:
        if not allowed(bot, t.m) or not bot.wants(t.m, live):
            self._cancel(t.m.slug, key)
            return
        pos = self.ledger.positions.get(key, {}).get(t.m.slug)   # read only
        st = self.ledger.state(key, t.m.slug)
        sib = self.siblings(key, t.m) if bot.one_line_per_game else 0
        stats = self.bot_stats(key, now) if bot.needs_stats else None
        v = View(t.m, bk, fair, t.prev_fair_long, live, now, self.ledger.bankroll,
                 self.cash_for(key), pos.contracts if pos else 0.0, self.theta, sib, stats,
                 **ctx)
        acts = bot.step(v, st)
        if acts:   # remember the game and market type (see siblings())
            st.setdefault("event", t.m.event_slug)
            st.setdefault("kind", t.m.kind)
        self._apply(key, bot, t, bk, fair, acts, now)

    def _observe(self, t: "Tracked", bk, fair: float, live: bool, now: datetime) -> dict:
        """Record this poll for the bots that read history or other markets."""
        m = t.m
        short = bk.side_asks("short")
        mid = (bk.bid + bk.ask) / 2 if bk.bid is not None and bk.ask is not None else None
        intl = reference.live_top(t.link)
        self.latest[m.slug] = {"slug": m.slug, "kind": m.kind, "long_name": m.long_name,
                               "line": m.line, "is_draw": m.is_draw, "fair": fair,
                               "prev": t.prev_fair_long, "ask_long": bk.ask,
                               "ask_short": short[0][0] if short else None, "mid": mid}
        self.by_event.setdefault(m.event_slug, set()).add(m.slug)
        ts = now.timestamp()
        q = self.series.setdefault(m.slug, deque())
        q.append((ts, fair, bk.ask, short[0][0] if short else None, bk.shares_traded,
                  bk.open_interest, mid, intl["spread"] if intl else None))
        while q and ts - q[0][0] > SERIES_S:
            q.popleft()
        rec = self.ledger.state(learn.MARKET, m.slug)
        if not rec:
            rec.update({"first_seen": _iso(now), "start": _iso(m.start), "league": m.league})
        if live:
            if "lo" not in rec:
                rec["first_live"] = fair
                rec["from_start"] = now - m.start <= timedelta(minutes=15)
                rec["lo"] = rec["hi"] = fair
            rec["lo"], rec["hi"] = min(rec["lo"], fair), max(rec["hi"], fair)
            rec["last_live"] = _iso(now)
        else:
            rec["pre"] = fair
        others = tuple(self.latest[s] for s in self.by_event[m.event_slug]
                       if s != m.slug and s in self.latest)
        return {"intl": intl, "others": others, "market": rec, "series": tuple(q),
                "learned": self.learned}

    def siblings(self, key: str, m: usmarket.UsMarket) -> int:
        """Other lines of m's game and market type this bot has traded or rests on.
        Traded markets carry their event and kind in the bot's state, so lines
        that are no longer the main line (or were traded before a restart) count."""
        n = 0
        for slug, st in self.ledger.states.get(key, {}).items():
            if slug != m.slug and st.get("event") == m.event_slug and st.get("kind") == m.kind \
                    and st.get("legs"):
                n += 1
        for o in self.ledger.orders:
            if o.bot == key and o.slug != m.slug and o.slug in self.markets:
                om = self.markets[o.slug].m
                n += om.event_slug == m.event_slug and om.kind == m.kind
        return n

    def bot_stats(self, key: str, now: datetime) -> dict:
        """A bot's own results (see View.stats), from the ledger. Cached for
        STATS_CACHE_S per bot: results change only as markets settle."""
        mono = time.monotonic()
        hit = self._stats_cache.get(key)
        if hit is not None and mono - hit[0] < STATS_CACHE_S and hit[1]["day"] == now.strftime("%Y-%m-%d"):
            return hit[1]
        today = now.strftime("%Y-%m-%d")
        done, open_by_cat = [], {}
        by_cat: dict[str, list] = {}      # sport -> [wins, n, pnl, wins today, n today, pnl today]
        by_hour: dict[int, list] = {}     # UTC hour of the first buy -> [wins, n]
        states = self.ledger.states.get(key, {})
        for slug, p in self.ledger.positions.get(key, {}).items():
            if abs(p.cash) < 1e-9 and abs(p.contracts) < 1e-9:
                continue
            cat = category_of(slug.split("-", 1)[1]) if "-" in slug else "other"
            if slug in self.ledger.settled:
                when = self.ledger.settled_at.get(slug, "")
                v = p.cash + p.contracts * self.ledger.settled[slug]
                done.append((when, v))
                c = by_cat.setdefault(cat, [0, 0, 0.0, 0, 0, 0.0])
                c[0] += v > 0
                c[1] += 1
                c[2] += v
                if when.startswith(today):
                    c[3] += v > 0
                    c[4] += 1
                    c[5] += v
                first = (states.get(slug) or {}).get("first_fill")
                if first:
                    h = by_hour.setdefault(int(first[11:13]), [0, 0])
                    h[0] += v > 0
                    h[1] += 1
            else:
                open_by_cat[cat] = open_by_cat.get(cat, 0.0) + max(0.0, -p.cash)
        done.sort()
        orders = (livesim.counts(self.ledger, key).get("by_hour") or {})
        out = {"pnl_today": sum(v for t, v in done if t.startswith(today)),
               "last_results": [v for _, v in done[-10:]], "open_by_cat": open_by_cat,
               "by_cat": by_cat, "by_hour": by_hour,
               "orders_by_hour": {int(h): tuple(x) for h, x in orders.items()}, "day": today}
        self._stats_cache[key] = (mono, out)
        return out

    def cash_for(self, key: str) -> float:
        return self.ledger.available_cash(key)

    def _cancel(self, slug: str, bot: str | None = None) -> None:
        self.ledger.orders = [o for o in self.ledger.orders
                              if not (o.slug == slug and (bot is None or o.bot == bot))]

    def _settle_orders(self, t: Tracked, bk: usmarket.Book, now: datetime) -> None:
        keep = []
        for o in self.ledger.orders:
            if o.slug != t.m.slug:
                keep.append(o)
                continue
            filled = 0.0
            # A Live_ copy's bid waits behind others at its price: it fills only on
            # trades strictly through it, and only its queue share, in whole contracts.
            live = getattr(BOTS.get(o.bot), "live_sim", False)
            tol = -1e-9 if live else 1e-9
            asks = bk.side_asks(o.side)
            if asks and asks[0][0] <= o.price + 1e-9:     # the book crossed our bid
                filled = min(o.contracts, asks[0][1])
            elif (bk.shares_traded is not None and o.traded_at_place is not None
                  and bk.shares_traded > o.traded_at_place and bk.last_trade is not None):
                # A long bid at p fills on trades at <= p; a short bid at q is an
                # offer on the long at 1-q and fills on trades at >= 1-q.
                through = (bk.last_trade <= o.price + tol if o.side == "long"
                           else bk.last_trade >= 1 - o.price - tol)
                if through:
                    filled = min(o.contracts, bk.shares_traded - o.traded_at_place)
            if live:
                filled = livesim.queue_share(filled)
            if filled > 1e-9:
                f = self.ledger.record(o.bot, o.slug, o.side, o.price, filled, 0.0, "maker",
                                       o.tag, None, now)
                BOTS[o.bot].on_fill(self.ledger.state(o.bot, o.slug), f, now)
                self.stats["maker_fills"] += 1
                o.contracts -= filled
            o.traded_at_place = bk.shares_traded
            if o.contracts > 1e-6:
                keep.append(o)
        self.ledger.orders = keep

    def _apply(self, key, bot, t: Tracked, bk, fair_long, acts, now) -> None:
        self._cancel(t.m.slug, key)   # resting orders live one poll unless re-issued
        st = self.ledger.state(key, t.m.slug)
        for a in acts:
            fair = fair_long if a.side == "long" else 1 - fair_long
            if isinstance(a, Take) and getattr(bot, "live_sim", False):
                livesim.take(self, key, bot, t, a, fair, now, st)
            elif isinstance(a, Take):
                budget = a.dollars
                for px, qty in bk.side_asks(a.side):
                    if px > a.limit + 1e-9 or budget < 0.01:
                        break
                    n = min(qty, budget / px)
                    fee = fees.taker_fee(n, px, self.theta)
                    pos = self.ledger.position(key, t.m.slug)
                    if cash_needed(pos, a.side, px, n, fee) > self.ledger.available_cash(key) + 1e-9:
                        self.stats["no_cash"] += 1
                        break
                    f = self.ledger.record(key, t.m.slug, a.side, px, n, fee, "taker", a.tag,
                                           fair, now)
                    bot.on_fill(st, f, now)
                    self.stats["takes"] += 1
                    budget -= n * px
            elif isinstance(a, Rest):
                if not 0 < a.price < 1 or a.dollars < 0.5:
                    continue
                n = a.dollars / a.price
                pos = self.ledger.position(key, t.m.slug)
                if cash_needed(pos, a.side, a.price, n, 0.0) > self.ledger.available_cash(key) + 1e-9:
                    self.stats["no_cash"] += 1
                    continue
                self.ledger.orders.append(Order(key, t.m.slug, a.side, round(a.price, 4), n,
                                                a.tag, _iso(now), bk.shares_traded))

    # -- settlement ----------------------------------------------------------
    def settle(self) -> int:
        n = 0
        # Netted positions (both sides bought) settle too, so their locked-in
        # result is reported as settled rather than left open forever.
        slugs = {s for ms in self.ledger.positions.values() for s, p in ms.items()
                 if s not in self.ledger.settled
                 and (abs(p.contracts) > 1e-9 or abs(p.cash) > 1e-9)}
        for slug in sorted(slugs):
            px = usmarket.settlement(slug)
            if px is not None:
                self.ledger.settled[slug] = px
                self.ledger.settled_at[slug] = _iso(_now())
                n += 1
        self.ledger.drop_dead_state()
        return n


def paired_cost(st: dict) -> float | None:
    legs = st.get("legs") or {}
    lg, sh = legs.get("long"), legs.get("short")
    if not lg or not sh or not lg["contracts"] or not sh["contracts"]:
        return None
    return lg["cost"] / lg["contracts"] + sh["cost"] / sh["contracts"]


def _entry_long_px(led: Ledger, bot: str, slug: str, contracts: float) -> float:
    """Average entry price of the held side, as a long-side price."""
    side = "long" if contracts > 0 else "short"
    n = cost = 0.0
    for f in led.fills:
        if f["bot"] == bot and f["slug"] == slug and f["side"] == side:
            n += f["contracts"]
            cost += f["contracts"] * f["price"]
    if n <= 0:
        return 0.0
    avg = cost / n
    return avg if side == "long" else 1 - avg


def report(led: Ledger, keys: list[str] | None = None) -> str:
    lines = [f"Paper bankroll ${led.bankroll:,.0f} per bot since {led.started_at}. No real orders.", "",
             "| Bot | Fills taker/maker | Markets traded | Open | Settled P&L | Fees "
             "| Equity (open at US mid, else cost) | Cash free | Both-sides markets (avg paired cost) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for key in keys or list(BOTS):
        fills = [f for f in led.fills if f["bot"] == key]
        taker = sum(f["kind"] == "taker" for f in fills)
        settled_pnl = fee_total = 0.0
        equity = led.bankroll
        open_n = traded = 0
        for slug, p in led.positions.get(key, {}).items():
            if abs(p.cash) < 1e-9 and abs(p.contracts) < 1e-9:
                continue
            traded += 1
            fee_total += p.fees
            if slug in led.settled:
                v = p.cash + p.contracts * led.settled[slug]
                settled_pnl += v
                equity += v
            else:
                open_n += abs(p.contracts) > 1e-9
                if slug in led.marks:
                    equity += p.cash + p.contracts * led.marks[slug]
                else:   # unmarked: the held contracts are carried at their entry price
                    equity += p.cash + p.contracts * _entry_long_px(led, key, slug, p.contracts)
        pcs = [pc for pc in (paired_cost(st) for st in led.states.get(key, {}).values()) if pc]
        both = f"{len(pcs)} (${sum(pcs) / len(pcs):.3f})" if pcs else "0"
        lines.append(f"| {key} | {taker}/{len(fills) - taker} | {traded} | {open_n} "
                     f"| ${settled_pnl:,.2f} | ${fee_total:,.2f} | ${equity:,.2f} "
                     f"| ${led.available_cash(key):,.2f} | {both} |")
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description="Paper-trade the seven report bots. Sends no orders.")
    p.add_argument("--minutes", type=float, default=60.0, help="how long to run")
    p.add_argument("--fast", type=float, default=15.0, help="seconds between polls of live games")
    p.add_argument("--slow", type=float, default=180.0, help="seconds between polls of upcoming games")
    p.add_argument("--discover", type=float, default=900.0, help="seconds between market discovery")
    p.add_argument("--horizon", type=float, default=24.0, help="hours ahead to trade upcoming games")
    p.add_argument("--leagues", nargs="*", default=DEFAULT_LEAGUES + EXTRA_LEAGUES)
    p.add_argument("--bankroll", type=float, default=1000.0, help="paper dollars per bot")
    p.add_argument("--taker-theta", type=float, default=fees.WORST_TAKER_THETA)
    p.add_argument("--ledger", default=str(STATE / "ledger.json"))
    p.add_argument("--report", action="store_true", help="only settle and print the report")
    a = p.parse_args(argv)
    path = Path(a.ledger)
    led = Ledger.load(path, a.bankroll)
    log_path = path.with_name(path.stem + ".log")

    def log(msg: str) -> None:
        line = f"{_iso(_now())} {msg}"
        print(line, flush=True)
        with log_path.open("a") as f:
            f.write(line + "\n")

    eng = Engine(led, a.leagues, a.taker_theta, log, a.horizon)
    if not a.report:
        end = time.monotonic() + a.minutes * 60
        stop = []
        # SIGTERM ends the run early but cleanly: settle, save and report as usual.
        signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
        next_discover = next_settle = next_save = 0.0
        while time.monotonic() < end and not stop:
            mono = time.monotonic()
            if mono >= next_discover:
                eng.discover_next()
                next_discover = mono + (a.discover / max(1, len(eng.leagues))
                                        if eng.first_pass_done else 0.0)
            eng.poll(a.fast, a.slow)
            if mono >= next_settle:
                n = eng.settle()
                if n:
                    log(f"settled {n} markets")
                next_settle = mono + 600
            if mono >= next_save:
                led.save(path)
                log(f"stats {json.dumps(eng.stats)} resting={len(led.orders)}")
                next_save = mono + 120
            time.sleep(1.0)
    n = eng.settle()
    led.save(path)
    log(f"final settle {n}; stats {json.dumps(eng.stats)}")
    for key, why in eng.removed.items():
        log(f"taken off this run: {key} ({why})")
    print(report(led))


if __name__ == "__main__":
    main()
