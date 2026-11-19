"""The top 10 Live_ bots, each placing new bets only in its own best hours.

Each copies one Live_ bot exactly, but outside its window (UTC) it makes no new
buys and waits for the window to open again; bets it already holds ride to the
end, and exits (locks, trailing stops) still run. Paper money, $1,000 each.

Windows are the run of hours whose matches made the bot the most money, by the
hour of its first buy in each settled match, in the ledger to 2026-11-19 18:28 UTC
(about one day of data, so they may change as more results come in):

    python3 -m psb.hours [--ledger paper-results/ledger.json]

prints them ranked, best total first, next to the bot they copy.
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from .fifty import Copier

# copied Live_ bot: (first hour, end hour) UTC; new bets from first:00 to end:00.
WINDOWS: dict[str, tuple[int, int]] = {
    "Live_19-29": (10, 14),   # +778 in these hours vs +643 over all hours
    "Live_19-08": (11, 17),   # +556 vs +514
    "Live_19-30": (10, 14),   # +542 vs +437
    "Live_19-06": (11, 16),   # +585 vs +501
    "Live_19-34": (11, 17),   # +531 vs +489
    "Live_19-03": (10, 16),   # +532 vs +437
    "Live_19-33": (10, 16),   # +508 vs +438
    "Live_19-39": (11, 18),   # +524 vs +462
    "Live_19-25": (10, 16),   # +505 vs +392
    "Live_19-00": (10, 16),   # +491 vs +378
}


def in_window(hour: int, window: tuple[int, int]) -> bool:
    a, b = window
    return a <= hour < b if a < b else hour >= a or hour < b


class HoursBot(Copier):
    live_sim = True
    target: str = ""
    window: tuple[int, int] = (0, 24)

    def choose(self, v, st):
        return self.target

    def step(self, v, st):
        acts = super().step(v, st)
        if in_window(v.now.hour, self.window):
            return acts
        return [a for a in acts if getattr(a, "tag", "") != "entry"]


def _make(target: str, window: tuple[int, int]):
    key = f"Live_hrs-{target.removeprefix('Live_')}"
    title = f"{key}: {target}, new bets {window[0]:02d}:00-{window[1]:02d}:00 UTC only"
    cls = type(f"Hrs_{target.replace('-', '_')}", (HoursBot,),
               {"key": key, "title": title, "target": target, "window": window})
    return cls()


HOURS = tuple(_make(t, w) for t, w in WINDOWS.items())


def report(led) -> str:
    from .dashboard import bot_rows

    keys = [b.key for b in HOURS] + [b.target for b in HOURS]
    rows = {r["bot"]: r for r in bot_rows(led, keys)}
    out = ["Best-hours bots vs the bot they copy (Live_, $1,000 each; times UTC):", "",
           "| Bot | New bets | Total now | Settled | Won/settled | Open | Copied bot total |",
           "|---|---|---|---|---|---|---|"]
    for b in sorted(HOURS, key=lambda b: rows[b.key]["equity"], reverse=True):
        r, t = rows[b.key], rows[b.target]
        out.append(f"| {b.key} | {b.window[0]:02d}-{b.window[1]:02d} | {r['equity'] - led.bankroll:+,.2f} "
                   f"| {r['settled_pnl']:+,.2f} | {r['won_markets']}/{r['settled_markets']} | {r['open']} "
                   f"| {t['equity'] - led.bankroll:+,.2f} |")
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
