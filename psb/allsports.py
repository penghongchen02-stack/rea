"""Fifteen all-sports bots (a01-a15), each with a Live_ copy in livesim.py.

Each one works alone: it reads only market prices (US and international) and
its own results (View.stats), never another bot's, so any one of them could be
the single bot an account runs. Paper money; $10 a buy unless it sizes itself;
never above the international fair value after the fee.

Several measure the market as they go (how slow the US site is, how long a
cheap price lasts); that memory lives in the bot for the run and is rebuilt
after a restart.
"""
from __future__ import annotations

from collections import deque

from . import fees
from .bots import SIDES
from .fifty import _covers, _window
from .simple_bots import EPS, Simple, best_edge_side

JUMP = 0.015   # #15 x06's jump


def _jump_side(v) -> str | None:
    if v.prev_fair_long is None:
        return None
    d = v.fair_long - v.prev_fair_long
    if abs(d) < JUMP - EPS:
        return None
    return "long" if d > 0 else "short"


def _gap(fair: float, ask: float | None, theta: float) -> float | None:
    if ask is None:
        return None
    return fair - ask - fees.taker_fee(1, ask, theta)


class A(Simple):
    """Base: any sport, live and before play, the bot's own results in v.stats."""
    needs_stats = True
    edge = 0.005

    def own_cat(self, v) -> list:
        return ((v.stats or {}).get("by_cat") or {}).get(v.category) or [0, 0, 0.0, 0, 0, 0.0]

    def bet(self, v, st) -> float:
        return self.clip

    def size(self, v, st, side):
        return self.bet(v, st)


class A01SportSwitcher(A):
    key, title = "a01-sport-switcher", "Sport switcher: $15 where it is up today, $5 where down"
    live_only = True

    def pick(self, v, st):
        s = _jump_side(v)
        return (s, self.edge) if s else None

    def bet(self, v, st):
        today = self.own_cat(v)[5]
        return 15.0 if today > 0 else 5.0 if today < 0 else 10.0


