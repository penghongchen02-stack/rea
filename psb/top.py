"""What "Paper" shows: every bot ranked, best first, plus the rule audit.

    python3 -m psb.top [--ledger paper-results/ledger.json] [--n 10]

Ranked by settled P&L (money from games that paid out), then by equity (open
bets at the current US mid). Paper only: nothing here trades.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
import shutil
import tempfile
from pathlib import Path

from .audit import audit
from .registry import BOTS
from .dashboard import bot_rows
from .livesim import BASE_OF
from .livesim import counts as livesim_counts
from .paper import Ledger
from .simple_bots import IDEAS

NAMES = {i.key: f"#{i.n} {i.title}" for i in IDEAS if i.key}
NAMES.update({k: b.title for k, b in BOTS.items() if k not in NAMES and getattr(b, "title", "")})


def _row(r: dict) -> str:
    name = NAMES.get(r["bot"], r["bot"])
    won = f"{r['won_markets']}/{r['settled_markets']}" if r["settled_markets"] else "-"
    return (f"| {name} | {r['settled_pnl']:+,.2f} | {r['equity']:,.2f} | {won} "
            f"| {r['taker_fills'] + r['maker_fills']} | {r['open']} |")


def brief(rows: list[dict], last: dict | None, n: int = 5) -> str:
    """Top n by settled P&L, plus the biggest moves since the last snapshot."""
    out = ["| Bot | Settled P&L | Won/settled |", "|---|---|---|"]
    for r in rows[:n]:
        won = f"{r['won_markets']}/{r['settled_markets']}" if r["settled_markets"] else "-"
        out.append(f"| {NAMES.get(r['bot'], r['bot'])} | {r['settled_pnl']:+,.2f} | {won} |")
    if last:
        moves = [(r["settled_pnl"] - last["pnl"].get(r["bot"], 0.0), r) for r in rows]
        moves.sort(key=lambda m: abs(m[0]), reverse=True)
        big = [m for m in moves if abs(m[0]) >= 10][:3]
        settled_new = sum(r["settled_markets"] for r in rows) - last.get("settled", 0)
        out.append("")
        out.append(f"Changes since {last['at']} UTC: {settled_new} more matches settled.")
        for d, r in big:
            out.append(f"- {NAMES.get(r['bot'], r['bot'])}: {d:+,.2f} (now {r['settled_pnl']:+,.2f})")
        if not big:
            out.append("- No bot moved by $10 or more.")
    return "\n".join(out)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", default="paper-results/ledger.json")
    ap.add_argument("--n", type=int, default=10, help="rows at the top and bottom")
    ap.add_argument("--brief", metavar="SNAPSHOT",
                    help="print only the top 5 and the changes since SNAPSHOT (a JSON file this "
                         "writes after reading)")
    a = ap.parse_args(argv)
    # Read a copy: the running engine rewrites the ledger every two minutes.
    with tempfile.TemporaryDirectory() as d:
        snap = Path(d) / "ledger.json"
        shutil.copyfile(a.ledger, snap)
        led = Ledger.load(snap, 1000.0)
    rows = bot_rows(led, list(BOTS))
    rows.sort(key=lambda r: (r["settled_pnl"], r["equity"]), reverse=True)
    if a.brief:
        snap_path = Path(a.brief)
        last = json.loads(snap_path.read_text()) if snap_path.exists() else None
        print(brief(rows, last))
        snap_path.write_text(json.dumps({
            "at": datetime.now(timezone.utc).strftime("%H:%M"),
            "settled": sum(r["settled_markets"] for r in rows),
            "pnl": {r["bot"]: r["settled_pnl"] for r in rows}}))
        return
    traded = [r for r in rows if r["taker_fills"] + r["maker_fills"] > 0]
    idle = [r for r in rows if r["taker_fills"] + r["maker_fills"] == 0]
    head = ("| Bot | Settled P&L | Equity | Won/settled | Bets | Open |\n"
            "|---|---|---|---|---|---|")
    print(f"Paper results, {len(BOTS)} bots, $1,000 each. No real orders.\n")
    print(f"Top {min(a.n, len(traded))}:\n{head}")
    for r in traded[:a.n]:
        print(_row(r))
    if len(traded) > a.n:
        print(f"\nBottom {min(a.n, len(traded) - a.n)}:\n{head}")
        for r in traded[-min(a.n, len(traded) - a.n):]:
            print(_row(r))
    if idle:
        print(f"\nNo bets yet ({len(idle)}): " + ", ".join(NAMES.get(r["bot"], r["bot"]) for r in idle))
    by_key = {r["bot"]: r for r in rows}
    pairs = [(base, twin) for twin, base in BASE_OF.items() if twin in by_key and base in by_key]
    if pairs:
        print("\nPaper vs Live_ copies (same decisions and bet sizes; Live_ buys at once and fills ~0.1 s later,"
              " whole contracts, half the shown size):")
        print("| Bot | Paper settled | Live_ settled | Live_ equity | Orders sent | Missed: price gone "
              "| Missed: competition | Part-filled |\n|---|---|---|---|---|---|---|---|")
        for base, twin in pairs:
            b, w = by_key[base], by_key[twin]
            c = livesim_counts(led, twin)
            print(f"| {NAMES.get(base, base)} | {b['settled_pnl']:+,.2f} | {w['settled_pnl']:+,.2f} "
                  f"| {w['equity']:,.2f} | {c.get('orders', 0)} | {c.get('missed_price_gone', 0)} "
                  f"| {c.get('missed_competition', 0)} | {c.get('partial', 0)} |")
    problems, s = audit(led, Path.cwd())
    print(f"\nRule check: {s['fills_checked']} bets checked, {len(problems)} problems; "
          f"real money {'ARMED' if s['live_armed'] else 'off'}.")
    for p in problems[:5]:
        print(f"- {p}")
    if s["removed_bot_fills"]:
        print("Not checked, from removed bots: " + ", ".join(
            f"{k} ({n} bets)" for k, n in sorted(s["removed_bot_fills"].items())))


if __name__ == "__main__":
    main()
