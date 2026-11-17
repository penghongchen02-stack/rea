from datetime import datetime, timedelta, timezone

import pytest

from psb import bots, fees, paper, reference
from psb.usmarket import Book, UsMarket, participants_of

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


def _m(league="epl", kind="moneyline", name="Fulham", line=None, is_draw=False,
       start=NOW + timedelta(hours=3), slug=None, event="epl-mnc-ful-2026-11-21",
       title="Manchester City FC vs. Fulham FC", teams=("Manchester City", "Fulham")):
    return UsMarket(slug or f"x-{event}-{name}", event, league, kind, name, line, teams,
                    participants_of(title), is_draw, start, 0.01)


def _book(bid, ask, qty=1000.0, last=None, traded=None):
    return Book(((bid, qty),) if bid else (), ((ask, qty),) if ask else (),
                "MARKET_STATE_OPEN", last, traded)


def _view(m, bk, fair, prev=None, live=False, cash=1000.0, held=0.0, now=NOW):
    return bots.View(m, bk, fair, prev, live, now, 1000.0, cash, held, fees.WORST_TAKER_THETA)


# --- linking US markets to international outcomes ---------------------------

def _intl(title, bid, ask, mtype="moneyline", outcomes='["Yes", "No"]', line=None):
    return {"outcomes": outcomes, "groupItemTitle": title, "sportsMarketType": mtype,
            "bestBid": bid, "bestAsk": ask, "acceptingOrders": True, "line": line,
            "clobTokenIds": '["t0", "t1"]'}


SOCCER = {"markets": [
    _intl("Manchester City FC", 0.69, 0.70),
    _intl("Draw (Manchester City FC vs. Fulham FC)", 0.18, 0.19),   # names both teams
    _intl("Fulham FC", 0.12, 0.13),
]}


@pytest.mark.parametrize("market,expected", [
    (_m(name="Manchester City"), 0.695),
    (_m(name="Draw", is_draw=True), 0.185),
    (_m(name="Fulham"), 0.125),
])
def test_soccer_links_to_the_right_outcome(market, expected):
    assert reference.gamma_fair(reference.link(market, SOCCER)) == pytest.approx(expected)


NBA = {"markets": [
    _intl(None, 0.57, 0.58, outcomes='["Knicks", "Raptors"]'),
    _intl("Spread -3.5", 0.48, 0.49, "spreads", '["Knicks", "Raptors"]', -3.5),
    _intl("Spread -2.5", 0.40, 0.42, "spreads", '["Raptors", "Knicks"]', -2.5),
    _intl("O/U 216.5", 0.49, 0.50, "totals", '["Over", "Under"]', 216.5),
]}
NBA_EVENT = dict(league="nba", event="nba-ny-tor-2026-11-17", title="NY Knicks vs. TOR Raptors",
                 teams=("Knicks", "Raptors"))


@pytest.mark.parametrize("kind,name,line,expected", [
    ("moneyline", "Raptors", None, 0.425),
    ("spreads", "Knicks", -3.5, 0.485),          # same team, same line
    ("spreads", "Knicks", 2.5, 1 - 0.41),        # Knicks +2.5 = not (Raptors -2.5)
    ("totals", "Over", 216.5, 0.495),
])
def test_lines_link_including_complements(kind, name, line, expected):
    ln = reference.link(_m(kind=kind, name=name, line=line, **NBA_EVENT), NBA)
    assert reference.gamma_fair(ln) == pytest.approx(expected)


def test_missing_line_stays_unlinked():
    assert reference.link(_m(kind="spreads", name="Knicks", line=3.5, **NBA_EVENT), NBA) is None


def test_wide_international_book_gives_no_fair_value():
    ln = reference.link(_m(name="Fulham"), {"markets": [_intl("Fulham FC", 0.01, 0.99)]})
    assert reference.gamma_fair(ln) is None


def test_live_fair_uses_the_token_and_complement():
    ln = reference.Link({"clobTokenIds": '["t0", "t1"]'}, 1, True)
    assert reference.live_fair(ln, {"t1": 0.30}) == pytest.approx(0.70)
    assert reference.live_fair(ln, {}) is None


def _cs2(us_title, intl_title, outcomes, start):
    m = _m(league="cs2", name="Hype Tech", event="cs2-ht-tlr-2026-11-17", title=us_title,
           teams=("Hype Tech",), start=datetime(2026, 11, 17, 15, tzinfo=timezone.utc))
    ev = {"slug": "cs2-x-2026-11-17", "title": intl_title, "startTime": start,
          "markets": [{"sportsMarketType": "moneyline", "outcomes": outcomes}]}
    return m, ev


