"""What "Live" shows: each Live_ copy's settled result, order outcomes, and every
open bet with what it paid, its price now, P&L now, and what it wins or loses.

    python3 -m psb.livereport [--ledger paper-results/ledger.json]

Live_ copies are paper bots filled like real orders (see livesim.py); nothing
is sent anywhere.
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

from .livesim import BASE_OF, counts
from .paper import Ledger


def _who(slug: str, side: str) -> tuple[str, str]:
    parts = slug.split("-")
    a, b = (parts[2], parts[3]) if len(parts) > 3 else (slug, "")
    return (a, b) if side == "long" else (b, a)


def report(led: Ledger) -> str:
    out = ["Live_ copies: paper bots filled like real orders. No real orders.", ""]
    for key in BASE_OF:
        positions = led.positions.get(key, {})
        fills = defaultdict(list)
        for f in led.fills:
            if f["bot"] == key:
                fills[f["slug"]].append(f)
        settled = [p.cash + p.contracts * led.settled[s] for s, p in positions.items()
                   if s in led.settled and (abs(p.cash) > 1e-9 or abs(p.contracts) > 1e-9)]
        rows, open_now = [], 0.0
        for slug, p in positions.items():
            if slug in led.settled or abs(p.contracts) < 1e-6:
                continue
            side = "long" if p.contracts > 0 else "short"
            held = [f for f in fills[slug] if f["side"] == side]
            n_bought = sum(f["contracts"] for f in held)
            if not n_bought:
                continue
            per = sum(f["contracts"] * f["price"] + f["fee"] for f in held) / n_bought
            n = abs(p.contracts)
            paid = per * n
            mark = led.marks.get(slug)
            now = None if mark is None else (mark if side == "long" else 1 - mark)
            pnl = None if mark is None else p.cash + p.contracts * mark
            open_now += pnl or 0.0
            who, opp = _who(slug, side)
            rows.append((max(f["t"] for f in fills[slug]), slug.split("-")[1], who, opp, paid, per,
                         now, pnl, n - paid))
        rows.sort(reverse=True)
        c = counts(led, key)
        out.append(f"## {key} (copy of {BASE_OF[key]})")
        out.append(f"Settled: {len(settled)} matches, won {sum(x > 0 for x in settled)}, "
                   f"P&L {sum(settled):+,.2f}. Open: {len(rows)} bets, {open_now:+,.2f} now. "
                   f"Total now {sum(settled) + open_now:+,.2f}.")
        out.append(f"Orders: {c.get('orders', 0)} sent, {c.get('missed_price_gone', 0)} missed "
                   f"(price gone), {c.get('missed_competition', 0)} missed (assumed competition), "
                   f"{c.get('partial', 0)} part-filled.")
        out.append("")
        out.append("| Pick | Against | Event | Paid | Price now | P&L now | If wins | If loses |")
        out.append("|---|---|---|---|---|---|---|---|")
        for _, ev, who, opp, paid, per, now, pnl, win in rows:
            out.append(f"| {who} | {opp} | {ev} | ${paid:,.2f} at {per * 100:.0f}c "
                       f"| {'-' if now is None else f'{now * 100:.0f}c'} "
                       f"| {'waiting to settle' if pnl is None else f'{pnl:+,.2f}'} "
                       f"| {win:+,.2f} | -{paid:,.2f} |")
        out.append("")
    return "\n".join(out)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", default="paper-results/ledger.json")
    a = ap.parse_args(argv)
    with tempfile.TemporaryDirectory() as d:   # the engine rewrites the ledger every 2 minutes
        snap = Path(d) / "ledger.json"
        shutil.copyfile(a.ledger, snap)
        led = Ledger.load(snap, 1000.0)
    print(report(led))


if __name__ == "__main__":
    main()
