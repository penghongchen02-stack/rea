from datetime import datetime, timezone

import pytest

from psb import fees, paper, reference
from psb.usmarket import Top, UsMarket

START = datetime(2026, 11, 21, 12, 30, tzinfo=timezone.utc)


def _us(name, is_draw=False, league="epl", slug="atc-epl-mnc-ful-2026-11-21-x"):
    return UsMarket(slug, "epl-mnc-ful-2026-11-21", league, name,
                    ("Manchester City", "Fulham"), is_draw, START, 0.01)


def _intl(title, bid, ask):
    return {"outcomes": '["Yes", "No"]', "groupItemTitle": title, "sportsMarketType": "moneyline",
            "bestBid": bid, "bestAsk": ask, "acceptingOrders": True}


# Shapes observed on gamma-api for epl-mac-ful-2026-11-21; the draw title names both teams.
SOCCER_EVENT = {"markets": [
    _intl("Manchester City FC", 0.69, 0.70),
    _intl("Draw (Manchester City FC vs. Fulham FC)", 0.18, 0.19),
    _intl("Fulham FC", 0.12, 0.13),
]}


@pytest.mark.parametrize("market,expected", [
    (_us("Manchester City"), 0.695),
    (_us("Draw", is_draw=True), 0.185),
    (_us("Fulham"), 0.125),
])
def test_soccer_fair_value_picks_the_right_outcome(market, expected):
    assert reference.fair_value(market, SOCCER_EVENT) == pytest.approx(expected)


def test_two_way_fair_value_uses_outcome_index():
    event = {"markets": [{"outcomes": '["Knicks", "Raptors"]', "sportsMarketType": "moneyline",
                          "bestBid": 0.57, "bestAsk": 0.58}]}
    m = UsMarket("aec-nba-ny-tor-2026-11-17", "nba-ny-tor-2026-11-17", "nba", "Raptors",
                 ("Knicks", "Raptors"), False, START, 0.01)
    assert reference.fair_value(m, event) == pytest.approx(0.425)


def test_no_fair_value_without_a_two_sided_book():
    event = {"markets": [_intl("Fulham FC", None, 0.13)]}
    assert reference.fair_value(_us("Fulham"), event) is None


def test_opposite_buy_nets_like_polymarket_us():
    """Hold 10 long @0.60, buy 10 short @0.25: closes the position for +$1.50."""
    led = paper.Ledger()
    led.fill("s", "m", "long", 0.60, 10, 0.0, "taker", 0.6)
    led.fill("s", "m", "short", 0.25, 10, 0.0, "taker", 0.25)
    pos = led.position("s", "m")
    assert pos.contracts == pytest.approx(0)
    assert pos.cash == pytest.approx(1.5)


def test_taker_requires_edge_after_fee():
    cfg = paper.Config(min_edge=0.02, taker_theta=fees.WORST_TAKER_THETA)
    m = _us("Fulham", league="epl", slug="atc-epl-mnc-ful-2026-11-21-ful")
    top = Top(bid=0.12, bid_qty=100, ask=0.13, ask_qty=100, state="MARKET_STATE_OPEN")
    # Fair 0.125: no edge either way, so no taker; a resting bid below fair is allowed.
    acts = paper.decide("ferrarichampions2026", m, top, 0.125, cfg, held=0)
    assert all(kind == "maker" for kind, _, _ in acts)
    # Fair 0.20: 0.13 + fee(0.13) + 0.02 < 0.20, so take the long side.
    acts = paper.decide("ferrarichampions2026", m, top, 0.20, cfg, held=0)
    assert ("taker", "long", 0.13) in acts


def test_position_cap_blocks_more_buys():
    cfg = paper.Config(order_contracts=10, max_contracts=100)
    m = _us("Fulham", slug="atc-epl-mnc-ful-2026-11-21-ful")
    top = Top(bid=0.12, bid_qty=100, ask=0.13, ask_qty=100, state="MARKET_STATE_OPEN")
    acts = paper.decide("ferrarichampions2026", m, top, 0.30, cfg, held=100)
    assert all(side == "short" for _, side, _ in acts)