def test_same_team_different_opponent_is_not_the_same_game():
    # Observed 2026-11-17: Hype Tech vs. The Last Resort was matched to Hype Tech vs. ex-INOX.
    m, ev = _cs2("Hype Tech vs. The Last Resort", "Hype Tech vs. ex-INOX Division",
                 '["Hype Tech", "ex-INOX Division"]', "2026-11-17T14:00:00Z")
    assert not reference.same_game(m, ev)


def test_search_strips_us_city_codes():
    # "NY Knicks TOR Raptors" returns New York House races; "Knicks Raptors" finds the game.
    m = _m(**{**NBA_EVENT, "teams": ()})
    assert "Knicks Raptors" in reference.search_queries(m)


# --- ledger and cash ---------------------------------------------------------

def test_opposite_buy_nets_like_polymarket_us_and_frees_cash():
    led = paper.Ledger()
    led.record("b", "m", "long", 0.60, 10, 0.0, "taker", "x", None, NOW)
    assert led.available_cash("b") == pytest.approx(994)
    led.record("b", "m", "short", 0.25, 10, 0.0, "taker", "x", None, NOW)   # paired cost 0.85
    pos = led.position("b", "m")
    assert pos.contracts == pytest.approx(0) and pos.cash == pytest.approx(1.5)
    assert led.available_cash("b") == pytest.approx(1001.5)


def test_resting_orders_reserve_cash():
    led = paper.Ledger()
    led.orders.append(paper.Order("b", "m", "long", 0.40, 10, "x", "t", 0))
    assert led.available_cash("b") == pytest.approx(996)
    assert led.available_cash("other") == pytest.approx(1000)


# --- engine fill model -------------------------------------------------------

class _Log(list):
    def __call__(self, msg):
        self.append(msg)


def _engine():
    led = paper.Ledger()
    return paper.Engine(led, [], fees.WORST_TAKER_THETA, _Log(), 24), led


def test_take_walks_the_book_up_to_limit_and_pays_fee():
    eng, led = _engine()
    m = _m()
    t = paper.Tracked(m, None)
    bk = Book((), ((0.40, 10), (0.41, 10), (0.50, 100)), "MARKET_STATE_OPEN", None, None)
    eng._apply("swisstony", bots.BOTS["swisstony"], t, bk, 0.6,
               [bots.Take("long", 100.0, 0.45, "probe")], NOW)
    got = [(f["price"], f["contracts"]) for f in led.fills]
    assert got == [(0.40, 10), (0.41, 10)]
    assert led.position("swisstony", m.slug).fees == pytest.approx(
        fees.taker_fee(10, 0.40, 0.095) + fees.taker_fee(10, 0.41, 0.095))


def test_rest_fills_only_on_trades_through_its_price_after_placement():
    eng, led = _engine()
    m = _m()
    t = paper.Tracked(m, None)
    eng._apply("ferrarichampions2026", bots.BOTS["ferrarichampions2026"], t,
               _book(0.40, 0.45, traded=500), 0.5, [bots.Rest("long", 0.42, 4.2, "dominant")], NOW)
    assert len(led.orders) == 1 and led.orders[0].contracts == pytest.approx(10)
    eng._settle_orders(t, _book(0.40, 0.45, last=0.44, traded=520), NOW)   # trade above our bid
    assert not led.fills
    eng._settle_orders(t, _book(0.40, 0.45, last=0.42, traded=524), NOW)   # 4 traded at our price
    assert [f["contracts"] for f in led.fills] == [pytest.approx(4)]
    assert led.orders[0].contracts == pytest.approx(6)


def test_short_rest_fills_on_trades_at_or_above_its_offer():
    eng, led = _engine()
    t = paper.Tracked(_m(), None)
    eng._apply("gamblingisallyourneed", bots.BOTS["gamblingisallyourneed"], t,
               _book(0.40, 0.45, traded=0), 0.5, [bots.Rest("short", 0.55, 5.5, "quote")], NOW)
    eng._settle_orders(t, _book(0.40, 0.45, last=0.45, traded=3), NOW)     # offer at 0.45 hit
    assert led.fills[0]["side"] == "short" and led.fills[0]["contracts"] == pytest.approx(3)


