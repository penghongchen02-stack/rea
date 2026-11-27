"""Export the paper ledger as the three data files the dashboard reads.

    python3 -m psb.dashboard [--ledger paper-results/ledger.json] [--out paper-results/dashboard]

bots.json     one row per original and v2 bot: totals as in the report
all-bots.json one row per bot in the registry, running or taken off, with its group
bot-info.json what each bot is: title, what it does, markets it trades
holdings.json what each bot holds now in unsettled markets, and its resting orders
bot-fills.json the latest 40 fills of every bot
games.json    one row per bot and settled market: P&L and when it settled
fills.json    the most recent fills, with the fair value each was judged against
ideas.json    the 50 simple bot ideas: status, rule, and totals for those that trade

    python3 -m psb.dashboard --live live-results [--config live.json]

live.json         the live runner, one row per mode (real money, shadow): bot, limits,
                  last status, exposure and results
live-orders.json  the latest orders it sent (real) or would have sent (shadow)
Each file is {"rows": [...], "meta": [...]}; meta carries this script so a
reader can see how every number was made. Nothing here trades.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from . import registry
from .bots import FIXED, ORIGINALS
from .registry import ALL_BOTS, BOTS
from .simple_bots import IDEAS
from .markets import category_of
from .paper import Ledger, _entry_long_px, paired_cost

RECENT_FILLS = 300
FIXED_KEYS = {b.key for b in FIXED}
IDEA_KEYS = {i.key for i in IDEAS if i.key}


# Which registry group each bot comes from, as a short label for the dashboard.
GROUPS = (("ORIGINALS", "Copied wallets"), ("FIXED", "Copied wallets"), ("RUNNING", "Simple ideas (s01-s50)"),
          ("VARIANTS", "Variants of #19, #15, swisstony"), ("EXITS", "#19 profit locks"),
          ("LIVE19", "Live_19 versions"), ("CLOCKS", "Live_ clock bots"), ("HOURS", "Live_ best-hours bots"),
          ("TWINS", "Live_ copies"), ("UNDERDOG", "Underdog"), ("UD_VERSIONS", "Underdog"),
          ("TENNIS_BOTS", "Tennis"), ("IDEAS6", "Six more ideas"), ("FIFTY", "Fifty more (n01-n50)"),
          ("GAPS", "Gaps (g01-g12)"), ("ALL15", "All-sports (a01-a15)"), ("KCH", "kch123 report"))
GROUP_OF: dict[str, str] = {}
for _name, _label in GROUPS:
    for _b in getattr(registry, _name):
        GROUP_OF.setdefault(_b.key, _label)


def _family(key: str) -> str:
    return key[:-3] if key in FIXED_KEYS else key


def _last_fill_times(led: Ledger) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for f in led.fills:
        out[(f["bot"], f["slug"])] = max(out.get((f["bot"], f["slug"]), ""), f["t"])
    return out


def bot_rows(led: Ledger, keys=None) -> list[dict]:
    rows = []
    for key in keys or [b.key for b in ORIGINALS + FIXED]:
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
            "group": GROUP_OF.get(key, "Other"), "running": key in BOTS,
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


def all_bot_rows(led: Ledger) -> list[dict]:
    """Every bot the paper run has: the running ones first, then those taken off."""
    keys = list(BOTS) + [k for k in ALL_BOTS if k not in BOTS]
    return [{k: v for k, v in r.items() if k not in ("family", "version")} for r in bot_rows(led, keys)]


BOT_FILLS = 40    # latest fills kept per bot for the bot detail panel


def _about(bot) -> str:
    """The first paragraph of the nearest docstring the bot's class (or a base) has."""
    for cls in type(bot).__mro__:
        if cls.__name__ in ("Bot", "object"):
            break
        doc = (cls.__doc__ or "").strip()
        if doc and not doc.startswith(cls.__name__ + "("):   # dataclass default docstring
            return " ".join(doc.split("\n\n")[0].split())
    return ""


def bot_info_rows() -> list[dict]:
    """What each bot is: its title, what it does, and what it trades."""
    rows = []
    for key in list(BOTS) + [k for k in ALL_BOTS if k not in BOTS]:
        b = ALL_BOTS[key]
        rows.append({
            "bot": key, "group": GROUP_OF.get(key, "Other"), "running": key in BOTS,
            "title": getattr(b, "title", "") or key, "about": _about(b),
            "markets": ", ".join(b.kinds), "live": b.trades_live, "pregame": b.trades_pregame,
            "max_per_market_pct": round(b.max_market_frac * 100, 1),
        })
    return rows


