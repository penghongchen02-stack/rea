"""Thirty Live_ clock bots: each copies one bot in the morning, one in the
afternoon and one at night (UTC), so the switch-by-time-of-day idea can be
compared combination by combination. Live_ only (fills like a real order).
Paper money, $1,000 each.

The picks are the best bots per time slot in paper results to 2026-11-19:
  morning   06-12 UTC: #15, #19, #15 x03, #15 x07, #19 x05
  afternoon 12-18 UTC: #50 ensemble, Live_19-29 (one $25 bet), Live_19-06 (2c edge)
  night     18-06 UTC: #15 x02, #15 x06, #15 x10

5 x 3 x 3 = 45 combinations; each morning bot is paired with all three afternoon
bots and two of the three night bots (a different night bot left out in turn),
giving 30.

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
NIGHT = (("s15-x02", "#15 x02"), ("s15-x06", "#15 x06"), ("s15-x10", "#15 x10"))


def slot(hour: int) -> str:
    if 6 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    return "night"


class ClockBot(Copier):
    live_sim = True
    plan: dict[str, str] = {}

    def choose(self, v, st):
        return self.plan[slot(v.now.hour)]


def _combos():
    out = []
    for i, am in enumerate(MORNING):
        nights = [n for j, n in enumerate(NIGHT) if j != i % len(NIGHT)]
        for pm in AFTERNOON:
            for nt in nights:
                out.append((am, pm, nt))
    return out


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