# --- bots --------------------------------------------------------------------

def test_ferrari_enters_dominant_then_hedges_only_below_one_dollar():
    b, st = bots.BOTS["ferrarichampions2026"], {}
    m = _m(name="Manchester City")
    acts = b.step(_view(m, _book(0.60, 0.62), 0.66), st)
    assert acts and acts[0].side == "long" and acts[0].tag == "dominant"
    b.on_fill(st, bots.Fill("long", 0.62, 20, "dominant"), NOW)
    # Short side asks at 1 - 0.40 = 0.60: 0.62 + 0.60 > 1.00, so only a resting hedge at 0.97 - 0.62.
    acts = b.step(_view(m, _book(0.40, 0.41), 0.4, held=20, now=NOW + timedelta(minutes=5)), st)
    assert [(a.tag, type(a).__name__, a.price) for a in acts] == [("hedge", "Rest", 0.35)]
    # A crash makes the short side cheap: 1 - 0.70 = 0.30, pair 0.92 < 1.00 -> take it.
    acts = b.step(_view(m, _book(0.70, 0.72), 0.7, held=20, now=NOW + timedelta(minutes=6)), st)
    assert acts[0].tag == "hedge" and isinstance(acts[0], bots.Take)
    # After 15 minutes the hedge window closes.
    assert b.step(_view(m, _book(0.70, 0.72), 0.7, held=20, now=NOW + timedelta(minutes=16)), st) == []


def test_ferrari_skips_nhl():
    m = _m(league="nhl", event="nhl-a-b-2026-11-17", title="A vs. B")
    assert bots.BOTS["ferrarichampions2026"].step(_view(m, _book(0.5, 0.52), 0.6), {}) == []


def test_randomgamebet_is_live_esports_only_and_hedges_below_0975():
    b = bots.BOTS["randomgamebet"]
    m = _m(league="cs2", name="Hype Tech", event="cs2-ht-tlr-2026-11-17",
           title="Hype Tech vs. The Last Resort", start=NOW - timedelta(minutes=30))
    assert not b.wants(m, live=False) and b.wants(m, live=True)
    st = {}
    acts = b.step(_view(m, _book(0.55, 0.56), 0.60, live=True), st)
    assert acts[0].tag == "dominant" and acts[0].dollars == 30   # edge 4c -> 2x tier ($30)
    assert b.step(_view(m, _book(0.55, 0.56), 0.62, live=True), {})[0].dollars == 50  # 6c -> 3x
    b.on_fill(st, bots.Fill("long", 0.56, 53.57, "dominant"), NOW)
    # Other side asks 1 - 0.60 = 0.40: pair 0.96 < 0.975, take the hedge now.
    acts = b.step(_view(m, _book(0.60, 0.61), 0.6, live=True, held=53.57), st)
    assert isinstance(acts[0], bots.Take) and acts[0].limit == pytest.approx(0.415)
    # Other side asks 1 - 0.50 = 0.50: pair 1.06, so rest a hedge bid at 0.97 - 0.56.
    acts = b.step(_view(m, _book(0.50, 0.51), 0.5, live=True, held=53.57), st)
    assert isinstance(acts[0], bots.Rest) and acts[0].price == pytest.approx(0.41)


def test_swisstony_probes_then_sizes_anchor_by_edge_and_never_buys_nba():
    b, st = bots.BOTS["swisstony"], {}
    m = _m(name="Manchester City")
    a1 = b.step(_view(m, _book(0.55, 0.56), 0.62), st)
    assert a1[0].tag == "probe" and a1[0].dollars == 5
    for _ in range(2):
        b.on_fill(st, bots.Fill("long", 0.56, 9, "probe"), NOW)
    a3 = b.step(_view(m, _book(0.55, 0.56), 0.62, held=18), st)
    kelly = 1000 * 0.25 * (0.62 - 0.56) / (1 - 0.56)
    assert a3[0].tag == "anchor" and a3[0].dollars == pytest.approx(min(kelly, 50 - 2 * 9 * 0.56))
    nba = _m(**{**NBA_EVENT, "name": "Knicks"})
    assert b.step(_view(nba, _book(0.55, 0.56), 0.62), {}) == []


