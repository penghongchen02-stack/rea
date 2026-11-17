"""Paper trading on live Polymarket US order books. No orders are ever sent.

Each cycle, for every open US game-winner market in the chosen leagues:
  1. read the US top of book (public API),
  2. take a fair value from the matching international Polymarket game,
  3. let each strategy decide, using its report rules plus the fair-value edge,
  4. simulate fills conservatively and record them in a JSON ledger.

Fill model:
  taker  fills at the current best price, up to the displayed size, and pays the
         US taker fee at the configured tier.
  maker  rests a limit at a price better than the current book; it fills only if
         a later cycle shows the opposite best price trading through it (strictly
         better than our limit), which ignores queue position and is therefore
         optimistic about price but pessimistic about frequency. No fee, no rebate.

Positions use one signed contract count per market, as on Polymarket US: buying
the other side reduces or flips the held side and cashes out the difference.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import fees, reference, usmarket
from .markets import category_of
from .strategies import STRATEGIES, Candidate

STATE = Path(__file__).resolve().parent.parent / "paper"
DEFAULT_LEAGUES = ["nba", "nfl", "nhl", "cbb", "epl", "lal", "bun", "sea", "ucl", "mls",
                   "ufc", "cs2", "dota2", "valorant", "lol"]
BOTH_SIDES = {"ferrarichampions2026", "gamblingisallyourneed", "randomgamebet"}
TAKER_ONLY = {"sinenooneei"}  # live directional entries in the report are taker fills


@dataclass
class Config:
    min_edge: float = 0.02          # required edge after fees, in price units
    order_contracts: float = 10.0   # per simulated order
    max_contracts: float = 100.0    # per strategy per market
    taker_theta: float = fees.WORST_TAKER_THETA  # a new account starts at tier 1


@dataclass
class Quote:
    strategy: str
    slug: str
    side: str        # "long" buys the long instrument, "short" buys the other side
    price: float     # price paid per contract of the chosen side
    contracts: float
    placed_at: str
    fair: float      # fair value of the chosen side when placed


@dataclass
class Position:
    contracts: float = 0.0   # >0 long, <0 short
    cash: float = 0.0        # cash flow so far (negative = spent)
    fees: float = 0.0


@dataclass
class Ledger:
    quotes: list[Quote] = field(default_factory=list)
    positions: dict[str, dict[str, Position]] = field(default_factory=dict)  # strategy -> slug
    fills: list[dict] = field(default_factory=list)
    settled: dict[str, float] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Ledger":
        if not path.exists():
            return cls()
        d = json.loads(path.read_text())
        return cls(
            quotes=[Quote(**q) for q in d["quotes"]],
            positions={s: {m: Position(**p) for m, p in ms.items()} for s, ms in d["positions"].items()},
            fills=d["fills"],
            settled=d["settled"],
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "quotes": [asdict(q) for q in self.quotes],
            "positions": {s: {m: asdict(p) for m, p in ms.items()} for s, ms in self.positions.items()},
            "fills": self.fills,
            "settled": self.settled,
        }, indent=1))
        tmp.replace(path)

    def position(self, strategy: str, slug: str) -> Position:
        return self.positions.setdefault(strategy, {}).setdefault(slug, Position())

    def fill(self, strategy: str, slug: str, side: str, price: float, contracts: float,
             fee: float, kind: str, fair: float) -> None:
        """Buy `contracts` of `side` at `price` (price of that side)."""
        pos = self.position(strategy, slug)
        signed = contracts if side == "long" else -contracts
        # Buying long at p costs p; buying short at q receives (1-q) on the long instrument.
        long_px = price if side == "long" else 1 - price
        pos.cash -= signed * long_px
        pos.contracts += signed
        pos.fees += fee
        pos.cash -= fee
        self.fills.append({"t": _now(), "strategy": strategy, "slug": slug, "side": side,
                           "price": round(price, 4), "contracts": contracts,
                           "fee": round(fee, 4), "kind": kind,
                           "fair": round(fair, 4)})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _round_to_tick(p: float, tick: float, down: bool) -> float:
    n = p / tick
    n = math.floor(n + 1e-9) if down else math.ceil(n - 1e-9)
    return round(n * tick, 6)


def decide(strategy: str, m: usmarket.UsMarket, top: usmarket.Top, fair: float,
           cfg: Config, held: float) -> list[tuple[str, str, float]]:
    """Return (kind, side, price) actions for one strategy on one market."""
    accept = STRATEGIES[strategy].accept
    hour = datetime.now(timezone.utc).hour
    category = category_of(m.event_slug)
    actions: list[tuple[str, str, float]] = []
    room_long = cfg.max_contracts - held
    room_short = cfg.max_contracts + held
    sides = []
    if top.ask is not None:
        sides.append(("long", top.ask, fair, room_long))
    if top.bid is not None:
        sides.append(("short", 1 - top.bid, 1 - fair, room_short))
    for side, take_px, fv, room in sides:
        if room < cfg.order_contracts or not 0 < take_px < 1:
            continue
        if not accept(Candidate(category, "moneyline", "BUY", take_px, hour)):
            continue
        fee = fees.taker_fee(1, take_px, cfg.taker_theta)
        if fv - take_px - fee >= cfg.min_edge:
            actions.append(("taker", side, take_px))
            continue
        if strategy in TAKER_ONLY:
            continue
        # Rest a bid at most min_edge below fair value: one tick above the current
        # best bid for this side when that is still cheap enough, else at the limit.
        limit = _round_to_tick(fv - cfg.min_edge, m.tick, down=True)
        best_same = top.bid if side == "long" else (1 - top.ask if top.ask is not None else None)
        price = limit if best_same is None else min(limit, round(best_same + m.tick, 6))
        if 0 < price < take_px:
            actions.append(("maker", side, price))
    if strategy not in BOTH_SIDES:
        # Directional strategies keep only the side with the larger edge.
        actions.sort(key=lambda a: -((fair if a[1] == "long" else 1 - fair) - a[2]))
        actions = actions[:1]
    return actions


def cycle(leagues: list[str], ledger: Ledger, cfg: Config, log) -> dict:
    stats = {"markets": 0, "matched": 0, "taker_fills": 0, "maker_fills": 0, "quotes": 0}
    markets: list[usmarket.UsMarket] = []
    for lg in leagues:
        try:
            markets += usmarket.game_markets(lg)
        except Exception as e:  # a league outage must not stop the others
            log(f"league {lg}: {e}")
    events: dict[str, dict | None] = {}
    live_slugs = set()
    for m in markets:
        stats["markets"] += 1
        try:
            top = usmarket.top_of_book(m.slug)
        except Exception as e:
            log(f"book {m.slug}: {e}")
            continue
        if top.state not in (None, "MARKET_STATE_OPEN"):
            continue
        live_slugs.add(m.slug)
        # Resting quotes from earlier cycles: fill only if the book traded through them.
        keep = []
        for q in ledger.quotes:
            if q.slug != m.slug:
                keep.append(q)
                continue
            opp = top.ask if q.side == "long" else (1 - top.bid if top.bid is not None else None)
            if opp is not None and opp < q.price:
                ledger.fill(q.strategy, q.slug, q.side, q.price, q.contracts, 0.0, "maker", q.fair)
                stats["maker_fills"] += 1
            # Every quote lives one cycle; it is re-placed below if still wanted.
        ledger.quotes = keep
        if m.event_slug not in events:
            events[m.event_slug] = reference.find_event(m)
        ev = events[m.event_slug]
        fair = reference.fair_value(m, ev) if ev else None
        if fair is None:
            continue
        stats["matched"] += 1
        for strategy in STRATEGIES:
            held = ledger.position(strategy, m.slug).contracts
            for kind, side, px in decide(strategy, m, top, fair, cfg, held):
                if kind == "taker":
                    shown = top.ask_qty if side == "long" else top.bid_qty
                    n = min(cfg.order_contracts, shown)
                    if n <= 0:
                        continue
                    fee = fees.taker_fee(n, px, cfg.taker_theta)
                    fv = fair if side == "long" else 1 - fair
                    ledger.fill(strategy, m.slug, side, px, n, fee, "taker", fv)
                    stats["taker_fills"] += 1
                else:
                    fv = fair if side == "long" else 1 - fair
                    ledger.quotes.append(Quote(strategy, m.slug, side, px, cfg.order_contracts,
                                               _now(), fv))
                    stats["quotes"] += 1
    # Quotes on markets that are no longer open are cancelled.
    ledger.quotes = [q for q in ledger.quotes if q.slug in live_slugs]
    return stats


def settle(ledger: Ledger) -> int:
    n = 0
    open_slugs = {slug for ms in ledger.positions.values() for slug, p in ms.items()
                  if slug not in ledger.settled and abs(p.contracts) > 1e-9}
    for slug in sorted(open_slugs):
        px = usmarket.settlement(slug)
        if px is not None:
            ledger.settled[slug] = px
            n += 1
    return n


def report(ledger: Ledger) -> str:
    lines = ["| Strategy | Fills (taker/maker) | Open markets | Settled P&L | Fees | Open exposure |",
             "|---|---|---|---|---|---|"]
    for s in STRATEGIES:
        fills = [f for f in ledger.fills if f["strategy"] == s]
        taker = sum(f["kind"] == "taker" for f in fills)
        settled_pnl = fee_total = exposure = 0.0
        open_n = 0
        for slug, p in ledger.positions.get(s, {}).items():
            fee_total += p.fees
            if slug in ledger.settled:
                settled_pnl += p.cash + p.contracts * ledger.settled[slug]
            elif abs(p.contracts) > 1e-9:
                open_n += 1
                exposure += abs(p.contracts)
        lines.append(f"| {s} | {taker}/{len(fills) - taker} | {open_n} | ${settled_pnl:,.2f} "
                     f"| ${fee_total:,.2f} | {exposure:,.0f} contracts |")
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description="Paper-trade the seven strategies. Sends no orders.")
    p.add_argument("--cycles", type=int, default=1)
    p.add_argument("--interval", type=float, default=60.0, help="seconds between cycles")
    p.add_argument("--leagues", nargs="*", default=DEFAULT_LEAGUES)
    p.add_argument("--ledger", default=str(STATE / "ledger.json"))
    p.add_argument("--min-edge", type=float, default=Config.min_edge)
    p.add_argument("--report", action="store_true", help="only settle and print the report")
    a = p.parse_args(argv)
    cfg = Config(min_edge=a.min_edge)
    path = Path(a.ledger)
    ledger = Ledger.load(path)
    log_path = path.with_name("paper.log")

    def log(msg: str) -> None:
        line = f"{_now()} {msg}"
        print(line, flush=True)
        with log_path.open("a") as f:
            f.write(line + "\n")

    if not a.report:
        for i in range(a.cycles):
            started = time.monotonic()
            stats = cycle(a.leagues, ledger, cfg, log)
            ledger.save(path)
            log(f"cycle {i + 1}/{a.cycles} {json.dumps(stats)}")
            if i + 1 < a.cycles:
                time.sleep(max(0.0, a.interval - (time.monotonic() - started)))
    log(f"settled {settle(ledger)} markets")
    ledger.save(path)
    print(report(ledger))


if __name__ == "__main__":
    main()
