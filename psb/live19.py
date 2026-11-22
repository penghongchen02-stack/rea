"""Forty Live_19 versions, plus a control, to find the best way to run #19.

Each is a Live_ copy (livesim.py: fills like a real order, whole contracts,
half the shown size) of #19 tennis set-winner fade with only the named
settings changed. All start together, so they see the same matches from the
same first price; "Live_19-00" is plain Live_19 started with them, the fair
baseline. Paper only, $1,000 each.

    python3 -m psb.live19 [--ledger paper-results/ledger.json]

prints them ranked, best total first.
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from .exits import ProfitLock
from .simple_bots import EPS, S19TennisSetFade
from .variants import TENNIS_ESPORTS, _AllSports

TRAIL = {"trail_rise": 0.25, "trail_drop": 0.10}

# (number, short description, settings). Unnamed settings are #19's:
# 15c drop from the first price, 1c edge, tennis only, 3 entries of $10,
# 5-95c, held to the end.
SPECS: list[tuple[int, str, dict]] = [
    (0, "control: same as Live_19", {}),
    # When to buy
    (1, "10c drop", {"drop": 0.10}),
    (2, "12c drop", {"drop": 0.12}),
    (3, "18c drop", {"drop": 0.18}),
    (4, "20c drop", {"drop": 0.20}),
    (5, "0.5c edge", {"edge": 0.005}),
    (6, "2c edge", {"edge": 0.02}),
    (7, "3c edge", {"edge": 0.03}),
    (8, "20c drop, 2c edge", {"drop": 0.20, "edge": 0.02}),
    # Lock in a profit
    (9, "lock at 60c", {"lock_at": 0.60}),
    (10, "lock at 70c", {"lock_at": 0.70}),
    (11, "lock at 80c", {"lock_at": 0.80}),
    (12, "lock at 90c", {"lock_at": 0.90}),
    (13, "trail: 25c up, 10c off peak", dict(TRAIL)),
    (14, "trail: 15c up, 8c off peak", {"trail_rise": 0.15, "trail_drop": 0.08}),
    (15, "trail: 20c up, 5c off peak", {"trail_rise": 0.20, "trail_drop": 0.05}),
    (16, "trail: 30c up, 10c off peak", {"trail_rise": 0.30, "trail_drop": 0.10}),
    (17, "trail: 25c up, 15c off peak", {"trail_rise": 0.25, "trail_drop": 0.15}),
    # Cut a loss
    (18, "stop 10c under cost", {"stop_loss": 0.10}),
    (19, "stop 15c under cost", {"stop_loss": 0.15}),
    (20, "stop 20c under cost", {"stop_loss": 0.20}),
    (21, "trail 25/10 + stop 15c", {**TRAIL, "stop_loss": 0.15}),
    (22, "lock at 70c + stop 15c", {"lock_at": 0.70, "stop_loss": 0.15}),
    # Which prices
    (23, "only 15c and up", {"price_floor": 0.15}),
    (24, "only 25c and up", {"price_floor": 0.25}),
    (25, "only 75c and under", {"price_cap": 0.75}),
    (26, "15c and up + trail 25/10", {"price_floor": 0.15, **TRAIL}),
    # Bet sizes
    (27, "6 bets of $5", {"max_entries": 6, "clip": 5.0}),
    (28, "1 bet of $10", {"max_entries": 1}),
    (29, "1 bet of $25", {"max_entries": 1, "clip": 25.0}),
    (30, "2 bets of $15", {"max_entries": 2, "clip": 15.0}),
    # Which sports
    (31, "all sports", {"all_sports": True}),
    (32, "tennis + esports", {"cats": TENNIS_ESPORTS}),
    (33, "all sports + trail 25/10", {"all_sports": True, **TRAIL}),
    (34, "all sports, 20c drop, 2c edge", {"all_sports": True, "drop": 0.20, "edge": 0.02}),
    # Combinations
    (35, "12c drop + trail 25/10", {"drop": 0.12, **TRAIL}),
    (36, "18c drop, 2c edge + trail 25/10", {"drop": 0.18, "edge": 0.02, **TRAIL}),
    (37, "20c drop + lock at 70c", {"drop": 0.20, "lock_at": 0.70}),
    (38, "12c drop, 15c and up + trail 25/10", {"drop": 0.12, "price_floor": 0.15, **TRAIL}),
    (39, "2c edge + trail 25/10 + stop 15c", {"edge": 0.02, **TRAIL, "stop_loss": 0.15}),
    (40, "12c drop, 0.5c edge, 15c and up, lock at 75c",
     {"drop": 0.12, "edge": 0.005, "price_floor": 0.15, "lock_at": 0.75}),
    # One bet per match like 29, sized by the money the bot has (added 2026-11-19)
    (41, "1 bet of 2.5% of its cash", {"max_entries": 1, "cash_frac": 0.025, "max_market_frac": 0.10}),
    (42, "1 bet of 5% of its cash", {"max_entries": 1, "cash_frac": 0.05, "max_market_frac": 0.10}),
    (43, "1 bet of 80 contracts", {"max_entries": 1, "contracts": 80, "max_market_frac": 0.10}),
    # Was 10% of its cash (19:45 UTC 2026-11-19 to 03:30 UTC 2026-11-20); the user
    # set it to one $25 bet, like 29, keeping the guard.
    (44, "1 bet of $25 + cash out at half of a $100+ gain",
     {"max_entries": 1, "clip": 25.0, "guard_at": 100.0, "guard_keep": 0.5}),
]

_EXIT_SETTINGS = {"lock_at", "trail_rise", "trail_drop", "stop_loss", "guard_at"}
_SIZE_SETTINGS = {"cash_frac", "contracts"}


class _SizeByMoney:
    """Bet a share of the cash the bot has now, or a fixed number of contracts."""
    cash_frac: float | None = None
    contracts: int | None = None

    def size(self, v, st, side):
        if self.cash_frac is not None:
            return self.cash_frac * v.cash
        if self.contracts is not None:
            return self.contracts * v.ask(side)
        return super().size(v, st, side)


def _make(num: int, desc: str, settings: dict):
    settings = dict(settings)
    bases: tuple = (S19TennisSetFade,)
    if settings.pop("all_sports", False):
        bases = (_AllSports,) + bases
        settings["cats"] = None
    if _EXIT_SETTINGS & settings.keys():
        bases = (ProfitLock,) + bases
    if _SIZE_SETTINGS & settings.keys():
        bases = (_SizeByMoney,) + bases
    key = f"Live_19-{num:02d}"
    cls = type(f"Live19_{num:02d}", bases,
               {"key": key, "title": f"{key}: {desc}", "live_sim": True, "needs_stats": True,
                "rules": S19TennisSetFade().rules_key, **settings})
    return cls()


def _active(bot) -> bool:
    from .livesim import PAUSED   # late import: livesim imports this module's neighbours
    return bot.key not in PAUSED


LIVE19 = tuple(b for b in (_make(*s) for s in SPECS) if _active(b))


def _fast(num: int):
    """A copy of Live_19-<num> that also uses the engine's fast lane: the same
    rules and sizes, checked about every second instead of every poll round."""
    base = next(b for b in LIVE19 if b.key == f"Live_19-{num:02d}")
    desc = base.title.split(": ", 1)[1]
    cls = type(f"{type(base).__name__}_Fast", (type(base),),
               {"key": f"{base.key}-fast", "title": f"{base.key}-fast: {desc}, fast lane",
                "fast_lane": True})
    return cls()


class _SmallerInBand:
    """Bet band_clip instead of the usual size when the ask is in [lo, hi)."""
    band: tuple[float, float] = (0.60, 0.70)
    band_clip = 15.0

    def size(self, v, st, side):
        a = v.ask(side)
        lo, hi = self.band
        if a is not None and lo - EPS <= a < hi - EPS:
            return self.band_clip
        return super().size(v, st, side)


def _smaller_60s(num: int):
    """A copy of Live_19-<num> that bets $15 when the price is 60-69c; all else the same."""
    base = next(b for b in LIVE19 if b.key == f"Live_19-{num:02d}")
    desc = base.title.split(": ", 1)[1]
    cls = type(f"{type(base).__name__}_15at60", (_SmallerInBand, type(base)),
               {"key": f"{base.key}-15at60", "title": f"{base.key}-15at60: {desc}, $15 at 60-70c"})
    return cls()


# Added 2026-11-21 to test faster checks against Live_19-29 on the same matches.
# Added 2026-11-22: $15 instead of $25 on 60-70c bets, at the user's request.
LIVE19 = LIVE19 + (_fast(29), _smaller_60s(29))


def report(led) -> str:
    from .dashboard import bot_rows
    from .livesim import counts

    rows = {r["bot"]: r for r in bot_rows(led, [b.key for b in LIVE19])}
    out = ["Live_19 versions, best total first (paper, filled like real orders, $1,000 each):", "",
           "| # | Version | Total now | Settled P&L | Won/settled | Open bets | Orders | Missed |",
           "|---|---|---|---|---|---|---|---|"]
    ranked = sorted((rows[b.key] for b in LIVE19 if b.key in rows),
                    key=lambda r: r["equity"], reverse=True)
    for r in ranked:
        c = counts(led, r["bot"])
        title = next(b.title for b in LIVE19 if b.key == r["bot"]).split(": ", 1)[1]
        out.append(f"| {r['bot'].removeprefix('Live_19-')} | {title} | {r['equity'] - led.bankroll:+,.2f} "
                   f"| {r['settled_pnl']:+,.2f} | {r['won_markets']}/{r['settled_markets']} | {r['open']} "
                   f"| {c.get('orders', 0)} | {c.get('missed', 0)} |")
    return "\n".join(out)


def main(argv=None) -> None:
    from .paper import Ledger

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", default="paper-results/ledger.json")
    a = ap.parse_args(argv)
    with tempfile.TemporaryDirectory() as d:   # read a copy: a run may be saving it
        p = Path(d) / "ledger.json"
        shutil.copyfile(a.ledger, p)
        print(report(Ledger.load(p, 1000.0)))


if __name__ == "__main__":
    main()
