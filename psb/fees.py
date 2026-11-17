"""Polymarket US fee schedule effective 2026-11-14 (https://docs.polymarket.us/fees.md).

Standard taker fee = theta * contracts * p * (1 - p). Maker fills pay no fee;
maker rebates start only above $10M of 30-day maker volume, so they are ignored.
The docs do not state per-fill rounding, so fees are left unrounded here.
"""
from __future__ import annotations

# (trailing 30-day taker volume upper bound, theta)
TAKER_TIERS: list[tuple[float, float]] = [
    (10_000, 0.095),
    (50_000, 0.085),
    (250_000, 0.075),
    (float("inf"), 0.0695),
]
BEST_TAKER_THETA = TAKER_TIERS[-1][1]
WORST_TAKER_THETA = TAKER_TIERS[0][1]


def taker_theta(trailing_30d_taker_volume: float) -> float:
    for bound, theta in TAKER_TIERS:
        if trailing_30d_taker_volume < bound:
            return theta
    return BEST_TAKER_THETA


def taker_fee(contracts: float, price: float, theta: float) -> float:
    if not 0.0 < price < 1.0:
        raise ValueError(f"price must be strictly between 0 and 1, got {price}")
    return theta * contracts * price * (1.0 - price)
