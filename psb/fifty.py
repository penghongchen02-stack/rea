"""Fifty more bots (n01-n50), each with a Live_ copy in livesim.py. Paper money.

Unless a bot says otherwise it bets $10 a time, up to 3 times a market ($30),
only at a US price no higher than the international fair value after the fee,
in any sport, before or during play. Many read context the engine adds to the
View (bots.View: intl, others, market, series, learned); see learn.py for what
"in our records" means. Where a rule stands in for something we cannot see
(a game clock, a break), its docstring says so.
"""
from __future__ import annotations

from datetime import timedelta

from . import fees
from .bots import SIDES, Bot, Rest, Take, other
from .exits import ProfitLock
from .learn import band, league_of, players
from .simple_bots import EPS, S15Momentum, S19TennisSetFade, Simple, best_edge_side

TENNIS = frozenset({"tennis"})


def _side_toward(d: float) -> str:
    return "long" if d > 0 else "short"


def _ago(series, seconds: float):
    """The newest sample at least `seconds` old, or None."""
    if not series:
        return None
    cut = series[-1][0] - seconds
    old = None
    for s in series:
        if s[0] <= cut:
            old = s
        else:
            break
    return old


def _window(series, seconds: float):
    if not series:
        return ()
    cut = series[-1][0] - seconds
    return tuple(s for s in series if s[0] >= cut)


def _covers(series, seconds: float) -> bool:
    return bool(series) and series[-1][0] - series[0][0] >= seconds


def _elapsed_min(v) -> float:
    return (v.now - v.m.start).total_seconds() / 60


class N(Simple):
    """Base: $10 a buy, 3 a market, min edge 0 (fair or better after the fee)."""
    edge = 0.0
    tennis_only = False

    def step(self, v, st):
        if self.tennis_only and v.category not in TENNIS:
            return []
        return super().step(v, st)

    def side_if(self, side: str | None):
        return (side, self.edge) if side else None


# --- US order book shape -------------------------------------------------------------

class N01Pileup(N):
    key, title = "n01-pileup", "Buyer pile-up: 3x more waiting to buy than to sell"

    def pick(self, v, st):
        b = sum(q for _, q in v.book.bids[:3])
        a = sum(q for _, q in v.book.asks[:3])
        if a > 0 and b >= 3 * a:
            return self.side_if("long")
        if b > 0 and a >= 3 * b:
            return self.side_if("short")
        return None


class N02Snipe(N):
    key, title = "n02-snipe", "Last-contract snipe: a few cheap contracts before a 2c+ step up"

    def pick(self, v, st):
        for s in SIDES:
            lv = v.book.side_asks(s)
            if len(lv) >= 2 and lv[0][1] <= 25 and lv[1][0] - lv[0][0] >= 0.02 - EPS:
                st["snipe"] = lv[0][0] * lv[0][1]
                return self.side_if(s)
        return None

    def size(self, v, st, side):
        return min(self.clip, st.get("snipe", self.clip))   # the cheap level only


