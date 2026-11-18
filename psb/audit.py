"""Check every paper fill against its bot's written rules, and that nothing is live.

    python3 -m psb.audit [--ledger paper-results/ledger.json]

Prints one line per broken rule (bot, market, what was expected) and a summary.
Rules checked, from bots.py and simple_bots.py:
  * a buy that opens a position is at or under the international fair value
    plus the taker fee allowance (hedges, second legs and exits may pay more,
    as their bots allow when the pair stays under $1.00);
  * the bot's price band;
  * the bot's per-market spending cap (with a cent of rounding);
  * simple bots: at most `max_entries` entries per market;
  * ferrarichampions2026-v2: one line per game and market type;
  * real money is off: live.json unarmed, no psb.live process running.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import defaultdict
from pathlib import Path

from .registry import BOTS
from .paper import Ledger

# Legs that may pay above fair by design (closing a pair or exiting).
EXEMPT_TAGS = {"hedge", "second", "terminal_exit", "reversal_exit", "rally_exit", "quote"}
SLACK = 0.005   # half a cent: book prices are on a 1c tick, fair is a midpoint


def audit(led: Ledger, repo: Path) -> tuple[list[str], dict]:
    problems: list[str] = []
    spent = defaultdict(float)          # (bot, slug) -> $ spent on opening buys
    entries = defaultdict(int)
    lines = defaultdict(set)            # (bot, event, kind) -> slugs traded
    checked = 0
    for f in led.fills:
        bot = BOTS.get(f["bot"])
        if bot is None:
            problems.append(f"{f['bot']}: fill from a bot that no longer exists ({f['slug']})")
            continue
        checked += 1
        key = (f["bot"], f["slug"])
        if f["tag"] not in EXEMPT_TAGS:
            spent[key] += f["contracts"] * f["price"]
            if f["kind"] == "taker" and f["fair"] is not None and f["price"] > f["fair"] + SLACK:
                problems.append(f"{f['bot']} {f['slug']} {f['t']}: bought {f['side']} at "
                                f"{f['price']:.2f} above fair {f['fair']:.3f} ({f['tag']})")
            lo, hi = getattr(bot, "price_floor", None), getattr(bot, "price_cap", None)
            if lo is not None and not lo - 1e-9 <= f["price"] <= hi + 1e-9:
                problems.append(f"{f['bot']} {f['slug']}: price {f['price']:.2f} outside {lo:.2f}-{hi:.2f}")
        if f["tag"] == "entry":
            entries[key] += 1
        st = led.states.get(f["bot"], {}).get(f["slug"], {})
        if st.get("event") and st.get("kind") and f["tag"] in ("dominant",):
            lines[(f["bot"], st["event"], st["kind"])].add(f["slug"])
    for (b, slug), usd in spent.items():
        cap = led.bankroll * BOTS[b].max_market_frac
        if usd > cap + 0.01 + 1e-6:
            # Ferrari-style dominant clips and gambling quotes size per order, not per market.
            if b.startswith(("ferrarichampions2026", "gamblingisallyourneed", "randomgamebet")):
                continue
            problems.append(f"{b} {slug}: spent ${usd:.2f} > per-market cap ${cap:.2f}")
    for (b, slug), n in entries.items():
        mx = getattr(BOTS[b], "max_entries", None)
        if mx is not None and n > mx:
            # One entry can fill across several price levels: count distinct times.
            times = {f["t"] for f in led.fills if f["bot"] == b and f["slug"] == slug and f["tag"] == "entry"}
            if len(times) > mx:
                problems.append(f"{b} {slug}: {len(times)} entries > {mx}")
    for (b, ev, kind), slugs in lines.items():
        if BOTS[b].one_line_per_game and len(slugs) > 1:
            problems.append(f"{b} {ev}: {len(slugs)} {kind} lines {sorted(slugs)}")
    live_cfg = repo / "live.json"
    armed = False
    if live_cfg.exists():
        try:
            armed = bool(json.loads(live_cfg.read_text()).get("armed"))
        except ValueError:
            armed = True   # unreadable: report it rather than assume it is off
    # The real-money runner is started as `python3 -m psb.live`; match only that.
    running_live = subprocess.run(["pgrep", "-f", r"-m psb\.live( |$)"],
                                  capture_output=True).returncode == 0
    if armed:
        problems.append("live.json is ARMED (real money)")
    if running_live:
        problems.append("a psb.live process is running")
    return problems, {"fills_checked": checked, "bots": len(BOTS), "live_armed": armed,
                      "live_running": running_live}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", default="paper-results/ledger.json")
    a = ap.parse_args(argv)   # run from the repo root: live.json is read from there
    led = Ledger.load(Path(a.ledger), 1000.0)
    problems, s = audit(led, Path.cwd())
    for p in problems:
        print("RULE", p)
    print(f"audit: {s['fills_checked']} fills from {s['bots']} bots checked, {len(problems)} problems; "
          f"real money {'ARMED' if s['live_armed'] else 'off'}, "
          f"live runner {'RUNNING' if s['live_running'] else 'not running'}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
