"""Live copies ("Live_...") of bots: the same decisions, filled the way real orders would be.

Paper bots fill instantly against the book they decided on. A real order on
Polymarket US is sent ~0.1-0.3 s later and fills only against what is still
there. A live copy (key "Live_...") therefore, for every buy:

1. sizes it under the real-money limits in live.json's defaults: at most
   ORDER_USD per order, EXPOSURE_USD at risk in total, and no new orders after
   a DAILY_LOSS_USD loss on games settled today;
2. rounds down to whole contracts, as real orders must be;
3. waits DELAY_S, then reads the US book again (another ~0.1 s);
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
from .simple_bots import S15Momentum, S19TennisSetFade

ORDER_USD = 5.0
EXPOSURE_USD = 50.0
DAILY_LOSS_USD = 25.0
DELAY_S = 0.2
COMPETITION = 0.5


def _twin(base, key: str, title: str):
    cls = type(f"LiveSim_{key.replace('-', '_')}", (base,),
               {"key": key, "title": title, "live_sim": True, "needs_stats": True,
                "rules": base().rules_key})
    return cls()


TWINS = (
    _twin(S19TennisSetFade, "Live_19", "Live_19"),
    _twin(S15Momentum, "Live_15", "Live_15"),
    _twin(SwissTonyV2, "Live_swisstony-v2", "Live_swisstony-v2"),
)
BASE_OF = {"Live_19": "s19-tennis-set-fade", "Live_15": "s15-momentum",
           "Live_swisstony-v2": "swisstony-v2"}
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


COUNTS = "_livesim"   # per-bot order outcomes, kept in the ledger's states


def counts(ledger, key: str) -> dict:
    return ledger.states.get(key, {}).get(COUNTS) or {}


def take(engine, key: str, bot, t, a, fair: float, now, st: dict) -> None:
    """Fill one Take the way a real fill-or-cancel limit order would be filled."""
    stats = engine.ledger.state(key, COUNTS)
    if (engine.bot_stats(key, now)["pnl_today"] <= -DAILY_LOSS_USD):
        stats["daily_stop"] = stats.get("daily_stop", 0) + 1
        return
    at_risk = engine.ledger.bankroll - engine.ledger.available_cash(key)
    dollars = min(a.dollars, ORDER_USD, EXPOSURE_USD - at_risk)
    n = int(math.floor(dollars / a.limit + 1e-9)) if a.limit > 0 else 0
    if n < 1:
        stats["no_room"] = stats.get("no_room", 0) + 1
        return
    stats["orders"] = stats.get("orders", 0) + 1
    time.sleep(DELAY_S)
    try:
        bk = usmarket.book(t.m.slug)   # the book as it is when the order would arrive
    except Exception as e:
        engine.log(f"livesim book {t.m.slug}: {e}")
        stats["missed"] = stats.get("missed", 0) + 1
        return
    left = n
    for px, qty in bk.side_asks(a.side):
        if px > a.limit + 1e-9 or left < 1:
            break
        k = min(left, int(math.floor(qty * COMPETITION)))
        if k < 1:
            continue
        fee = fees.taker_fee(k, px, engine.theta)
        f = engine.ledger.record(key, t.m.slug, a.side, px, k, fee, "taker", a.tag, fair, now)
        bot.on_fill(st, f, now)
        left -= k
    if left == n:
        stats["missed"] = stats.get("missed", 0) + 1
    elif left > 0:
        stats["partial"] = stats.get("partial", 0) + 1