def holding_rows(led: Ledger) -> list[dict]:
    """What each bot holds now (open positions in unsettled markets) and its resting orders."""
    rows = []
    started = {}
    for f in led.fills:
        started.setdefault((f["bot"], f["slug"]), f["t"])
    for key, ms in led.positions.items():
        for slug, p in ms.items():
            if slug in led.settled or abs(p.contracts) < 1e-9:
                continue
            n = abs(p.contracts)
            side = "long" if p.contracts > 0 else "short"
            # Average price paid per contract of the side held, fees included.
            avg = -p.cash / n if side == "long" else 1 - p.cash / n
            mark = led.marks.get(slug)
            rows.append({
                "bot": key, "what": "holding", "market": slug, "side": side,
                "contracts": round(n, 2), "price": round(avg, 4),
                "now": None if mark is None else round(mark if side == "long" else 1 - mark, 4),
                "open_pnl": None if mark is None else round(p.cash + p.contracts * mark, 2),
                "since": started.get((key, slug)),
            })
    for o in led.orders:
        rows.append({"bot": o.bot, "what": "resting order", "market": o.slug, "side": o.side,
                     "contracts": round(o.contracts, 2), "price": round(o.price, 4), "now": None,
                     "open_pnl": None, "since": o.placed_at})
    rows.sort(key=lambda r: (r["bot"], r["what"], r["since"] or ""))
    return [dict(r, id=i) for i, r in enumerate(rows)]


def bot_fill_rows(led: Ledger) -> list[dict]:
    """The latest BOT_FILLS fills of every bot, newest first."""
    kept: dict[str, list] = {}
    for i in range(len(led.fills) - 1, -1, -1):
        f = led.fills[i]
        got = kept.setdefault(f["bot"], [])
        if len(got) < BOT_FILLS:
            got.append((i, f))
    rows = []
    for key in sorted(kept):
        for i, f in kept[key]:
            rows.append({"id": i, "t": f["t"], "bot": f["bot"], "market": f["slug"],
                         "side": f["side"], "price": f["price"], "contracts": f["contracts"],
                         "dollars": round(f["contracts"] * f["price"], 2), "fee": f["fee"],
                         "how": f["kind"], "tag": f["tag"]})
    return rows


