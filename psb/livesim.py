"""Live copies ("Live_...") of bots: the same decisions, filled the way real orders would be.

Paper bots fill instantly against the book they decided on. A real order on
Polymarket US is sent ~0.1-0.3 s later and fills only against what is still
there. A live copy (key "Live_...") therefore, for every buy:

1. sizes it exactly as its paper base does (same dollars per bet and per
   match), so paper and live results compare dollar for dollar;
2. rounds down to whole contracts, as real orders must be;
3. sends at once: the US book is read again right away, and that read's own
   ~0.1 s stands in for the order's trip to the exchange;
4. fills like a fill-or-cancel limit order against that new book, taking only
   COMPETITION of each level's size (other traders see the same price). That
   share is an assumption, not a measurement.

Nothing is sent anywhere: this is still paper trading, made harsher.
"""
from __future__ import annotations

import math
import time

from . import fees, usmarket
from .bots import SwissTonyV2
from .exits import S19Lock70
from .simple_bots import S01Lag, S15Momentum, S19TennisSetFade, S50Ensemble
from .allsports import ALL_CLASSES
from .fifty import FIFTY_CLASSES
from .gaps import GAP_CLASSES
from .ideas import IDEA_CLASSES
from .tennis import TENNIS_BOTS, TnPre1, TnPre2
from .underdog import UD_CLASSES, Underdog
from .variants import VARIANTS

DELAY_S = 0.0   # no added wait: buy as soon as the price looks good
COMPETITION = 0.5
# Copies that order the same market within this many seconds share one fresh
# read of its book, as orders sent together would arrive together; otherwise
# dozens of copies acting on one price move would each wait for their own read.
SHARE_READ_S = 1.0


def _arrival_book(engine, slug: str):
    """The book an order sent now would meet: read once per SHARE_READ_S per market."""
    cache = engine.__dict__.setdefault("_livesim_books", {})
    hit = cache.get(slug)
    mono = time.monotonic()
    if hit is not None and mono - hit[0] < SHARE_READ_S:
        return hit[1]
    bk = usmarket.book(slug, lane=getattr(engine, "lane", "main"))
    cache[slug] = (mono, bk)
    return bk


def _twin(base, key: str, title: str):
    cls = type(f"LiveSim_{key.replace('-', '_')}", (base,),
               {"key": key, "title": title, "live_sim": True, "needs_stats": True,
                "rules": base().rules_key})
    return cls()


_VARIANT = {type(b)().key: type(b) for b in VARIANTS}

TWINS = (
    _twin(S19TennisSetFade, "Live_19", "Live_19"),
    _twin(S15Momentum, "Live_15", "Live_15"),
    _twin(SwissTonyV2, "Live_swisstony-v2", "Live_swisstony-v2"),
    _twin(S19Lock70, "Live_19-lock70", "Live_19-lock70"),
    _twin(_VARIANT["s15-x06"], "Live_15-x06", "Live_15-x06"),
    _twin(_VARIANT["swisstony-x01"], "Live_swisstony-x01", "Live_swisstony-x01"),
    _twin(Underdog, "Live_underdog", "Live_underdog"),
    _twin(S50Ensemble, "Live_50", "Live_50"),
    _twin(S01Lag, "Live_01", "Live_01"),   # added 2026-11-22 at the user's request
    _twin(TnPre1, "Live_tn-pre-1c", "Live_tn-pre-1c"),
    _twin(TnPre2, "Live_tn-pre-2c", "Live_tn-pre-2c"),
    *(_twin(c, f"Live_{c.key}", f"Live_{c.key}") for c in IDEA_CLASSES),
    *(_twin(type(b), f"Live_{b.key}", f"Live_{b.key}") for b in TENNIS_BOTS
      if b.key.startswith("tn-maker")),
    *(_twin(c, f"Live_{c.key}", f"Live_{c.key}") for c in FIFTY_CLASSES),
    *(_twin(c, f"Live_{c.key}", f"Live_{c.key}") for c in GAP_CLASSES),
    *(_twin(c, f"Live_{c.key}", f"Live_{c.key}") for c in ALL_CLASSES),
    *(_twin(c, f"Live_{c.key}", f"Live_{c.key}") for c in UD_CLASSES),
)
# Live_ copies paused on 2026-11-19 at 14:06 UTC, the weakest by total then: each
# Live_ order re-reads the US book, and with 144 copies those reads slowed every
# bot's checks (a live game was read every ~18 s instead of ~10 s). Their paper
# bots keep running; delete a key here to bring its copy back.
PAUSED = frozenset({
    *(f"Live_19-{n}" for n in ("24", "26", "38", "23", "14", "07", "09", "15", "40", "01", "18",
                               "27", "35", "02", "28", "20", "22", "19", "21", "37", "36", "17",
                               "04", "13", "16")),
    "Live_swisstony-v2", "Live_swisstony-x01", "Live_n42-low-fee", "Live_n10-aggressive",
    "Live_n29-game-high", "Live_n47-scale-in", "Live_n14-busy-abroad", "Live_a07-price-step",
    "Live_n12-intl-wall", "Live_g12-clock", "Live_g09-take-profit", "Live_n08-volume-spike",
    "Live_n49-re-entry", "Live_n48-half-lock",
})
TWINS = tuple(b for b in TWINS if b.key not in PAUSED)
# Added 2026-11-22 at the user's request: Live_50 with one $25 bet per match
# instead of up to three $10 bets. Not in BASE_OF: its sizes differ from the
# paper #50, so it is compared with Live_50 instead.
_LIVE50_25 = type("LiveSim_Live_50_25", (type(next(b for b in TWINS if b.key == "Live_50")),),
                  {"key": "Live_50-25", "title": "Live_50-25: Live_50 with 1 bet of $25 per match",
                   "clip": 25.0, "max_entries": 1, "max_market_frac": 0.025})
