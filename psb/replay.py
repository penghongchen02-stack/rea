"""Replay each source wallet's downloaded window.

For every fill on a resolved market, P&L before fees is
    BUY:  size * (payout - price)      SELL: size * (price - payout)
which sums exactly to the wallet's cash flows plus final settlement.

On Polymarket US a buy of the opposite side cashes out the held side at
(1 - opposite price) instead of keeping both legs. Per contract that pays the
same as holding both to resolution, and p*(1-p) is symmetric, so the US taker
fee is identical too. The replay therefore needs no netting adjustment; see
tests/test_replay.py for the worked example.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import fees
from .fetch import DATA
from .markets import category_of, resolution_of
from .polydata import read_jsonl
from .sources import WALLETS
from .strategies import STRATEGIES, Candidate


@dataclass
class Book:
    fills: int = 0
    notional: float = 0.0
    gross: float = 0.0
    taker_notional: float = 0.0
    us_fee_best: float = 0.0   # US tier 4 taker fee on the fills that were taker fills
    us_fee_worst: float = 0.0  # same fills at US tier 1 (a new account)

    def add(self, size: float, price: float, pnl: float, is_taker: bool) -> None:
        self.fills += 1
        self.notional += size * price
        self.gross += pnl
        if is_taker:
            self.taker_notional += size * price
            self.us_fee_best += fees.taker_fee(size, price, fees.BEST_TAKER_THETA)
            self.us_fee_worst += fees.taker_fee(size, price, fees.WORST_TAKER_THETA)

    def summary(self) -> dict:
        n = self.notional or float("nan")
        return {
            "fills": self.fills,
            "notional": round(self.notional, 2),
            "gross_pnl": round(self.gross, 2),
            "taker_share_pct": round(100 * self.taker_notional / n, 1),
            "gross_roi_pct": round(100 * self.gross / n, 3),
            "us_t4_roi_pct": round(100 * (self.gross - self.us_fee_best) / n, 3),
            "us_t1_roi_pct": round(100 * (self.gross - self.us_fee_worst) / n, 3),
        }


@dataclass
class WalletReplay:
    all: Book = field(default_factory=Book)
    accepted: Book = field(default_factory=Book)
    rejected: Book = field(default_factory=Book)
    by_category: dict[str, Book] = field(default_factory=lambda: defaultdict(Book))
    unresolved_fills: int = 0
    unresolved_notional: float = 0.0


def candidate(trade: dict, market_type: str | None) -> Candidate:
    hour = datetime.fromtimestamp(int(trade["timestamp"]), tz=timezone.utc).hour
    return Candidate(category_of(trade.get("slug") or trade.get("event_slug") or ""),
                     market_type, trade["side"], float(trade["price"]), hour)


def replay_wallet(key: str) -> dict:
    d = DATA / key
    meta = json.loads((d / "meta.json").read_text())
    markets = json.loads((d / "markets.json").read_text())
    strat = STRATEGIES[key]
    r = WalletReplay()
    for t in read_jsonl(d / "trades.jsonl"):
        size, price = float(t["size"]), float(t["price"])
        if not 0.0 < price < 1.0 or size <= 0:
            continue
        res = resolution_of(markets.get(t["condition_id"]))
        payout = res.payout(int(t["outcome_index"]))
        if payout is None:
            r.unresolved_fills += 1
            r.unresolved_notional += size * price
            continue
        pnl = size * (payout - price) if t["side"] == "BUY" else size * (price - payout)
        c = candidate(t, res.market_type)
        taker = bool(t.get("is_taker"))
        r.all.add(size, price, pnl, taker)
        r.by_category[c.category].add(size, price, pnl, taker)
        (r.accepted if strat.accept(c) else r.rejected).add(size, price, pnl, taker)

    # Closed positions over-represent redeemed winners (losing positions are never
    # redeemed), so they are used only for the fee rate the wallet actually paid.
    positions = read_jsonl(d / "closed_positions.jsonl")
    pos_cost = sum(float(p.get("avg_price") or 0) * float(p.get("total_size") or 0)
                   for p in positions)
    pos_fees = sum(float(p.get("entry_fees_usdc") or 0) for p in positions)
    fee_rate = pos_fees / pos_cost if pos_cost else None
    total = r.all.notional or float("nan")
    return {
        "wallet": key,
        "address": WALLETS[key].address,
        "address_inferred": WALLETS[key].address_inferred,
        "window_utc": [datetime.fromtimestamp(x, tz=timezone.utc).strftime("%Y-%m-%d")
                       for x in meta["window_covered"]],
        "trade_cap_hit": meta["trade_cap_hit"],
        "resolved": r.all.summary(),
        "unresolved_fills": r.unresolved_fills,
        "unresolved_notional": round(r.unresolved_notional, 2),
        "paid_fee_rate_pct": round(100 * fee_rate, 3) if fee_rate is not None else None,
        "roi_after_paid_fees_pct": (round(100 * (r.all.gross / total - fee_rate), 3)
                                    if fee_rate is not None else None),
        "strategy": {
            "rules": strat.rules,
            "agreement_fills_pct": round(100 * r.accepted.fills / max(r.all.fills, 1), 1),
            "agreement_notional_pct": round(100 * r.accepted.notional / total, 1),
            "accepted": r.accepted.summary(),
            "rejected": r.rejected.summary(),
        },
        "by_category": {k: v.summary() for k, v in
                        sorted(r.by_category.items(), key=lambda kv: -kv[1].notional)},
    }


def markdown(results: list[dict]) -> str:
    lines = [
        "| Wallet | Window | Resolved notional | Taker share | Gross ROI | Actual ROI after their fees "
        "| ROI with US fees (T4 / T1) | Rules agree (notional) | Rules-only gross ROI "
        "| Rules-only with US fees T4 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for x in results:
        a, s = x["resolved"], x["strategy"]
        acc = s["accepted"]
        lines.append(
            f"| {x['wallet']} | {x['window_utc'][0]}→{x['window_utc'][1]} | ${a['notional']:,.0f} "
            f"| {a['taker_share_pct']}% | {a['gross_roi_pct']}% "
            f"| {x['roi_after_paid_fees_pct']}% (fees {x['paid_fee_rate_pct']}%) "
            f"| {a['us_t4_roi_pct']}% / {a['us_t1_roi_pct']}% "
            f"| {s['agreement_notional_pct']}% | {acc['gross_roi_pct']}% | {acc['us_t4_roi_pct']}% |"
        )
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("wallets", nargs="*", default=list(WALLETS))
    p.add_argument("--out", default=str(DATA / "replay.json"))
    a = p.parse_args(argv)
    results = [replay_wallet(k) for k in a.wallets if (DATA / k / "meta.json").exists()]
    Path(a.out).write_text(json.dumps(results, indent=2))
    print(markdown(results))


if __name__ == "__main__":
    main()