def idea_rows(led: Ledger) -> list[dict]:
    totals = {r["bot"]: r for r in bot_rows(led, [i.key for i in IDEAS if i.key])}
    rows = []
    for i in IDEAS:
        t = totals.get(i.key) or {}
        rows.append({
            "idea": i.n, "title": i.title, "status": i.status, "rule": i.note, "bot": i.key or "",
            "since": t.get("since"), "settled_pnl": t.get("settled_pnl"), "equity": t.get("equity"),
            "fees": t.get("fees"), "fills": (t.get("taker_fills") or 0) + (t.get("maker_fills") or 0)
            if t else None, "markets": t.get("markets"), "open": t.get("open"),
            "settled_markets": t.get("settled_markets"), "won_markets": t.get("won_markets"),
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
                "version": "v2" if key in FIXED_KEYS else "idea" if key in IDEA_KEYS else "original",
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


# --- the live runner (psb.live) ------------------------------------------------
# Its state stays on the machine it runs on (live-results/ is git-ignored), so
# these files are written next to it and attached to the dashboard by hand.

LIVE_MODES = (("real", "Real money", "ledger.json", "orders.jsonl"),
              ("shadow", "Shadow (no orders sent)", "shadow-ledger.json", "shadow-orders.jsonl"))
RECENT_ORDERS = 200
_START = re.compile(r"^(\S+) (ARMED: REAL MONEY|SHADOW: no orders sent); bot (\S+); .*?"
                    r"(?:; not armed because: (.*))?$")
_STATS = re.compile(r"^(\S+) stats (\{.*\}) open_orders=(\d+) exposure=\$(-?[\d.]+)$")
_HALT = re.compile(r"^(\S+) HALT: (.*)$")


def _read_log(path: Path) -> dict[str, dict]:
    """Per mode: the latest start, stats and halt lines of live.log."""
    seen: dict[str, dict] = {}
    mode = None
    if not path.exists():
        return seen
    for line in path.read_text().splitlines():
        if m := _START.match(line):
            mode = "real" if m[2].startswith("ARMED") else "shadow"
            seen[mode] = {"started": m[1], "last_seen": m[1], "bot": m[3],
                          "not_armed_because": m[4], "halted": None}
        elif mode and (m := _STATS.match(line)):
            s = json.loads(m[2])
            seen[mode].update(last_seen=m[1], open_orders=int(m[3]), exposure=float(m[4]),
                              polls=s.get("polls"), no_cash=s.get("no_cash"))
        elif mode and (m := _HALT.match(line)):
            seen[mode].update(last_seen=m[1], halted=m[2])
    return seen


def live_rows(state: Path, config: dict | None) -> list[dict]:
    """One row per mode: whether it has run, its bot, limits and results."""
    seen = _read_log(state / "live.log")
    limits = (config or {}).get("limits") or {}
    rows = []
    for mode, label, ledger_name, _ in LIVE_MODES:
        s = seen.get(mode, {})
        row = {"mode": label, "has_run": bool(s), "bot": s.get("bot"),
               "started": s.get("started"), "last_seen": s.get("last_seen"),
               "halted": s.get("halted"), "not_armed_because": s.get("not_armed_because"),
               "exposure": s.get("exposure"), "open_orders": s.get("open_orders"),
               "polls": s.get("polls"), "no_cash": s.get("no_cash"),
               "max_order_usd": limits.get("max_order_usd"),
               "max_exposure_usd": limits.get("max_exposure_usd"),
               "max_daily_loss_usd": limits.get("max_daily_loss_usd"),
               "config_armed": (config or {}).get("armed"), "config_bot": (config or {}).get("bot")}
        totals = {}
        if s.get("bot") and (state / ledger_name).exists():
            led = Ledger.load(state / ledger_name, 1000.0)
            totals = bot_rows(led, [s["bot"]])[0]
        for k in ("settled_pnl", "equity", "fees", "taker_fills", "maker_fills", "markets",
                  "open", "settled_markets", "won_markets", "cash_free"):
            row[k] = totals.get(k)
        rows.append(row)
    return rows


def live_order_rows(state: Path) -> list[dict]:
    """The latest orders the runner sent (real) or would have sent (shadow), newest first."""
    rows = []
    for mode, label, _, orders_name in LIVE_MODES:
        path = state / orders_name
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            o = json.loads(line)
            rows.append({"t": o["t"], "mode": label, "sent": o.get("sent", False),
                         "bot": o["bot"], "market": o["slug"], "kind": o["kind"],
                         "side": o["side"], "price": o["price"], "contracts": o["contracts"],
                         "dollars": round(o["contracts"] * o["price"], 2), "tag": o.get("tag"),
                         "order_id": o.get("order_id")})
    rows.sort(key=lambda r: r["t"], reverse=True)
    return [dict(r, id=i) for i, r in enumerate(rows[:RECENT_ORDERS])]


def export_live(state: Path, config_path: Path, out: Path, code: str) -> None:
    config = json.loads(config_path.read_text()) if config_path.exists() else None
    meta = [{"label": "dashboard.py", "value": code, "type": "code", "lang": "python"},
            {"label": "Input", "value": f"{state.name}/live.log, ledger.json, shadow-ledger.json, "
                                        f"orders.jsonl, shadow-orders.jsonl and {config_path.name}"}]
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("live", live_rows(state, config)), ("live-orders", live_order_rows(state))):
        (out / f"{name}.json").write_text(json.dumps({"rows": rows, "meta": meta}))
        print(f"{out / name}.json: {len(rows)} rows")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", default="paper-results/ledger.json")
    ap.add_argument("--out", default="paper-results/dashboard")
    ap.add_argument("--live", metavar="DIR", help="instead export the live runner's state in DIR "
                    "(e.g. live-results) to DIR/dashboard: live.json and live-orders.json")
    ap.add_argument("--config", default="live.json", help="with --live: the runner's config")
    a = ap.parse_args(argv)
    if a.live:
        state = Path(a.live)
        export_live(state, Path(a.config), state / "dashboard", Path(__file__).read_text())
        return
    led = Ledger.load(Path(a.ledger), 1000.0)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    code = Path(__file__).read_text()
    meta = [{"label": "dashboard.py", "value": code, "type": "code", "lang": "python"},
            {"label": "Input", "value": "paper-results/ledger.json (paper only, no real orders)"},
            {"label": "Paper bankroll per bot", "value": f"${led.bankroll:,.0f}"}]
    for name, rows in (("bots", bot_rows(led)), ("all-bots", all_bot_rows(led)), ("games", game_rows(led)),
                       ("fills", fill_rows(led)), ("ideas", idea_rows(led)),
                       ("bot-info", bot_info_rows()), ("holdings", holding_rows(led)),
                       ("bot-fills", bot_fill_rows(led))):
        (out / f"{name}.json").write_text(json.dumps({"rows": rows, "meta": meta}))
        print(f"{out / name}.json: {len(rows)} rows")


if __name__ == "__main__":
    main()