TWINS = TWINS + (_LIVE50_25(),)
BASE_OF = {"Live_19": "s19-tennis-set-fade", "Live_15": "s15-momentum",
           "Live_swisstony-v2": "swisstony-v2", "Live_19-lock70": "s19-lock70",
           "Live_15-x06": "s15-x06", "Live_swisstony-x01": "swisstony-x01",
           "Live_underdog": "underdog-1", "Live_50": "s50-ensemble", "Live_01": "s01-us-intl-lag",
           "Live_tn-pre-1c": "tn-pre-1c", "Live_tn-pre-2c": "tn-pre-2c",
           **{f"Live_{c.key}": c.key for c in IDEA_CLASSES},
           **{f"Live_{b.key}": b.key for b in TENNIS_BOTS if b.key.startswith("tn-maker")},
           **{f"Live_{c.key}": c.key for c in FIFTY_CLASSES},
           **{f"Live_{c.key}": c.key for c in GAP_CLASSES},
           **{f"Live_{c.key}": c.key for c in ALL_CLASSES},
           **{f"Live_{c.key}": c.key for c in UD_CLASSES}}
BASE_OF = {k: v for k, v in BASE_OF.items() if k not in PAUSED}
# Keys these copies had before 2026-11-18 17:50 UTC (see migrate_keys).
OLD_KEYS = {"s19-livesim": "Live_19", "s15-livesim": "Live_15",
            "swisstony-v2-livesim": "Live_swisstony-v2"}


def migrate_keys(ledger) -> int:
    """Move history recorded under OLD_KEYS to the current keys; returns fills moved."""
    moved = 0
    for old, new in OLD_KEYS.items():
        for table in (ledger.positions, ledger.states):
            if old in table:
                table.setdefault(new, {}).update(table.pop(old))
        if old in ledger.bot_started:
            ledger.bot_started.setdefault(new, ledger.bot_started.pop(old))
        for f in ledger.fills:
            if f["bot"] == old:
                f["bot"] = new
                moved += 1
    return moved


def queue_share(contracts: float) -> float:
    """Whole contracts a resting order gets of `contracts` that traded at or
    through its price: other bids rest at the same price, so only COMPETITION
    of it."""
    return float(math.floor(contracts * COMPETITION + 1e-9))


COUNTS = "_livesim"   # per-bot order outcomes, kept in the ledger's states


def counts(ledger, key: str) -> dict:
    return ledger.states.get(key, {}).get(COUNTS) or {}


def take(engine, key: str, bot, t, a, fair: float, now, st: dict) -> None:
    """Fill one Take the way a real fill-or-cancel limit order would be filled."""
    stats = engine.ledger.state(key, COUNTS)
    dollars = min(a.dollars, engine.ledger.available_cash(key))
    n = int(math.floor(dollars / a.limit + 1e-9)) if a.limit > 0 else 0
    if n < 1:
        stats["no_room"] = stats.get("no_room", 0) + 1
        return
    stats["orders"] = stats.get("orders", 0) + 1
    hour = stats.setdefault("by_hour", {}).setdefault(str(now.hour), [0, 0])   # [orders, missed]
    hour[0] += 1
    if DELAY_S:
        time.sleep(DELAY_S)
    try:
        bk = _arrival_book(engine, t.m.slug)   # the book as it is when the order would arrive
    except Exception as e:
        engine.log(f"livesim book {t.m.slug}: {e}")
        stats["missed"] = stats.get("missed", 0) + 1
        return
    left = n
    shown = 0.0   # contracts still offered at or under the limit when the order arrives
    for px, qty in bk.side_asks(a.side):
        if px > a.limit + 1e-9 or left < 1:
            break
        shown += qty
        k = min(left, int(math.floor(qty * COMPETITION)))
        if k < 1:
            continue
        fee = fees.taker_fee(k, px, engine.theta)
        f = engine.ledger.record(key, t.m.slug, a.side, px, k, fee, "taker", a.tag, fair, now)
        bot.on_fill(st, f, now)
        left -= k
    if left == n:
        stats["missed"] = stats.get("missed", 0) + 1
        hour[1] += 1
        # Why: the price was gone, or only the assumed competition took what was left.
        why = "missed_price_gone" if shown < 1e-9 else "missed_competition"
        stats[why] = stats.get(why, 0) + 1
    elif left > 0:
        stats["partial"] = stats.get("partial", 0) + 1