def test_sinenooneei_anchor_cap_and_live_confirmation():
    b = bots.BOTS["sinenooneei"]
    m = _m(league="cs2", name="Hype Tech", event="cs2-ht-tlr-2026-11-17",
           title="Hype Tech vs. The Last Resort", start=NOW - timedelta(minutes=20))
    st = {}
    # Live entry needs the edge on the previous poll too.
    assert b.step(_view(m, _book(0.50, 0.51), 0.60, prev=0.50, live=True), st) == []
    acts = b.step(_view(m, _book(0.50, 0.51), 0.60, prev=0.59, live=True), st)
    assert acts[0].tag == "probe" and acts[0].dollars == pytest.approx(5 * 0.5)  # esports half
    for _ in range(3):
        b.on_fill(st, bots.Fill("long", 0.51, 5, "probe"), NOW)
    # No anchor above $0.80.
    acts = b.step(_view(m, _book(0.84, 0.85), 0.92, prev=0.92, live=True, held=15), st)
    assert all(a.tag != "anchor" for a in acts)


def test_kch123_bursts_small_buys_on_football_only():
    b = bots.BOTS["kch123"]
    nfl = _m(league="nfl", kind="spreads", name="IND Colts", line=13.5,
             event="nfl-ind-hou-2026-11-19", title="IND Colts vs. HOU Texans")
    acts = b.step(_view(nfl, _book(0.50, 0.51), 0.58), {})
    assert acts[0].tag == "probe" and acts[0].dollars == 5
    assert b.step(_view(_m(**{**NBA_EVENT, "name": "Knicks"}), _book(0.5, 0.51), 0.58), {}) == []


def test_geiyecapixie_buys_building_lead_and_takes_rally_profit():
    b, st = bots.BOTS["geiyecapixie"], {}
    m = _m(league="lol", name="BLG", event="lol-blg-nip-2026-11-17", title="BLG vs. NIP",
           start=NOW - timedelta(minutes=15))
    acts = b.step(_view(m, _book(0.60, 0.61), 0.70, prev=0.66, live=True), st)
    assert acts[0].tag == "clip" and acts[0].dollars == 30   # fair 0.70 -> $30 tier
    b.on_fill(st, bots.Fill("long", 0.61, 49, "clip"), NOW)
    acts = b.step(_view(m, _book(0.77, 0.78), 0.78, prev=0.78, live=True, held=49), st)
    assert acts[0].tag == "rally_exit" and acts[0].side == "short"   # bid 0.77 >= 0.61 + 0.15
    assert b.step(_view(m, _book(0.6, 0.61), 0.70, prev=0.66, live=True,
                        now=NOW.replace(hour=20)), {}) == []          # outside 09-14 UTC


def test_gambling_quotes_both_sides_and_exits_what_it_holds():
    b = bots.BOTS["gamblingisallyourneed"]
    m = _m(name="Manchester City", start=NOW + timedelta(hours=5))
    acts = b.step(_view(m, _book(0.60, 0.66), 0.65, now=NOW.replace(hour=15)), {})
    assert {a.side for a in acts} == {"long", "short"}
    held = b.step(_view(m, _book(0.60, 0.66), 0.65, held=-10, now=NOW.replace(hour=15)), {})
    exit_long = [a for a in held if a.side == "long"][0]
    assert exit_long.dollars == pytest.approx(10 * exit_long.price)


def test_probe_bots_skip_long_shots():
    # Observed 2026-11-17: swisstony bought 320 contracts at $0.01 on a 2.45% fair value.
    m = _m(league="cbb", event="cbb-sacred-prov-2026-11-17", title="Sacred Heart vs. Providence")
    assert bots.BOTS["swisstony"].step(_view(m, _book(None, 0.01), 0.0245), {}) == []


def test_randomgamebet_precheck_skips_unhedgeable_entries():
    # Observed 2026-11-17: dominant buys at $0.97-0.98 can never pair below $0.975.
    b = bots.BOTS["randomgamebet"]
    m = _m(league="cs2", name="WBT", event="cs2-wba-eex-2026-11-17", title="WBT vs. ex-ENCE",
           start=NOW - timedelta(hours=1))
    assert b.step(_view(m, _book(0.97, 0.98), 0.985, live=True), {}) == []


def test_unmarked_open_position_is_carried_at_cost():
    led = paper.Ledger()
    led.record("swisstony", "m", "long", 0.01, 320, 0.30, "taker", "probe", None, NOW)
    out = paper.report(led)
    row = [r for r in out.splitlines() if r.startswith("| swisstony")][0]
    assert "$999.70" in row        # bankroll minus the fee, no phantom mark at 0.50
