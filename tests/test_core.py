import json
import math

import pytest

from psb import fees
from psb.markets import category_of, resolution_of
from psb.strategies import STRATEGIES, Candidate


def test_fee_matches_docs_example():
    # docs.polymarket.us/fees.md: Tier 4, 100 contracts at $0.50 -> $1.74 (rounded).
    assert fees.taker_fee(100, 0.5, fees.BEST_TAKER_THETA) == pytest.approx(1.7375)


@pytest.mark.parametrize("volume,theta", [(0, 0.095), (9_999, 0.095), (10_000, 0.085),
                                          (249_999, 0.075), (250_000, 0.0695)])
def test_taker_tiers(volume, theta):
    assert fees.taker_theta(volume) == theta


def test_fee_rejects_settled_prices():
    with pytest.raises(ValueError):
        fees.taker_fee(1, 1.0, 0.095)


def test_us_cash_out_equals_holding_both_sides():
    """docs.polymarket.us opposing-sides example: 10 Yes @0.60, then buy 10 No @0.25."""
    yes_n, yes_p, no_p = 10, 0.60, 0.25
    us_pnl = yes_n * (1 - no_p) - yes_n * yes_p           # cash out $7.50 on $6.00 cost
    global_pnl = yes_n * 1.0 - (yes_n * yes_p + yes_n * no_p)  # hold both, $10 payout
    assert us_pnl == pytest.approx(global_pnl) == pytest.approx(1.5)
    # p(1-p) is symmetric, so the second leg's taker fee is the same either way.
    assert fees.taker_fee(10, no_p, 0.08) == pytest.approx(fees.taker_fee(10, 1 - no_p, 0.08))


@pytest.mark.parametrize("slug,cat", [
    ("nba-den-nop-2026-11-14-spread-away-12pt5", "nba"),
    ("mlb-atl-pit-2026-07-07", "mlb"),
    ("fifwc-fra-swe-2026-06-30-fra", "soccer"),
    ("atp-sinner-alcaraz-2026-06-08", "tennis"),
    ("lol-t1-geng-2026-11-01", "lol"),
    ("", "other"),
    ("presidential-election-2028", "other"),
])
def test_category(slug, cat, monkeypatch):
    # Hermetic stand-in for data/sports.json (international /sports league tags).
    from psb import markets
    monkeypatch.setattr(markets, "_league_tags", lambda: {"fifwc": frozenset({"100350"})})
    assert category_of(slug) == cat


def _market(prices, closed=True, status="resolved"):
    return {"closed": closed, "umaResolutionStatus": status,
            "outcomePrices": json.dumps(prices), "sportsMarketType": "moneyline"}


def test_resolution_settled():
    r = resolution_of(_market(["0", "1"]))
    assert (r.payout(0), r.payout(1)) == (0.0, 1.0)


@pytest.mark.parametrize("market", [
    None,
    _market(["0", "1"], closed=False),
    _market(["0", "1"], status="proposed"),
    _market(["0.37", "0.63"]),           # live prices, not a settlement
    {"closed": True, "umaResolutionStatus": "resolved", "outcomePrices": "not json"},
])
def test_resolution_unknown_stays_unknown(market):
    assert resolution_of(market).payout(0) is None


def test_every_wallet_has_a_strategy():
    from psb.sources import WALLETS
    assert set(STRATEGIES) == set(WALLETS)


def test_kch123_rule():
    s = STRATEGIES["kch123"].accept
    assert s(Candidate("nfl", "moneyline", "BUY", 0.55, 12))
    assert not s(Candidate("nfl", "moneyline", "BUY", 0.55, 17))   # weak hour
    assert not s(Candidate("nfl", "moneyline", "SELL", 0.55, 12))
    assert not s(Candidate("nba", "moneyline", "BUY", 0.55, 12))
    assert not s(Candidate("nfl", "moneyline", "BUY", 0.75, 12))