class A02SlowSports(A):
    """Measures, per sport, how many seconds the US mid takes to follow a 1.5c+
    international jump; buys jumps at $15 in the slower half of sports, $5 in
    the faster half, $10 until it has measured both."""
    key, title = "a02-slow-sports", "Speed-ranked sports: bet more where the US site is slowest"
    live_only = True

    def __init__(self):
        self.lag: dict[str, float] = {}
        self.done: set[tuple[str, float]] = set()

    def learn(self, v):
        s = v.series
        for i in range(1, len(s) - 1):
            d = s[i][1] - s[i - 1][1]
            if abs(d) < JUMP - EPS or (v.m.slug, s[i][0]) in self.done or s[i][6] is None:
                continue
            for later in s[i + 1:]:
                if later[6] is not None and (later[6] - s[i][6]) * d >= 0.01 - EPS:
                    lag = min(60.0, later[0] - s[i][0])
                    old = self.lag.get(v.category)
                    self.lag[v.category] = lag if old is None else 0.8 * old + 0.2 * lag
                    self.done.add((v.m.slug, s[i][0]))
                    break
                if later[0] - s[i][0] > 60:
                    self.done.add((v.m.slug, s[i][0]))
                    break

    def pick(self, v, st):
        self.learn(v)
        s = _jump_side(v)
        return (s, self.edge) if s else None

    def bet(self, v, st):
        mine = self.lag.get(v.category)
        if mine is None or len(self.lag) < 2:
            return 10.0
        lags = sorted(self.lag.values())
        return 15.0 if mine >= lags[len(lags) // 2] else 5.0


class A03BusyGames(A):
    """Keeps each market's shares traded in its last 10 minutes; buys jumps only
    in the 10 busiest markets it has seen in the last 5 minutes."""
    key, title = "a03-busy-games", "Busy-game focus: only the 10 most-traded games now"
    live_only = True

    def __init__(self):
        self.busy: dict[str, tuple[float, float]] = {}   # slug -> (time, traded last 10 min)

    def pick(self, v, st):
        w = _window(v.series, 600)
        now = v.now.timestamp()
        if w and w[0][4] is not None and w[-1][4] is not None:
            self.busy[v.m.slug] = (now, w[-1][4] - w[0][4])
        fresh = sorted((x[1] for k, x in self.busy.items() if now - x[0] <= 300), reverse=True)
        mine = self.busy.get(v.m.slug)
        if mine is None or not fresh or mine[1] <= 0 or mine[1] < fresh[min(9, len(fresh) - 1)]:
            return None
        s = _jump_side(v)
        return (s, self.edge) if s else None


class A04Confirmed(A):
    key, title = "a04-confirmed", "Two-speed confirm: a jump the US site starts to follow"
    # Between its last two checks, the international price jumped 1.5c+ and the US
    # mid moved the same way by at least 0.5c but less than the jump.
    live_only = True

    def pick(self, v, st):
        # The last two checks of this game (a busy run checks each game every
        # 10-20 s, so a fixed 10 s window would often hold only one).
        w = v.series[-2:]
        if len(w) < 2 or w[-1][0] - w[0][0] > 30 or w[0][6] is None or w[-1][6] is None:
            return None
        d, us = w[-1][1] - w[0][1], w[-1][6] - w[0][6]
        if abs(d) >= JUMP - EPS and us * d > 0 and abs(us) >= 0.005 - EPS and abs(us) < abs(d):
            return ("long" if d > 0 else "short", self.edge)
        return None


class A05BiggestGap(A):
    """One bet a minute across all sports: the first market whose gap is at least
    1c and at least the biggest gap seen in the minute before."""
    key, title = "a05-biggest-gap", "Biggest gap of the minute: one bet a minute"

    def __init__(self):
        self.best: dict[int, float] = {}
        self.bet_minute = None

    def pick(self, v, st):
        minute = int(v.now.timestamp() // 60)
        s = best_edge_side(v)
        e = v.taker_edge(s) if s else None
        if e is None:
            return None
        self.best[minute] = max(self.best.get(minute, 0.0), e)
        for k in [k for k in self.best if k < minute - 1]:
            del self.best[k]
        if self.bet_minute == minute or e < max(0.01, self.best.get(minute - 1, 0.0)) - EPS:
            return None
        self.bet_minute = minute
        return (s, 0.01)


class A06GrowingGap(A):
    key, title = "a06-growing-gap", "Gap that keeps growing: wider two checks in a row"

    def pick(self, v, st):
        s = best_edge_side(v)
        e = v.taker_edge(s) if s else None
        hist = st.setdefault("gaps", [])
        hist.append((s, e))
        del hist[:-3]
        if len(hist) < 3 or e is None or any(h[0] != s or h[1] is None for h in hist):
            return None
        if hist[0][1] < hist[1][1] < hist[2][1] and e >= 0.005 - EPS:
            return (s, 0.005)
        return None


class A07PriceStep(A):
    """Learns each sport's usual US price step (the median non-zero move of the
    US mid between checks) and buys when the US price is a full step behind."""
    key, title = "a07-price-step", "Price-step: the US price a full usual step behind"

    def __init__(self):
        self.moves: dict[str, deque] = {}

    def pick(self, v, st):
        m0, m1 = st.get("mid_prev"), st.get("mid_last")
        q = self.moves.setdefault(v.category, deque(maxlen=200))
        if m0 is not None and m1 is not None and abs(m1 - m0) >= 0.005:
            q.append(abs(m1 - m0))
        if len(q) < 20:
            return None
        step = sorted(q)[len(q) // 2]
        for s in SIDES:
            a = v.ask(s)
            if a is not None and v.fair(s) - a >= step - EPS:
                return (s, 0.0)
        return None


class A08SpreadToWinner(A):
    key, title = "a08-spread-to-winner", "Spread moved, winner price did not: buy the winner"

    def pick(self, v, st):
        m0, m1 = st.get("mid_prev"), st.get("mid_last")
        if v.m.kind != "moneyline" or m0 is None or m1 is None or abs(m1 - m0) >= 0.01:
            return None
        for o in v.others:
            if o["kind"] == "spreads" and o["long_name"] == v.m.long_name and o["prev"] is not None:
                d = o["fair"] - o["prev"]
                if abs(d) >= 0.03 - EPS:
                    return ("long" if d > 0 else "short", 0.0)
        return None


class A09CalmThenStorm(A):
    key, title = "a09-calm-then-storm", "Calm-then-storm: the first 2c move after 10 flat minutes"
    live_only = True

    def pick(self, v, st):
        w = _window(v.series, 600)
        if not _covers(w, 540) or len(w) < 3:
            return None
        calm = w[:-1]
        if max(x[1] for x in calm) - min(x[1] for x in calm) > 0.01 + EPS:
            return None
        d = w[-1][1] - w[-2][1]
        return ("long" if d > 0 else "short", 0.0) if abs(d) >= 0.02 - EPS else None


class A10SelfHours(A):
    """#15 x06's jump, sized by its own record for this UTC hour: $3 once 5+ of its
    bets started this hour and they win under 35%, or (Live_) 50%+ of its orders
    this hour missed; $10 otherwise."""
    key, title = "a10-self-hours", "Self-learning hours: bet small in its own bad hours"
    live_only = True

    def pick(self, v, st):
        s = _jump_side(v)
        return (s, self.edge) if s else None

    def bet(self, v, st):
        stats, h = v.stats or {}, v.now.hour
        w, n = (stats.get("by_hour") or {}).get(h, (0, 0))
        o, miss = (stats.get("orders_by_hour") or {}).get(h, (0, 0))
        if (n >= 5 and w / n < 0.35) or (o >= 5 and miss / o >= 0.5):
            return 3.0
        return 10.0


class A11SameTimePair(A):
    """A favourite in one game collapsed 15c+ in 5 minutes: back the favourite
    (55-85c) of another live game in the same league in the next 5 minutes."""
    key, title = "a11-same-time-pair", "Same-time pairs: back another league favourite after a collapse"
    live_only = True
    max_entries = 1

    def __init__(self):
        self.collapse: dict[str, tuple[float, str]] = {}   # league -> (time, event)

    def pick(self, v, st):
        now = v.now.timestamp()
        old = _window(v.series, 300)
        if old:
            for s in SIDES:
                f0 = old[0][1] if s == "long" else 1 - old[0][1]
                if f0 >= 0.55 and f0 - v.fair(s) >= 0.15 - EPS:
                    self.collapse[v.m.league] = (now, v.m.event_slug)
        c = self.collapse.get(v.m.league)
        if c is None or c[1] == v.m.event_slug or now - c[0] > 300:
            return None
        s = next((s for s in SIDES if 0.55 <= v.fair(s) <= 0.85), None)
        return (s, self.edge) if s else None


class A12StaleMarket(A):
    key, title = "a12-stale-market", "Stale market: no US trade in 30 min, international moved 3c+"

    def pick(self, v, st):
        w = _window(v.series, 1800)
        if not _covers(w, 1740) or w[0][4] is None or w[-1][4] != w[0][4]:
            return None
        d = w[-1][1] - w[0][1]
        return ("long" if d > 0 else "short", 0.0) if abs(d) >= 0.03 - EPS else None


class A13PriceLifetime(A):
    """Measures, per sport, how long a 1c+ US gap lasts; buys 1c+ gaps only in
    sports where they usually last 15 seconds or more."""
    key, title = "a13-price-lifetime", "Price lifetime: gaps only where cheap prices last 15s+"

    def __init__(self):
        self.life: dict[str, float] = {}
        self.open: dict[str, float] = {}   # slug -> when its current gap opened

    def pick(self, v, st):
        now = v.now.timestamp()
        s = best_edge_side(v)
        e = v.taker_edge(s) if s else None
        if e is not None and e >= 0.01:
            self.open.setdefault(v.m.slug, now)
        elif v.m.slug in self.open:
            dur = now - self.open.pop(v.m.slug)
            old = self.life.get(v.category)
            self.life[v.category] = dur if old is None else 0.8 * old + 0.2 * dur
        if self.life.get(v.category, 0.0) < 15 or e is None or e < 0.01:
            return None
        return (s, 0.01)


class A14WinRateLeash(A):
    key, title = "a14-win-rate-leash", "Win-rate leash: pause a sport after a bad day (own record)"
    live_only = True

    def pick(self, v, st):
        c = self.own_cat(v)
        if c[4] >= 5 and c[3] / c[4] < 0.35:
            return None
        s = _jump_side(v)
        return (s, self.edge) if s else None


class A15SizeBySport(A):
    key, title = "a15-size-by-sport", "Bet size by sport: $15 where it wins, $5 where it loses"
    live_only = True

    def pick(self, v, st):
        s = _jump_side(v)
        return (s, self.edge) if s else None

    def bet(self, v, st):
        c = self.own_cat(v)
        if c[1] < 10:
            return 10.0
        return 15.0 if c[2] > 0 else 5.0


ALL_CLASSES = (A01SportSwitcher, A02SlowSports, A03BusyGames, A04Confirmed, A05BiggestGap,
               A06GrowingGap, A07PriceStep, A08SpreadToWinner, A09CalmThenStorm,
               A10SelfHours, A11SameTimePair, A12StaleMarket, A13PriceLifetime,
               A14WinRateLeash, A15SizeBySport)
ALL15 = tuple(c() for c in ALL_CLASSES)