class N03Wall(N):
    key, title = "n03-wall", "Wall bounce: a huge bid within 2c under the price"

    def pick(self, v, st):
        for s in SIDES:
            bids, a = v.book.side_bids(s), v.ask(s)
            if a is None or len(bids) < 2:
                continue
            sizes = sorted(q for _, q in bids[:6])
            typical = sizes[len(sizes) // 2]
            for px, q in bids[:3]:
                if a - px <= 0.02 + EPS and q >= 200 and q >= 5 * typical:
                    return self.side_if(s)
        return None


class N04SpreadSnap(N):
    key, title = "n04-spread-snap", "Spread snap: a 4c+ US spread closes to 1c"
    live_only = True

    def pick(self, v, st):
        b, a = v.book.bid, v.book.ask
        prev, st["spr"] = st.get("spr"), (a - b if a is not None and b is not None else None)
        m0, m1 = st.get("mid_prev"), st.get("mid_last")
        if prev is None or st["spr"] is None or m0 is None or m1 is None:
            return None
        if prev >= 0.04 - EPS and st["spr"] <= 0.01 + EPS and abs(m1 - m0) >= 0.005:
            return self.side_if(_side_toward(m1 - m0))
        return None


class N05EmptyShelf(Bot):
    """Be the first bid on a side nobody bids for: 3c under fair, $10, $40 held at most."""
    key, title = "n05-empty-shelf", "Empty shelf: first bid where nobody bids, 3c under fair"
    max_market_frac = 0.04

    def wants(self, m, live):
        return m.kind == "moneyline"

    def step(self, v, st):
        acts = []
        for s in SIDES:
            if v.book.side_bids(s):
                continue
            px = round(v.fair(s) - 0.03, 2)
            a = v.ask(s)
            held = self.leg(st, s)[0] * (self.leg(st, s)[1] or 0)
            if 0.05 <= px <= 0.95 and (a is None or px < a) and held < 40:
                d = min(10.0, self.room(v, st))
                if d >= 1:
                    acts.append(Rest(s, px, d, "quote"))
        return acts


class N06DeepBook(N):
    key, title = "n06-deep-book", "Deep-book only: 1c+ gap where $500+ waits on both sides"
    edge = 0.01

    def pick(self, v, st):
        deep = all(sum(p * q for p, q in v.book.side_asks(s)[:3]) >= 500 for s in SIDES)
        return self.side_if(best_edge_side(v)) if deep else None


# --- US trades ---------------------------------------------------------------------

def _trade_delta(v, st) -> float:
    sh = v.book.shares_traded
    prev = st.get("sh")
    st["sh"] = sh
    if sh is None or prev is None:
        return 0.0
    return max(0.0, sh - prev)


def _trade_side(v, st) -> str | None:
    lt, m0 = v.book.last_trade, st.get("mid_prev")
    if lt is None or m0 is None or abs(lt - m0) < 1e-9:
        return None
    return _side_toward(lt - m0)


class N07Whale(N):
    key, title = "n07-whale", "Whale follower: one 500+ contract trade"

    def pick(self, v, st):
        return self.side_if(_trade_side(v, st)) if _trade_delta(v, st) >= 500 else None


class N08VolumeSpike(N):
    key, title = "n08-volume-spike", "Volume spike: trading 5x its usual pace"

    def pick(self, v, st):
        d = _trade_delta(v, st)
        ema = st.get("ema")
        st["ema"] = d if ema is None else 0.9 * ema + 0.1 * d
        if ema is None or d < 50 or d < 5 * max(ema, 1.0):
            return None
        m0, m1 = st.get("mid_prev"), st.get("mid_last")
        if m0 is None or m1 is None or abs(m1 - m0) < 0.005:
            return None
        return self.side_if(_side_toward(m1 - m0))


class N09Silence(N):
    key, title = "n09-silence", "First trade after 10 quiet minutes"

    def pick(self, v, st):
        d = _trade_delta(v, st)
        now = v.now.timestamp()
        quiet_since = st.get("quiet")
        if d <= 0:
            st.setdefault("quiet", now)
            return None
        st["quiet"] = now
        if quiet_since is None or now - quiet_since < 600:
            return None
        return self.side_if(_trade_side(v, st))


class N10Aggressive(N):
    key, title = "n10-aggressive", "Aggressive buyers: 3 trades in a row at the offer"

    def pick(self, v, st):
        d = _trade_delta(v, st)
        lt, a0, b0 = v.book.last_trade, st.get("ask0"), st.get("bid0")
        st["ask0"], st["bid0"] = v.book.ask, v.book.bid
        if d <= 0 or lt is None:
            return None
        if a0 is not None and lt >= a0 - EPS:
            st["agg"] = max(1, st.get("agg", 0) + 1)
        elif b0 is not None and lt <= b0 + EPS:
            st["agg"] = min(-1, st.get("agg", 0) - 1)
        else:
            st["agg"] = 0
        if abs(st["agg"]) >= 3:
            return self.side_if("long" if st["agg"] > 0 else "short")
        return None


class N11OpenInterest(N):
    key, title = "n11-open-interest", "Money pouring in: open bets +10% in 5 min with the price"

    def pick(self, v, st):
        old = _ago(v.series, 300)
        oi = v.book.open_interest
        if old is None or oi is None or not old[5]:
            return None
        d = v.fair_long - old[1]
        if oi >= old[5] * 1.10 and abs(d) >= 0.02:
            return self.side_if(_side_toward(d))
        return None


# --- International order book --------------------------------------------------------

class N12IntlWall(N):
    key, title = "n12-intl-wall", "International wall: 5x more bid than offered abroad"

    def pick(self, v, st):
        i = v.intl
        if not i:
            return None
        if i["bid_size"] >= 500 and i["bid_size"] >= 5 * max(i["ask_size"], 1):
            return self.side_if("long")
        if i["ask_size"] >= 500 and i["ask_size"] >= 5 * max(i["bid_size"], 1):
            return self.side_if("short")
        return None


class N13IntlConfidence(N):
    key, title = "n13-intl-confidence", "International confidence: spread 3c+ snaps to 1c"

    def pick(self, v, st):
        i = v.intl
        if not i or i["spread"] > 0.01 + EPS:
            return None
        wide = [s for s in _window(v.series, 300)[:-1] if s[7] is not None and s[7] >= 0.03 - EPS]
        if not wide:
            return None
        d = v.fair_long - wide[-1][1]
        return self.side_if(_side_toward(d)) if abs(d) >= 0.01 else None


class N14BusyAbroad(N):
    key, title = "n14-busy-abroad", "Busy abroad, quiet here: 3 international moves, no US trades"

    def pick(self, v, st):
        s = v.series[-4:]
        if len(s) < 4:
            return None
        moves = [s[i + 1][1] - s[i][1] for i in range(3)]
        trades = {x[4] for x in s}
        if all(abs(m) >= 0.005 for m in moves) and len(trades) == 1 and None not in trades:
            d = s[-1][1] - s[0][1]
            return self.side_if(_side_toward(d)) if abs(d) >= 0.015 else None
        return None


# --- Same game, other markets --------------------------------------------------------

class N15SpreadLag(N):
    """Spreads that lag their game's winner price: the winner market for the same
    team moved 3c+ on its last poll while this spread's US price did not move."""
    key, title = "n15-spread-lag", "Spread lags the winner price"
    kinds = ("spreads",)

    def pick(self, v, st):
        m0, m1 = st.get("mid_prev"), st.get("mid_last")
        if m0 is None or m1 is None or abs(m1 - m0) >= 0.01:
            return None
        for o in v.others:
            if o["kind"] == "moneyline" and o["long_name"] == v.m.long_name and o["prev"] is not None:
                d = o["fair"] - o["prev"]
                if abs(d) >= 0.03 - EPS:
                    return self.side_if(_side_toward(d))
        return None


class N16ThreeWay(Bot):
    """Soccer: when team A + team B + draw cost under $0.99 a set after fees, buy 10
    of each (each market buys its own leg when polled; legs can miss)."""
    key, title = "n16-three-way", "Soccer three-way under $1: buy all three"
    max_market_frac = 0.01

    def wants(self, m, live):
        return m.kind == "moneyline"

    def step(self, v, st):
        if st.get("arb"):
            return []
        legs = [o for o in v.others if o["kind"] == "moneyline" and o["ask_long"] is not None]
        a = v.ask("long")
        if a is None or len(legs) != 2 or sum(o["is_draw"] for o in legs) + v.m.is_draw != 1:
            return []
        cost = sum(x + fees.taker_fee(1, x, v.taker_theta) for x in [a] + [o["ask_long"] for o in legs])
        if cost > 0.99 + EPS:
            return []
        st["arb"] = True
        return [Take("long", 10 * a, a, "arb")]


# --- Tennis: replaces the ideas that needed a clock or a score -----------------------

class N17WomensDog(N):
    key, title = "n17-womens-dog", "Women's tennis underdog at 30-45c before play"
    pregame_only = True
    max_entries = 1
    edge = 0.005

    def pick(self, v, st):
        if v.m.league not in ("wta", "itfwo"):
            return None
        return self.side_if(next((s for s in SIDES if 0.30 <= v.fair(s) <= 0.45), None))


def _side_lo(rec: dict, side: str) -> float | None:
    if "lo" not in rec:
        return None
    return rec["lo"] if side == "long" else 1 - rec["hi"]


class N18DominantCloser(N):
    key, title = "n18-dominant-closer", "Tennis: 85-95c and never under 70c this match"
    live_only, tennis_only = True, True

    def pick(self, v, st):
        rec = v.market or {}
        if not rec.get("from_start"):
            return None
        for s in SIDES:
            lo = _side_lo(rec, s)
            if 0.85 <= v.fair(s) <= 0.95 and lo is not None and lo >= 0.70:
                return self.side_if(s)
        return None


class N19CompetitiveDog(N):
    key, title = "n19-competitive-dog", "Tennis: underdog who never fell under 35c, now 45c or less"
    live_only, tennis_only = True, True
    edge = 0.005

    def pick(self, v, st):
        rec = v.market or {}
        pre = rec.get("pre")
        if pre is None or not rec.get("from_start") or _elapsed_min(v) < 30:
            return None
        s = "long" if pre < 0.5 else "short"
        lo = _side_lo(rec, s)
        return self.side_if(s) if lo is not None and lo >= 0.35 and v.fair(s) <= 0.45 else None


def _player_side(v) -> tuple[str, str] | None:
    return players(v.m.slug)


def _recent_wins(learned, player, days, now, league=None) -> int:
    rows = ((learned or {}).get("wins") or {}).get(player) or ()
    cut = (now - timedelta(days=days)).isoformat()
    return sum(1 for when, lg in rows if when >= cut and (league is None or lg == league))


class N20PlayerForm(N):
    key, title = "n20-player-form", "Tennis: a player with 2+ wins in 3 days (our records)"
    tennis_only = True
    max_entries = 1
    edge = 0.005

    def pick(self, v, st):
        pl = _player_side(v)
        if pl is None:
            return None
        w = [_recent_wins(v.learned, p, 3, v.now) for p in pl]
        if max(w) >= 2 and w[0] != w[1]:
            return self.side_if("long" if w[0] > w[1] else "short")
        return None


class N21StrongDiscount(N):
    key, title = "n21-strong-discount", "Tennis: an 80c+ favourite this week now under 60c"
    pregame_only, tennis_only = True, True
    max_entries = 1
    edge = 0.005

    def pick(self, v, st):
        pl = _player_side(v)
        best = (v.learned or {}).get("best_pre") or {}
        if pl is None:
            return None
        for s, p in zip(SIDES, pl):
            if best.get(p, 0) >= 0.80 and v.fair(s) <= 0.60:
                return self.side_if(s)
        return None


# --- Across games ---------------------------------------------------------------------

def _early(v) -> bool:
    return not v.live or _elapsed_min(v) <= 15


class N22LeagueMood(N):
    key, title = "n22-league-mood", "League mood: 60%+ upsets today, back underdogs"
    max_entries = 1
    edge = 0.005

    def pick(self, v, st):
        up, n = ((v.learned or {}).get("league_day") or {}).get(v.m.league, (0, 0))
        if n < 5 or up / n < 0.6 or not _early(v):
            return None
        return self.side_if(next((s for s in SIDES if 0.25 <= v.fair(s) <= 0.45), None))


class N23UpsetBounce(N):
    key, title = "n23-upset-bounce", "Upset bounce-back: after 3 upsets in a row, back favourites"
    max_entries = 1
    edge = 0.005

    def pick(self, v, st):
        last = ((v.learned or {}).get("league_last") or {}).get(v.m.league) or []
        if len(last) < 3 or not all(last[-3:]) or not _early(v):
            return None
        return self.side_if(next((s for s in SIDES if 0.55 <= v.fair(s) <= 0.80), None))


class N24Tired(N):
    key, title = "n24-tired", "Tennis: bet against a player who played 2h+ today"
    pregame_only, tennis_only = True, True
    max_entries = 1
    edge = 0.005

    def pick(self, v, st):
        pl, tired = _player_side(v), (v.learned or {}).get("long_today") or set()
        if pl is None or (pl[0] in tired) == (pl[1] in tired):
            return None
        return self.side_if("short" if pl[0] in tired else "long")


class N25Rematch(N):
    key, title = "n25-rematch", "Tennis rematch: back who won their last meeting"
    tennis_only = True
    max_entries = 1
    edge = 0.005

    def pick(self, v, st):
        pl = _player_side(v)
        if pl is None or not _early(v):
            return None
        today = "-".join(v.m.slug.split("-")[-3:])
        past = [w for d, w in ((v.learned or {}).get("h2h") or {}).get(tuple(sorted(pl)), ()) if d < today]
        if not past:
            return None
        return self.side_if("long" if past[-1] == pl[0] else "short")


class N26HotTournament(N):
    key, title = "n26-hot-tournament", "Tennis: 2+ wins in this league in 2 days"
    tennis_only = True
    max_entries = 1
    edge = 0.005

    def pick(self, v, st):
        pl = _player_side(v)
        if pl is None or not _early(v):
            return None
        w = [_recent_wins(v.learned, p, 2, v.now, v.m.league) for p in pl]
        if max(w) >= 2 and w[0] != w[1]:
            return self.side_if("long" if w[0] > w[1] else "short")
        return None


# --- Price path during play ---------------------------------------------------------

class N27Range(N):
    key, title = "n27-range", "Range trader: buy the bottom of a 10c+ range (10 min)"
    live_only = True

    def pick(self, v, st):
        w = _window(v.series, 600)
        if not _covers(w, 480):
            return None
        lo, hi = min(s[1] for s in w), max(s[1] for s in w)
        r = hi - lo
        if r < 0.10 - EPS:
            return None
        if v.fair_long <= lo + 0.2 * r:
            return self.side_if("long")
        if v.fair_long >= hi - 0.2 * r:
            return self.side_if("short")
        return None


class N28CalmFavourite(N):
    key, title = "n28-calm-favourite", "Calm favourite: 55-85c and flat for 20 minutes"
    live_only = True
    max_entries = 1

    def pick(self, v, st):
        w = _window(v.series, 1200)
        if not _covers(w, 1080):
            return None
        if max(s[1] for s in w) - min(s[1] for s in w) > 0.01 + EPS:
            return None
        return self.side_if(next((s for s in SIDES if 0.55 <= v.fair(s) <= 0.85), None))


class N29GameHigh(N):
    key, title = "n29-game-high", "Game high: a new high, 10c+ above the first live price"
    live_only = True

    def pick(self, v, st):
        rec = v.market or {}
        hi, lo = st.get("hi"), st.get("lo")
        st["hi"] = v.fair_long if hi is None else max(hi, v.fair_long)
        st["lo"] = v.fair_long if lo is None else min(lo, v.fair_long)
        first = rec.get("first_live")
        if not rec.get("from_start") or first is None or hi is None:
            return None
        if v.fair_long > hi + 0.005 and v.fair_long - first >= 0.10 - EPS:
            return self.side_if("long")
        if v.fair_long < lo - 0.005 and first - v.fair_long >= 0.10 - EPS:
            return self.side_if("short")
        return None


class N30SecondWind(N):
    key, title = "n30-second-wind", "Second wind: fell 20c+, then won back half"
    live_only = True
    max_entries = 1

    def pick(self, v, st):
        for s in SIDES:
            f = v.fair(s)
            pk, tr = st.get(f"pk_{s}"), st.get(f"tr_{s}")
            if pk is None or f > pk:
                st[f"pk_{s}"], st[f"tr_{s}"] = f, f
                continue
            if f < tr:
                st[f"tr_{s}"] = tr = f
            drop = pk - tr
            if drop >= 0.20 - EPS and f >= tr + drop / 2 and f < pk:
                return self.side_if(s)
        return None


class N31FakeMove(N):
    key, title = "n31-fake-move", "Fake move: a 5c+ drop fully undone within 2 minutes"
    live_only = True

    def pick(self, v, st):
        w = _window(v.series, 120)
        if len(w) < 3:
            return None
        for s in SIDES:
            f = [x[1] if s == "long" else 1 - x[1] for x in w]
            if f[0] - min(f) >= 0.05 - EPS and f[-1] >= f[0] - 0.01:
                return self.side_if(s)
        return None


# --- Timing ---------------------------------------------------------------------------

class N32KickoffCarry(N):
    key, title = "n32-kickoff-carry", "Kickoff carry: the side that drifted 3c+ in the last hour"
    max_entries = 1

    def pick(self, v, st):
        mins = _elapsed_min(v)
        if -75 <= mins <= -45:
            st.setdefault("f60", v.fair_long)
            return None
        f60 = st.get("f60")
        if not v.live or mins > 5 or f60 is None:
            return None
        d = v.fair_long - f60
        return self.side_if(_side_toward(d)) if abs(d) >= 0.03 - EPS else None


class N33SteamFade(N):
    key, title = "n33-steam-fade", "Tennis steam fade: bet against a 5c+ move in the last hour"
    pregame_only, tennis_only = True, True
    max_entries = 1
    edge = 0.005

    def pick(self, v, st):
        old = _ago(v.series, 3600)
        if old is None or not _covers(v.series, 2700):
            return None
        d = v.fair_long - old[1]
        return self.side_if(_side_toward(-d)) if abs(d) >= 0.05 - EPS else None


class N34Stamina(N):
    key, title = "n34-stamina", "Tennis 2.5h+: back the pre-match favourite at 55c or less"
    live_only, tennis_only = True, True
    max_entries = 1
    edge = 0.005

    def pick(self, v, st):
        pre = (v.market or {}).get("pre")
        if pre is None or _elapsed_min(v) < 150:
            return None
        s = "long" if pre >= 0.55 else "short" if pre <= 0.45 else None
        return self.side_if(s) if s and v.fair(s) <= 0.55 else None


class N35PreStartSqueeze(N):
    key, title = "n35-pre-start-squeeze", "Pre-start squeeze: US offer falls 2c in the last 10 min"
    pregame_only = True

    def pick(self, v, st):
        if not -10 <= _elapsed_min(v) <= 0:
            return None
        old = _ago(v.series, 300)
        if old is None:
            return None
        for s, i in (("long", 2), ("short", 3)):
            a0, a = old[i], v.ask(s)
            f0 = old[1] if s == "long" else 1 - old[1]
            if a0 is not None and a is not None and a0 - a >= 0.02 - EPS and abs(v.fair(s) - f0) < 0.01:
                return self.side_if(s)
        return None


class N36FirstTo60(N):
    key, title = "n36-first-to-60", "Tennis even match: back whoever first reaches 60c"
    live_only, tennis_only = True, True
    max_entries = 1

    def pick(self, v, st):
        rec = v.market or {}
        pre = rec.get("pre")
        if pre is None or not 0.45 <= pre <= 0.55 or not rec.get("from_start") or st.get("done"):
            return None
        for s in SIDES:
            if v.fair(s) >= 0.60 - EPS:
                st["done"] = True
                return self.side_if(s)
        return None


class Copier(Bot):
    """Acts exactly as another paper bot would, on a state of its own per copied bot.
    The copied bot's fills are passed back to it, so it sizes and caps itself."""
    kinds = ("moneyline", "spreads", "totals")
    needs_stats = True
    max_market_frac = 0.10

    def wants(self, m, live):
        return True

    def choose(self, v, st) -> str | None:
        raise NotImplementedError

    def step(self, v, st):
        key = self.choose(v, st)
        if key is None:
            return []
        from .registry import BOTS
        bot = BOTS.get(key)
        if bot is None or not bot.wants(v.m, v.live):
            return []
        st["via"] = key
        return bot.step(v, st.setdefault("sub", {}).setdefault(key, {}))

    def on_fill(self, st, f, now):
        super().on_fill(st, f, now)
        key = st.get("via")
        if key:
            from .registry import BOTS
            BOTS[key].on_fill(st.setdefault("sub", {}).setdefault(key, {}), f, now)


class N37Split(Copier):
    key, title = "n37-split", "Split: #15 x06 on ITF men's, swisstony x01 on ITF women's"

    def choose(self, v, st):
        return {"itfme": "s15-x06", "itfwo": "swisstony-x01"}.get(v.m.league)


class N38SteamChase(N):
    key, title = "n38-steam-chase", "Steam chaser: follow a 4c+ pre-game move in 30 minutes"
    pregame_only = True
    max_entries = 1

    def pick(self, v, st):
        old = _ago(v.series, 1800)
        if old is None:
            return None
        d = v.fair_long - old[1]
        return self.side_if(_side_toward(d)) if abs(d) >= 0.04 - EPS else None


class N39NewListing(N):
    key, title = "n39-new-listing", "New listing: a 1c+ gap in a market's first 30 minutes"
    edge = 0.01

    def pick(self, v, st):
        from .learn import _t
        rec, since = v.market or {}, _t((v.learned or {}).get("since"))
        seen = _t(rec.get("first_seen"))
        if not since or not seen or seen - since < timedelta(minutes=30):
            return None   # we were not watching before it appeared
        return self.side_if(best_edge_side(v)) if v.now - seen <= timedelta(minutes=30) else None


# --- Our own history -------------------------------------------------------------------

class N40Calibration(N):
    key, title = "n40-calibration", "Calibration: prices that win 5%+ more than they say"

    def pick(self, v, st):
        cal = (v.learned or {}).get("calib") or {}
        for s in SIDES:
            w, n = cal.get((v.category, band(v.fair(s))), (0, 0))
            if n >= 30 and w / n >= v.fair(s) + 0.05:
                return self.side_if(s)
        return None


class N41LeagueDog(N):
    key, title = "n41-league-dog", "League underdog map: underdogs that beat their price"

    def pick(self, v, st):
        dog = (v.learned or {}).get("dog") or {}
        for s in SIDES:
            if v.fair(s) >= 0.5:
                continue
            w, n = dog.get((v.m.league, band(v.fair(s))), (0, 0))
            if n >= 20 and w / n >= v.fair(s) + 0.05:
                return self.side_if(s)
        return None


class N42LowFee(N):
    key, title = "n42-low-fee", "Low-fee zone: 0.3c+ gaps at 15c and under or 85c and up"
    edge = 0.003

    def pick(self, v, st):
        for s in SIDES:
            a = v.ask(s)
            if a is not None and (a <= 0.15 or a >= 0.85):
                return self.side_if(s)
        return None


class N43BestPerSport(Copier):
    key, title = "n43-best-per-sport", "Copy the best bot in each sport (our records)"

    def choose(self, v, st):
        return ((v.learned or {}).get("best_by_cat") or {}).get(v.category)


class N44BestVsWorst(Copier):
    """Follow the best bot's buys, except where the worst bot would make the same buy
    now. (Opposite buys almost never happen: both bots buy only under fair.)"""
    key, title = "n44-best-vs-worst", "Follow the best bot unless the worst bot agrees"

    def choose(self, v, st):
        return (v.learned or {}).get("best")

    def step(self, v, st):
        from .registry import BOTS
        best, worst = (v.learned or {}).get("best"), (v.learned or {}).get("worst")
        if not best or not worst or best == worst:
            return []
        wb = BOTS.get(worst)
        if wb is None or not wb.wants(v.m, v.live):
            return []
        w_acts = wb.step(v, st.setdefault("worst", {}).setdefault(worst, {}))
        acts = super().step(v, st)
        w_sides = {a.side for a in w_acts if isinstance(a, Take)}
        return [a for a in acts if not (isinstance(a, Take) and a.side in w_sides)]


class N45GoodFills(S15Momentum):
    key, idea, title = "n45-good-fills", 0, "#15 x06 only in leagues where Live_ copies fill 60%+"
    jump, edge = 0.015, 0.005

    def pick(self, v, st):
        if ((v.learned or {}).get("fill_ratio") or {}).get(v.m.league, 0) < 0.6:
            return None
        return super().pick(v, st)


class N46Rounding(Bot):
    """Tennis: rest a bid on the 1c tick under fair when fair sits 0.5c+ above it.
    A maker pays no fee here, so the sliver is kept; $10 a quote, $40 held at most."""
    key, title = "n46-rounding", "Tennis rounding edge: bid the tick just under fair"
    max_market_frac = 0.04

    def wants(self, m, live):
        return m.kind == "moneyline"

    def step(self, v, st):
        if v.category not in TENNIS:
            return []
        acts = []
        for s in SIDES:
            f = v.fair(s)
            tick = int(f * 100 + 1e-9) / 100
            a = v.ask(s)
            held = self.leg(st, s)[0] * (self.leg(st, s)[1] or 0)
            if f - tick >= 0.005 - EPS and 0.05 <= tick <= 0.95 and (a is None or tick < a) and held < 40:
                d = min(10.0, self.room(v, st))
                if d >= 1:
                    acts.append(Rest(s, tick, d, "quote"))
        return acts


# --- How to buy and cash out ------------------------------------------------------------

class N47ScaleIn(S15Momentum):
    """#15 x06's signal, bought in thirds: $3.33 now, more at +3c and +6c."""
    key, idea, title = "n47-scale-in", 0, "Scale-in: #15 x06 in thirds, adding at +3c and +6c"
    jump, edge = 0.015, 0.005
    clip = 10 / 3

    def step(self, v, st):
        side, n = st.get("si_side"), st.get("si_n", 0)
        if side is None:
            acts = super().step(v, st)
            for a in acts:
                st["si_side"], st["si_f0"], st["si_n"] = a.side, v.fair(a.side), 1
            return acts
        if n >= 3 or v.held_side() != side:
            return []
        if v.fair(side) < st["si_f0"] + 0.03 * n - EPS:
            return []
        a, e = v.ask(side), v.taker_edge(side)
        if a is None or e is None or e < 0 or not self.price_floor <= a <= self.price_cap:
            return []
        st["si_n"] = n + 1
        return [Take(side, min(self.clip, self.room(v, st)), round(min(v.fair(side), self.price_cap), 4),
                     "add")]


class N48HalfLock(ProfitLock, S19TennisSetFade):
    """#19, but at +15c lock in half the held contracts and hold the rest."""
    key, idea, title = "n48-half-lock", 0, "Half lock: #19, lock half at +15c"
    rules = "s19-tennis-set-fade"

    def step(self, v, st):
        side = v.held_side()
        if side is not None and not st.get("half"):
            n, avg = self.leg(st, side)
            o = other(side)
            a = v.ask(o)
            if a is not None and v.fair(side) >= avg + 0.15 - EPS:
                if a + fees.taker_fee(1, a, v.taker_theta) <= 1 - avg - self.min_profit:
                    st["half"] = True
                    return [Take(o, abs(v.held) / 2 * a, a, "lock")]
        if st.get("half"):
            return []
        return S19TennisSetFade.step(self, v, st)


class N49ReEntry(ProfitLock, S19TennisSetFade):
    """#19 with a 70c lock-in that may buy in again after a lock."""
    key, idea, title = "n49-re-entry", 0, "Re-entry: #19 + lock at 70c, then buy again"
    rules = "s19-tennis-set-fade"
    lock_at = 0.70
    max_entries = 6

    def step(self, v, st):
        if st.get("locking") and v.held_side() is None:
            st.pop("locking")
            st.pop("peak", None)
        return super().step(v, st)


class N50EvenMoney(N):
    key, title = "n50-even-money", "Even money: 45-55c games, 1c+ gap"
    edge = 0.01

    def pick(self, v, st):
        return self.side_if(best_edge_side(v)) if 0.45 <= v.fair_long <= 0.55 else None


FIFTY_CLASSES = (
    N01Pileup, N02Snipe, N03Wall, N04SpreadSnap, N05EmptyShelf, N06DeepBook, N07Whale,
    N08VolumeSpike, N09Silence, N10Aggressive, N11OpenInterest, N12IntlWall,
    N13IntlConfidence, N14BusyAbroad, N15SpreadLag, N16ThreeWay, N17WomensDog,
    N18DominantCloser, N19CompetitiveDog, N20PlayerForm, N21StrongDiscount, N22LeagueMood,
    N23UpsetBounce, N24Tired, N25Rematch, N26HotTournament, N27Range, N28CalmFavourite,
    N29GameHigh, N30SecondWind, N31FakeMove, N32KickoffCarry, N33SteamFade, N34Stamina,
    N35PreStartSqueeze, N36FirstTo60, N37Split, N38SteamChase, N39NewListing,
    N40Calibration, N41LeagueDog, N42LowFee, N43BestPerSport, N44BestVsWorst, N45GoodFills,
    N46Rounding, N47ScaleIn, N48HalfLock, N49ReEntry, N50EvenMoney,
)
FIFTY = tuple(c() for c in FIFTY_CLASSES)
