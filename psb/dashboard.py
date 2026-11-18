"""Export the paper ledger as the three data files the dashboard reads.

    python3 -m psb.dashboard [--ledger paper-results/ledger.json] [--out paper-results/dashboard]

bots.json     one row per bot: totals as in the report
games.json    one row per bot and settled market: P&L and when it settled
fills.json    the most recent fills, with the fair value each was judged against
Each file is {"rows": [...], "meta": [...]}; meta carries this script so a
reader can see how every number was made. Paper only: nothing here trades.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .bots import BOTS, FIXED
from .markets import category_of
from .paper import Ledger, _entry_long_px, paired_cost

RECENT_FILLS = 300
FIXED_KEYS = {b.key for b in FIXED}


def _family(key: str) -> str:
    return key[:-3] if key.endswith("-v2") else key


def _last_fill_times(led: Ledger) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for f in led.fills:
        out[(f["bot"], f["slug"])] = max(out.get((f["bot"], f["slug"]), ""), f["t"])
    return out


def bot_rows(led: Ledger) -> list[dict]:
    rows = []
    for key in BOTS:
        fills = [f for f in led.fills if f["bot"] == key]
        taker = sum(f["kind"] == "taker" for f in fills)
        settled_pnl = fees_paid = 0.0
        equity = led.bankroll
        open_n = traded = settled_n = wins = 0
        for slug, p in led.positions.get(key, {}).items():
            if abs(p.cash) < 1e-9 and abs(p.contracts) < 1e-9:
                continue
            traded += 1
            fees_paid += p.fees
            if slug in led.settled:
                v = p.cash + p.contracts * led.settled[slug]
                settled_pnl += v
                equity += v
                settled_n += 1
                wins += v > 0
            else:
                open_n += abs(p.contracts) > 1e-9
                px = led.marks.get(slug)
                if px is None:
                    px = _entry_long_px(led, key, slug, p.contracts)
                equity += p.cash + p.contracts * px
        pcs = [pc for pc in (paired_cost(st) for st in led.states.get(key, {}).values()) if pc]
        rows.append({
            "bot": key, "family": _family(key),
            "version": "v2" if key in FIXED_KEYS else "original",
            # Ledgers from before per-bot start times: only the originals existed.
            "since": led.bot_started.get(key) or (None if key in FIXED_KEYS else led.started_at),
            "settled_pnl": round(settled_pnl, 2), "equity": round(equity, 2),
            "fees": round(fees_paid, 2), "taker_fills": taker, "maker_fills": len(fills) - taker,
            "markets": traded, "open": open_n, "settled_markets": settled_n, "won_markets": wins,
            "cash_free": round(led.available_cash(key), 2),
            "paired_markets": len(pcs),
            "avg_pair_cost": round(sum(pcs) / len(pcs), 4) if pcs else None,
        })
    return rows


def game_rows(led: Ledger) -> list[dict]:
    last = _last_fill_times(led)
    rows = []
    for key, ms in led.positions.items():
        for slug, p in ms.items():
            if slug not in led.settled or (abs(p.cash) < 1e-9 and abs(p.contracts) < 1e-9):
                continue
            league = slug.split("-")[1] if slug.count("-") > 1 else ""
            rows.append({
                "bot": key, "family": _family(key),
                "version": "v2" if key in FIXED_KEYS else "original",
                "market": slug, "sport": category_of(slug.split("-", 1)[1]) if "-" in slug else "other",
                "league": league,
                # Settlement times are recorded from 2026-11-18; before that the
                # market's last fill stands in (a game settles a few hours later).
                "settled_at": led.settled_at.get(slug) or last.get((key, slug), led.started_at),
                "pnl": round(p.cash + p.contracts * led.settled[slug], 2),
                "fees": round(p.fees, 2),
            })
    rows.sort(key=lambda r: (r["settled_at"], r["bot"], r["market"]))
    return rows


def fill_rows(led: Ledger) -> list[dict]:
    rows = []
    start = max(0, len(led.fills) - RECENT_FILLS)
    for i, f in enumerate(led.fills[start:], start):
        rows.append({
            "id": i, "t": f["t"], "bot": f["bot"], "market": f["slug"], "side": f["side"],
            "price": f["price"], "fair": f["fair"],
            "edge": None if f["fair"] is None else round(f["fair"] - f["price"], 4),
            "contracts": f["contracts"], "dollars": round(f["contracts"] * f["price"], 2),
            "fee": f["fee"], "how": f["kind"], "tag": f["tag"],
        })
    rows.reverse()   # newest first
    return rows


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", default="paper-results/ledger.json")
    ap.add_argument("--out", default="paper-results/dashboard")
    a = ap.parse_args(argv)
    led = Ledger.load(Path(a.ledger), 1000.0)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    code = Path(__file__).read_text()
    meta = [{"label": "dashboard.py", "value": code, "type": "code", "lang": "python"},
            {"label": "Input", "value": "paper-results/ledger.json (paper only, no real orders)"},
            {"label": "Paper bankroll per bot", "value": f"${led.bankroll:,.0f}"}]
    for name, rows in (("bots", bot_rows(led)), ("games", game_rows(led)), ("fills", fill_rows(led))):
        (out / f"{name}.json").write_text(json.dumps({"rows": rows, "meta": meta}))
        print(f"{out / name}.json: {len(rows)} rows")


if __name__ == "__main__":
    main()
