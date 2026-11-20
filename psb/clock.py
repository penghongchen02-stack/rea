"""Thirty Live_ clock bots: each copies one bot in the morning, one in the
afternoon and one at night (UTC), so the switch-by-time-of-day idea can be
compared combination by combination. Live_ only (fills like a real order).
Paper money, $1,000 each.

The picks are the best bots per time slot in paper results to 2026-11-19:
  morning   06-12 UTC: #15, #19, #15 x03, #15 x07, #19 x05
  afternoon 12-18 UTC: #50 ensemble, Live_19-29 (one $25 bet), Live_19-06 (2c edge)
  night     18-06 UTC: thirty different bots, one per clock bot (NIGHT below)

Each morning bot is paired with each afternoon bot twice (5 x 3 x 2 = 30), and
every clock bot has a night bot of its own, so no two clock bots trade alike.

At night (the slow tennis hours) the clock bots skip tennis and leave it to the
tennis bots: they trade the night bot's other sports only. So the night bots are
the 30 that made the most at night on other sports, by the hour of their first
buy in each settled match, in the ledger to 2026-11-20 00:44 UTC (bots that make
the same trades counted once). Until then the night bots were #15 x02, x06 and
x10, which won mostly on tennis at night and lost on the rest; the first night
(2026-11-19) was traded with those three, ten clock bots each.

    python3 -m psb.clock [--ledger paper-results/ledger.json]

prints them ranked, best total first.
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from .fifty import Copier

MORNING = (("s15-momentum", "#15"), ("s19-tennis-set-fade", "#19"), ("s15-x03", "#15 x03"),
           ("s15-x07", "#15 x07"), ("s19-x05", "#19 x05"))
AFTERNOON = (("s50-ensemble", "#50 ensemble"), ("Live_19-29", "19-29 $25 bet"),
             ("Live_19-06", "19-06 2c edge"))
# Best first; P&L at night on other sports to 2026-11-20 00:44 UTC, won/settled.
NIGHT = (
    ("s19-x10", "#19 x10"),                  # +231, 19/55
    ("s19-x05", "#19 x05"),                  # +146, 21/57
    ("gamblingisallyourneed-v2", "gambling v2"),   # +97, 54/89
    ("s19-x09", "#19 x09"),                  # +53, 5/11
    ("s39-flat-10", "#39 flat $10"),         # +47, 58/90
    ("n39-new-listing", "n39 new listing"),  # +41, 1/2
    ("s19-x06", "#19 x06"),                  # +40, 23/62
    ("Live_19-33", "19-33 all sports trail"),   # +37, 4/6
    ("s40-daily-stop", "#40 daily stop"),    # +37, 49/77
    ("n07-whale", "n07 whale"),              # +31, 6/8
    ("Live_n30-second-wind", "n30 second wind"),   # +25, 5/7
    ("n08-volume-spike", "n08 volume spike"),   # +17, 5/9
    ("s47-mean-reversion", "#47 mean reversion"),   # +15, 7/15
    ("Live_n06-deep-book", "n06 deep book"),   # +13, 2/4
    ("Live_g06-lines-maker", "g06 lines maker"),   # +12, 4/6
    ("a04-confirmed", "a04 confirmed jump"),   # +11, 2/4
    ("s01-us-intl-lag", "#1 US/intl lag"),   # +11, 35/81
    ("Live_19-32", "19-32 tennis+esports"),   # +8, 2/4
    ("underdog-1", "underdog $1"),           # +4, 3/6
    ("s03-closing-line", "#3 closing line"),   # +2, 2/2
    ("a11-same-time-pair", "a11 same-time pair"),   # +1, 1/1
    ("n02-snipe", "n02 snipe"),              # +0, 5/6
    ("Live_n13-intl-confidence", "n13 intl confidence"),   # -1, 5/9
    ("Live_a08-spread-to-winner", "a08 spread to winner"),   # -2, 1/2
    ("a06-growing-gap", "a06 growing gap"),  # -2, 5/11
    ("fav-fade", "comeback fade"),           # -4, 2/3
    ("Live_a09-calm-then-storm", "a09 calm then storm"),   # -4, 1/2
    ("s11-pregame-maker", "#11 pre-game maker"),   # -7, 10/20
    ("Live_50", "Live_50"),                  # -9, 0/2
    ("n50-even-money", "n50 even money"),    # -9, 3/7
)


def slot(hour: int) -> str:
    if 6 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    return "night"


NO_TENNIS_SLOTS = ("night",)


class ClockBot(Copier):
    live_sim = True
    plan: dict[str, str] = {}

    def choose(self, v, st):
        s = slot(v.now.hour)
        if s in NO_TENNIS_SLOTS and v.category == "tennis":
            return None
        return self.plan[s]


# Night bot by rank r goes to clock bot (r % 5) * 6 + _SPREAD[r // 5], so every
# morning bot and every afternoon bot gets a share of the best and the worst.
_SPREAD = (0, 2, 4, 1, 3, 5)


def _combos():
    days = [(am, pm) for am in MORNING for pm in AFTERNOON for _ in range(2)]
    nights = [None] * len(days)
    for r, nt in enumerate(NIGHT):
        nights[(r % len(MORNING)) * 6 + _SPREAD[r // len(MORNING)]] = nt
    return [(am, pm, nt) for (am, pm), nt in zip(days, nights)]


def _make(num: int, am, pm, nt):
    key = f"Live_clock-{num:02d}"
    title = f"{key}: AM {am[1]} / PM {pm[1]} / night {nt[1]}"
    cls = type(f"Clock{num:02d}", (ClockBot,),
               {"key": key, "title": title,
                "plan": {"morning": am[0], "afternoon": pm[0], "night": nt[0]}})
    return cls()


CLOCKS = tuple(_make(i + 1, *c) for i, c in enumerate(_combos()))


def report(led) -> str:
    from .dashboard import bot_rows
    from .livesim import counts

    titles = {b.key: b.title.split(": ", 1)[1] for b in CLOCKS}
    rows = sorted(bot_rows(led, list(titles)), key=lambda r: r["equity"], reverse=True)
    out = ["Clock bots, best total first (Live_, $1,000 each; times UTC):", "",
           "| # | Morning / afternoon / night | Total now | Settled P&L | Won/settled | Open | Orders | Missed |",
           "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        c = counts(led, r["bot"])
        out.append(f"| {r['bot'][-2:]} | {titles[r['bot']]} | {r['equity'] - led.bankroll:+,.2f} "
                   f"| {r['settled_pnl']:+,.2f} | {r['won_markets']}/{r['settled_markets']} | {r['open']} "
                   f"| {c.get('orders', 0)} | {c.get('missed', 0)} |")
    return "\n".join(out)


def main(argv=None) -> None:
    from .paper import Ledger

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", default="paper-results/ledger.json")
    a = ap.parse_args(argv)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ledger.json"
        shutil.copyfile(a.ledger, p)
        print(report(Ledger.load(p, 1000.0)))


if __name__ == "__main__":
    main()
