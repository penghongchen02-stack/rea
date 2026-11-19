from datetime import datetime, timedelta, timezone

import pytest

from psb import bots, fees, paper, reference, registry
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


def test_resting_order_that_closes_a_position_reserves_nothing():
    led = paper.Ledger()
    led.record("b", "m", "long", 0.60, 10, 0.0, "taker", "x", None, NOW)
    assert led.available_cash("b") == pytest.approx(994)
    led.orders.append(paper.Order("b", "m", "short", 0.35, 10, "exit", "t", 0))
    assert led.available_cash("b") == pytest.approx(994)
    led.orders.append(paper.Order("b", "m", "short", 0.35, 30, "exit", "t", 0))   # flips 20 short
    # 20 new shorts lock 20 * 0.35 = 7.00; cashing out the 10 longs at 0.65 returns 6.50
    assert led.available_cash("b") == pytest.approx(994 - 0.5)


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
    eng._apply("swisstony", registry.BOTS["swisstony"], t, bk, 0.6,
               [bots.Take("long", 100.0, 0.45, "probe")], NOW)
    got = [(f["price"], f["contracts"]) for f in led.fills]
    assert got == [(0.40, 10), (0.41, 10)]
    assert led.position("swisstony", m.slug).fees == pytest.approx(
        fees.taker_fee(10, 0.40, 0.095) + fees.taker_fee(10, 0.41, 0.095))


def test_rest_fills_only_on_trades_through_its_price_after_placement():
    eng, led = _engine()
    m = _m()
    t = paper.Tracked(m, None)
    eng._apply("ferrarichampions2026", registry.BOTS["ferrarichampions2026"], t,
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
    eng._apply("gamblingisallyourneed", registry.BOTS["gamblingisallyourneed"], t,
               _book(0.40, 0.45, traded=0), 0.5, [bots.Rest("short", 0.55, 5.5, "quote")], NOW)
    eng._settle_orders(t, _book(0.40, 0.45, last=0.45, traded=3), NOW)     # offer at 0.45 hit
    assert led.fills[0]["side"] == "short" and led.fills[0]["contracts"] == pytest.approx(3)


# --- bots --------------------------------------------------------------------

def test_ferrari_enters_dominant_then_hedges_only_below_one_dollar():
    b, st = registry.BOTS["ferrarichampions2026"], {}
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
    assert registry.BOTS["ferrarichampions2026"].step(_view(m, _book(0.5, 0.52), 0.6), {}) == []


def test_randomgamebet_is_live_esports_only_and_hedges_below_0975():
    b = registry.BOTS["randomgamebet"]
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
    b, st = registry.BOTS["swisstony"], {}
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
    b = registry.BOTS["sinenooneei"]
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
    b = registry.BOTS["kch123"]
    nfl = _m(league="nfl", kind="spreads", name="IND Colts", line=13.5,
             event="nfl-ind-hou-2026-11-19", title="IND Colts vs. HOU Texans")
    acts = b.step(_view(nfl, _book(0.50, 0.51), 0.58), {})
    assert acts[0].tag == "probe" and acts[0].dollars == 5
    assert b.step(_view(_m(**{**NBA_EVENT, "name": "Knicks"}), _book(0.5, 0.51), 0.58), {}) == []


def test_gambling_quotes_both_sides_and_exits_what_it_holds():
    b = registry.BOTS["gamblingisallyourneed"]
    m = _m(name="Manchester City", start=NOW + timedelta(hours=5))
    acts = b.step(_view(m, _book(0.60, 0.66), 0.65, now=NOW.replace(hour=15)), {})
    assert {a.side for a in acts} == {"long", "short"}
    held = b.step(_view(m, _book(0.60, 0.66), 0.65, held=-10, now=NOW.replace(hour=15)), {})
    exit_long = [a for a in held if a.side == "long"][0]
    assert exit_long.dollars == pytest.approx(10 * exit_long.price)


def test_probe_bots_skip_long_shots():
    # Observed 2026-11-17: swisstony bought 320 contracts at $0.01 on a 2.45% fair value.
    m = _m(league="cbb", event="cbb-sacred-prov-2026-11-17", title="Sacred Heart vs. Providence")
    assert registry.BOTS["swisstony"].step(_view(m, _book(None, 0.01), 0.0245), {}) == []


def test_randomgamebet_precheck_skips_unhedgeable_entries():
    # Observed 2026-11-17: dominant buys at $0.97-0.98 can never pair below $0.975.
    b = registry.BOTS["randomgamebet"]
    m = _m(league="cs2", name="WBT", event="cs2-wba-eex-2026-11-17", title="WBT vs. ex-ENCE",
           start=NOW - timedelta(hours=1))
    assert b.step(_view(m, _book(0.97, 0.98), 0.985, live=True), {}) == []


def test_unmarked_open_position_is_carried_at_cost():
    led = paper.Ledger()
    led.record("swisstony", "m", "long", 0.01, 320, 0.30, "taker", "probe", None, NOW)
    out = paper.report(led)
    row = [r for r in out.splitlines() if r.startswith("| swisstony")][0]
    assert "$999.70" in row        # bankroll minus the fee, no phantom mark at 0.50


def test_swisstony_skips_esports():
    m = _m(league="cs2", name="ICE", event="cs2-ice-bca-2026-11-17", title="ICE vs. BCA")
    assert registry.BOTS["swisstony"].step(_view(m, _book(0.60, 0.61), 0.70), {}) == []


def test_orders_from_a_previous_run_do_not_survive_restart():
    # Observed 2026-11-17: an order placed at 14:34 was still resting at 23:34 and
    # could have filled on nine hours of trades nobody watched.
    led = paper.Ledger()
    led.orders.append(paper.Order("b", "m", "long", 0.40, 10, "x", "t", 0))
    paper.Engine(led, [], fees.WORST_TAKER_THETA, _Log(), 24)
    assert led.orders == [] and led.available_cash("b") == pytest.approx(1000)


def test_orders_are_cancelled_when_their_market_is_dropped(monkeypatch):
    eng, led = _engine()
    m = _m()
    eng.markets[m.slug] = paper.Tracked(m, None)
    led.orders.append(paper.Order("ferrarichampions2026", m.slug, "long", 0.40, 10, "x", "t", 0))
    monkeypatch.setattr(paper.usmarket, "discover", lambda lg, lines, **kw: [])
    eng.leagues = ["epl"]
    eng.discover()
    assert m.slug not in eng.markets and led.orders == []


def test_netted_position_is_settled_and_valued_at_its_locked_cash(monkeypatch):
    led = paper.Ledger()
    led.record("b", "m", "long", 0.02, 500, 0.0, "maker", "quote", None, NOW)
    led.record("b", "m", "short", 0.96, 500, 0.0, "maker", "quote", None, NOW)   # pair $0.98
    eng = paper.Engine(led, [], fees.WORST_TAKER_THETA, _Log(), 24)
    monkeypatch.setattr(paper.usmarket, "settlement", lambda slug: 0.0)
    assert eng.settle() == 1 and led.settled == {"m": 0.0}


def test_unmarked_partly_closed_position_keeps_its_realized_cash():
    led = paper.Ledger()
    led.record("swisstony", "m", "long", 0.50, 20, 0.0, "taker", "x", None, NOW)
    led.record("swisstony", "m", "short", 0.40, 10, 0.0, "taker", "x", None, NOW)   # 10 pairs at $0.90
    # 10 longs left, carried at their 0.50 entry, plus $1.00 locked in on the pairs.
    row = [r for r in paper.report(led).splitlines() if r.startswith("| swisstony")][0]
    assert "$1,001.00" in row


# --- fixed (-v2) bots ----------------------------------------------------------

def test_every_v2_bot_keeps_its_original_entry_filters():
    for b in bots.FIXED:
        assert b.rules_key == b.key[:-3] and b.rules_key in registry.BOTS


def test_ferrari_v2_trades_one_line_per_game_and_caps_dominance():
    old, new = registry.BOTS["ferrarichampions2026"], registry.BOTS["ferrarichampions2026-v2"]
    m = _m(name="Manchester City")
    # 10c raw edge: the original clips 5x ($50), the fix at most 2.5x ($25).
    assert old.step(_view(m, _book(0.55, 0.56), 0.66), {})[0].dollars == 50
    assert new.step(_view(m, _book(0.55, 0.56), 0.66), {})[0].dollars == 25
    # Another line of the same game already traded: no new entry.
    v = _view(m, _book(0.55, 0.56), 0.66)
    v.siblings = 1
    assert new.step(v, {}) == [] and old.step(v, {}) != []


def test_engine_counts_other_lines_of_the_same_game():
    # Observed 2026-11-18: over 232.5 and 234.5 plus under 220.5-222.5 on one game.
    eng, led = _engine()
    a = _m(kind="totals", name="Over", line=232.5, slug="tot-232", **NBA_EVENT)
    b = _m(kind="totals", name="Over", line=234.5, slug="tot-234", **NBA_EVENT)
    ml = _m(name="Knicks", slug="ml", **NBA_EVENT)
    key = "ferrarichampions2026-v2"
    st = led.state(key, a.slug)
    st.update(event=a.event_slug, kind="totals", legs={"long": {"contracts": 10, "cost": 5}})
    assert eng.siblings(key, b) == 1 and eng.siblings(key, a) == 0 and eng.siblings(key, ml) == 0


def test_gambling_v2_skips_extreme_prices_and_never_closes_above_its_pair_cap():
    old, new = registry.BOTS["gamblingisallyourneed"], registry.BOTS["gamblingisallyourneed-v2"]
    m = _m(name="Fulham", start=NOW + timedelta(hours=5))
    at = NOW.replace(hour=15)
    # Fair 3.5c: the original quotes the long shot at 1.9c (rounded 2c), the fix does not.
    assert any(a.side == "long" for a in old.step(_view(m, _book(0.01, 0.05), 0.035, now=at), {}))
    assert all(a.side != "long" for a in new.step(_view(m, _book(0.01, 0.05), 0.035, now=at), {}))
    # Holding 10 short bought at 0.60: the closing long bid is at most 0.985 - 0.60 - 0.005.
    st = {"legs": {"short": {"contracts": 10, "cost": 6.0}}}
    acts = new.step(_view(m, _book(0.30, 0.50), 0.45, held=-10, now=at), st)
    close = [a for a in acts if a.side == "long"][0]
    assert close.price == pytest.approx(0.38) and close.price + 0.60 <= 0.985
    assert [a for a in old.step(_view(m, _book(0.30, 0.50), 0.45, held=-10, now=at), st)
            if a.side == "long"][0].price == pytest.approx(0.43)


def test_swisstony_v2_needs_two_cents_after_fee():
    m = _m(league="cbb", event="cbb-a-b-2026-11-17", title="A vs. B")
    # Ask 0.56, fee ~2.3c, fair 0.595: ~1.2c edge after fee.
    assert registry.BOTS["swisstony"].step(_view(m, _book(0.55, 0.56), 0.595), {})
    assert registry.BOTS["swisstony-v2"].step(_view(m, _book(0.55, 0.56), 0.595), {}) == []
    assert registry.BOTS["kch123"].step(_view(m, _book(0.55, 0.56), 0.595), {})


def test_sinenooneei_v2_anchors_small_and_only_after_a_real_move():
    b = registry.BOTS["sinenooneei-v2"]
    m = _m(league="cs2", name="Hype Tech", event="cs2-ht-tlr-2026-11-17",
           title="Hype Tech vs. The Last Resort", start=NOW - timedelta(minutes=20))
    st = {"thesis": "long", "thesis_fair": 0.60}
    for _ in range(3):
        b.on_fill(st, bots.Fill("long", 0.51, 5, "probe"), NOW)
    # Fair up 1c only: probe again, no anchor.
    acts = b.step(_view(m, _book(0.50, 0.51), 0.61, prev=0.61, live=True, held=15), st)
    assert acts and acts[0].tag == "probe"
    # Fair up 5c with 10c+ edge: anchor of 5x probe, half size for esports.
    acts = b.step(_view(m, _book(0.50, 0.51), 0.65, prev=0.65, live=True, held=15), st)
    assert acts[0].tag == "anchor" and acts[0].dollars == pytest.approx(25 * 0.5)


def test_randomgamebet_v2_caps_the_unhedged_dominant_leg():
    m = _m(league="cs2", name="Hype Tech", event="cs2-ht-tlr-2026-11-17",
           title="Hype Tech vs. The Last Resort", start=NOW - timedelta(minutes=30))
    v = _view(m, _book(0.55, 0.56), 0.62, live=True)
    assert registry.BOTS["randomgamebet"].step(v, {})[0].dollars == 50
    assert registry.BOTS["randomgamebet-v2"].step(v, {})[0].dollars == 15


def test_bots_added_later_get_their_own_start_time():
    led = paper.Ledger(started_at="2026-11-17T11:03:25Z")
    led.record("kch123", "m", "long", 0.5, 10, 0.0, "taker", "probe", 0.55, NOW)
    paper.Engine(led, [], fees.WORST_TAKER_THETA, _Log(), 24)
    assert set(led.bot_started) == set(registry.BOTS)
    assert led.bot_started["kch123"] == "2026-11-17T11:03:25Z"      # traded since the start
    assert led.bot_started["swisstony-v2"] > "2026-11-17T11:03:25Z"  # joined now


def test_us_itf_leagues_link_to_the_international_itf_league():
    # Observed 2026-11-18: 58 live ITF matches, none linked ("itfme" vs "itf").
    m = _m(league="itfme", event="itfme-piebai-saviva-2026-11-18", name="Piero Bailey",
           title="Piero Bailey vs. Savio Viva", teams=("Piero Bailey", "Savio Viva"))
    ev = {"slug": "itf-bailey1-viva1-2026-11-18", "startTime": m.start.isoformat(),
          "title": "M15 Monastir: Piero Bailey vs Savio Viva", "markets": []}
    assert reference.same_game(m, ev)
    assert bots.View(m, _book(0.5, 0.52), 0.6, None, False, NOW, 1000, 1000, 0,
                     fees.WORST_TAKER_THETA).category == "tennis"


def test_other_league_codes_link_when_teams_and_date_match():
    # Observed 2026-11-18: US "jpbl-chi-aki-..." is international "bkjpn-chi-aki-...".
    m = _m(league="jpbl", event="jpbl-chi-aki-2026-11-18", name="Chiba Jets",
           title="Chiba Jets vs. Akita Northern Happinets",
           teams=("Chiba Jets", "Akita Northern Happinets"))
    ev = {"slug": "bkjpn-chi-aki-2026-11-18", "startTime": m.start.isoformat(),
          "title": "Chiba Jets vs. Akita Northern Happinets", "markets": []}
    assert reference.same_game(m, ev)
    assert not reference.same_game(m, {**ev, "slug": "bkjpn-chi-kaw-2026-11-18",
                                       "title": "Chiba Jets vs. Kawasaki Brave Thunders"})


def test_refreshing_one_league_leaves_the_others_and_an_outage_keeps_markets(monkeypatch):
    eng, led = _engine()
    epl, nba = _m(), _m(name="Knicks", **NBA_EVENT)
    eng.markets = {epl.slug: paper.Tracked(epl, None), nba.slug: paper.Tracked(nba, None)}

    def down(lg, lines, **kw):
        raise usmarket_error("gateway down")
    usmarket_error = RuntimeError
    monkeypatch.setattr(paper.usmarket, "discover", down)
    eng.discover_league("epl")
    assert set(eng.markets) == {epl.slug, nba.slug}
    monkeypatch.setattr(paper.usmarket, "discover", lambda lg, lines, **kw: [])
    eng.discover_league("epl")
    assert set(eng.markets) == {nba.slug}


# --- the 50 simple ideas ---------------------------------------------------------

from psb import simple_bots


def test_ideas_cover_1_to_50_and_each_running_idea_has_a_bot():
    assert [i.n for i in simple_bots.IDEAS] == list(range(1, 51))
    for i in simple_bots.IDEAS:
        assert (i.key in registry.BOTS) == (i.status in ("running", "proxy"))
    assert not any(i.key for i in simple_bots.IDEAS if i.status == "removed")


@pytest.mark.parametrize("bot", simple_bots.RUNNING, ids=lambda b: b.key)
def test_every_simple_bot_steps_without_error_and_respects_its_limits(bot):
    m_live = _m(league="atp", event="atp-a-b-2026-11-18", name="A", title="A vs. B",
                teams=("A", "B"), start=NOW - timedelta(minutes=30))
    m_pre = _m(start=NOW + timedelta(hours=14))
    for m in (m_live, m_pre):
        for bid, ask, fair, prev in [(0.50, 0.52, 0.60, 0.55), (0.30, 0.31, 0.25, 0.33),
                                     (0.95, 0.96, 0.98, 0.97), (0.48, 0.56, 0.52, None)]:
            st = {}
            v = _view(m, _book(bid, ask, traded=10000), fair, prev=prev, live=m is m_live)
            v.stats = {"pnl_today": 0.0, "last_results": [], "open_by_cat": {}}
            if not bot.wants(m, m is m_live):
                continue
            for a in bot.step(v, st):
                assert a.dollars <= 1000 * bot.max_market_frac + 1e-9
                if isinstance(a, bots.Take):
                    assert a.limit <= v.fair(a.side) + 1e-9 or a.tag == "second"


def test_flat_bot_buys_once_with_edge_and_skips_without():
    b = registry.BOTS["s39-flat-10"]
    m = _m(name="Manchester City")
    st = {}
    acts = b.step(_view(m, _book(0.50, 0.52), 0.60), st)
    assert acts and acts[0].dollars == 10 and acts[0].side == "long"
    b.on_fill(st, bots.Fill("long", 0.52, 19, "entry"), NOW)
    assert b.step(_view(m, _book(0.50, 0.52), 0.60), st) == []          # once per market
    assert b.step(_view(m, _book(0.50, 0.52), 0.53), {}) == []          # no edge after fee


def test_daily_stop_and_sport_budget_use_the_bots_own_results():
    m = _m(name="Manchester City")
    v = _view(m, _book(0.50, 0.52), 0.60)
    v.stats = {"pnl_today": -60.0, "last_results": [], "open_by_cat": {"soccer": 200.0}}
    assert registry.BOTS["s40-daily-stop"].step(v, {}) == []
    assert registry.BOTS["s42-sport-budget"].step(v, {}) == []
    v.stats = {"pnl_today": 0.0, "last_results": [5, 5, 5], "open_by_cat": {}}
    assert registry.BOTS["s41-streak-cooler"].step(v, {})[0].dollars == 5


def test_sequential_pair_buys_the_second_side_only_under_98c():
    b, st = registry.BOTS["s07-underpriced-pair"], {}
    m = _m(name="Manchester City")
    assert b.step(_view(m, _book(0.50, 0.52), 0.55), st)[0].tag == "first"
    b.on_fill(st, bots.Fill("long", 0.52, 19, "first"), NOW)
    # Short asks 1 - 0.50 = 0.50: pair 1.02 -> wait.
    assert b.step(_view(m, _book(0.50, 0.52), 0.5, held=19), st) == []
    # Short asks 1 - 0.58 = 0.42: pair 0.94 + fee -> buy it.
    assert b.step(_view(m, _book(0.58, 0.60), 0.6, held=19), st)[0].tag == "second"


def test_a_buy_never_walks_the_book_past_the_price_cap():
    # Observed 2026-11-18: swisstony filled a level at 0.96 after checking a 0.94 ask.
    m = _m(league="cbb", event="cbb-a-b-2026-11-17", title="A vs. B")
    acts = registry.BOTS["swisstony"].step(_view(m, _book(0.93, 0.94), 0.995), {})
    assert acts and acts[0].limit <= 0.95
    acts = registry.BOTS["s39-flat-10"].step(_view(_m(), _book(0.93, 0.94), 0.995), {})
    assert acts and acts[0].limit <= 0.95


def test_audit_flags_a_buy_above_fair_and_passes_a_clean_ledger(tmp_path):
    from psb import audit
    led = paper.Ledger()
    led.record("kch123", "m", "long", 0.50, 10, 0.1, "taker", "probe", 0.55, NOW)
    assert audit.audit(led, tmp_path)[0] == []
    led.record("kch123", "m", "long", 0.60, 10, 0.1, "taker", "probe", 0.55, NOW)
    assert any("above fair" in p for p in audit.audit(led, tmp_path)[0])


def test_poll_reads_live_games_first_and_never_acts_on_a_stale_price(monkeypatch):
    eng, led = _engine()
    live_m = _m(name="Live", slug="live", start=NOW - timedelta(minutes=10))
    pre = [_m(name=f"P{i}", slug=f"pre{i}", start=NOW + timedelta(hours=3)) for i in range(40)]
    link = reference.Link({"clobTokenIds": '["t0", "t1"]'}, 0, False)
    for m in [*pre, live_m]:
        eng.markets[m.slug] = paper.Tracked(m, link)
    order = []
    monkeypatch.setattr(paper, "_now", lambda: NOW)
    monkeypatch.setattr(paper.reference, "live_mids", lambda links: {"t0": 0.6})
    monkeypatch.setattr(paper.usmarket, "book", lambda slug: order.append(slug) or _book(0.5, 0.52))
    monkeypatch.setattr(paper.livesim.time, "sleep", lambda s: None)
    eng.poll(5, 60)
    assert order[0] == "live"                                   # live first
    # Upcoming ones are capped (live-like bots re-read a book they trade, so count markets).
    assert len(set(order)) == 1 + paper.PREGAME_PER_POLL
    # A price read longer ago than MAX_FAIR_AGE is not acted on.
    clock = iter([0.0] + [100.0] * 50)
    monkeypatch.setattr(paper.time, "monotonic", lambda: next(clock))
    eng.markets = {"live": paper.Tracked(live_m, link)}
    led.fills.clear()
    eng._poll_group([eng.markets["live"]])
    assert led.fills == [] and eng.stats["stale"] == 1


# --- higher-volume versions of #19, #15 and swisstony ------------------------------

from psb import variants


def test_ten_versions_each_with_unique_keys_and_the_base_filters():
    assert len(variants.S19) == len(variants.S15) == len(variants.SWISS) == 10
    keys = [b.key for b in variants.VARIANTS]
    assert len(set(keys)) == 30 and all(k in registry.BOTS for k in keys)
    m_nba = _m(**{**NBA_EVENT, "name": "Knicks"})
    v = _view(m_nba, _book(0.55, 0.56), 0.62)
    assert registry.BOTS["swisstony-x01"].step(v, {}) == []          # swisstony skips the NBA
    assert registry.BOTS["swisstony-x05"].step(v, {})                # the all-sports version trades it


def test_looser_versions_trade_where_the_base_does_not():
    m = _m(league="atp", event="atp-a-b-2026-11-18", name="A", title="A vs. B", teams=("A", "B"),
           start=NOW - timedelta(minutes=30))
    st = {"fair0": 0.60}
    v = _view(m, _book(0.45, 0.46), 0.50, live=True)                  # 10c fall, 4c+ edge
    assert registry.BOTS["s19-tennis-set-fade"].step(v, dict(st)) == []   # needs 15c
    assert registry.BOTS["s19-x01"].step(v, dict(st))                     # 10c is enough
    v = _view(m, _book(0.45, 0.46), 0.52, prev=0.50, live=True)       # 2c jump
    assert registry.BOTS["s15-momentum"].step(v, {}) == []                # needs 3c
    assert registry.BOTS["s15-x01"].step(v, {})


# --- live-like copies -----------------------------------------------------------

def test_live_like_copy_misses_a_price_that_is_gone_when_its_order_arrives(monkeypatch):
    from psb import livesim
    eng, led = _engine()
    bot = registry.BOTS["Live_19"]
    m = _m(league="atp", event="atp-a-b-2026-11-18", name="A", title="A vs. B", teams=("A", "B"),
           start=NOW - timedelta(minutes=30))
    t = paper.Tracked(m, None)
    monkeypatch.setattr(livesim.time, "sleep", lambda s: None)
    monkeypatch.setattr(livesim, "SHARE_READ_S", 0.0)   # each order below is sent separately
    # The 12c offer is gone when the order arrives: nothing fills.
    monkeypatch.setattr(livesim.usmarket, "book", lambda slug: _book(0.30, 0.40))
    eng._apply("Live_19", bot, t, _book(0.10, 0.12), 0.20,
               [bots.Take("long", 10.0, 0.15, "entry")], NOW)
    c = livesim.counts(led, "Live_19")
    assert led.fills == [] and c["missed"] == 1 and c["missed_price_gone"] == 1
    # Still there: whole contracts, the bot's own $10 size, half the shown size.
    monkeypatch.setattr(livesim.usmarket, "book", lambda slug: _book(0.10, 0.12, qty=200))
    eng._apply("Live_19", bot, t, _book(0.10, 0.12), 0.20,
               [bots.Take("long", 10.0, 0.15, "entry")], NOW)
    assert [f["contracts"] for f in led.fills] == [66]          # $10 / 0.15 = 66, half of 200 shown
    # One contract shown: half rounds to none, counted as lost to competition.
    monkeypatch.setattr(livesim.usmarket, "book", lambda slug: _book(0.10, 0.12, qty=1))
    eng._apply("Live_19", bot, t, _book(0.10, 0.12), 0.20,
               [bots.Take("long", 10.0, 0.15, "entry")], NOW)
    assert livesim.counts(led, "Live_19")["missed_competition"] == 1
    assert led.fills[0]["contracts"] == int(led.fills[0]["contracts"])


def test_old_live_like_keys_move_to_the_live_names():
    from psb import livesim
    led = paper.Ledger()
    led.record("s19-livesim", "m", "long", 0.12, 40, 0.0, "taker", "entry", 0.15, NOW)
    led.state("s19-livesim", "m")["fair0"] = 0.3
    led.bot_started["s19-livesim"] = "2026-11-18T17:40:13Z"
    assert livesim.migrate_keys(led) == 1
    assert "s19-livesim" not in led.positions and led.positions["Live_19"]["m"].contracts == 40
    assert led.states["Live_19"]["m"]["fair0"] == 0.3 and led.fills[0]["bot"] == "Live_19"
    assert led.bot_started["Live_19"] == "2026-11-18T17:40:13Z"


# --- profit lock-in versions of #19 ---------------------------------------------

def _held_long(n=100.0, price=0.12):
    return {"fair0": 0.30, "legs": {"long": {"contracts": n, "cost": n * price}},
            "fills_by_tag": {"entry": 1}}


def _tennis():
    return _m(league="atp", event="atp-a-b-2026-11-18", name="A", title="A vs. B",
              teams=("A", "B"), start=NOW - timedelta(minutes=30))


def test_lock70_buys_the_other_player_once_the_held_one_reaches_70c():
    bot = registry.BOTS["s19-lock70"]
    st = _held_long()
    v = _view(_tennis(), _book(0.74, 0.76), 0.76, live=True, held=100.0)
    (a,) = bot.step(v, st)
    assert (a.side, a.tag, a.limit) == ("short", "lock", pytest.approx(0.26))
    assert a.dollars / a.limit == pytest.approx(100.0)        # exactly the held contracts
    assert st["locking"]
    # Fully locked: no new entries in this match, even on a fresh 15c fall.
    v = _view(_tennis(), _book(0.10, 0.11), 0.12, live=True, held=0.0)
    assert bot.step(v, st) == []


def test_lock70_holds_when_locking_would_not_fix_a_profit():
    st = _held_long(price=0.65)                                # paid 65c, other side costs 30c+
    v = _view(_tennis(), _book(0.69, 0.71), 0.72, live=True, held=100.0)
    assert registry.BOTS["s19-lock70"].step(v, st) == []
    assert not st.get("locking")


def test_trail_locks_only_after_a_10c_fall_from_the_peak():
    bot = registry.BOTS["s19-trail"]
    st = _held_long()
    assert bot.step(_view(_tennis(), _book(0.59, 0.61), 0.60, live=True, held=100.0), st) == []
    assert st["peak"] == pytest.approx(0.60)                   # rising: keep holding
    (a,) = bot.step(_view(_tennis(), _book(0.49, 0.51), 0.50, live=True, held=100.0), st)
    assert (a.side, a.tag) == ("short", "lock")


def test_lock_fills_count_as_exempt_in_the_audit(tmp_path):
    from psb import audit
    led = paper.Ledger(1000.0)
    led.record("s19-lock70", "m", "long", 0.12, 100, 0.0, "taker", "entry", 0.15, NOW)
    led.record("s19-lock70", "m", "short", 0.26, 100, 0.0, "taker", "lock", 0.24, NOW)
    problems, _ = audit.audit(led, tmp_path)
    assert problems == []


def test_copies_ordering_one_market_together_share_one_fresh_read(monkeypatch):
    from psb import livesim
    eng, led = _engine()
    m = _tennis()
    t = paper.Tracked(m, None)
    reads = []
    monkeypatch.setattr(livesim.usmarket, "book",
                        lambda slug: reads.append(slug) or _book(0.10, 0.12, qty=200))
    for key in ("Live_19", "Live_19-00", "Live_19-10"):
        eng._apply(key, registry.BOTS[key], t, _book(0.10, 0.12), 0.20,
                   [bots.Take("long", 10.0, 0.15, "entry")], NOW)
    assert reads == [m.slug]                                        # one read, three orders
    assert [f["contracts"] for f in led.fills] == [66, 66, 66]      # each its own half of the book


# --- forty Live_19 versions -----------------------------------------------------

def test_forty_live19_versions_and_a_control_with_their_own_settings():
    from psb import live19
    from psb import livesim
    keys = [b.key for b in live19.LIVE19]
    assert len(keys) == len(set(keys)) and all(k in registry.BOTS for k in keys)
    assert len(keys) + sum(k.startswith("Live_19-") and k != "Live_19-lock70" for k in livesim.PAUSED) == 41
    assert all(registry.BOTS[k].live_sim for k in keys)
    st = {"fair0": 0.30}
    v = _view(_tennis(), _book(0.11, 0.12), 0.15, live=True)         # 15c fall to a 12c ask
    assert registry.BOTS["Live_19-00"].step(v, dict(st))              # the control buys it
    assert live19._make(*live19.SPECS[23]).step(v, dict(st)) == []    # 15c-and-up skips it
    v = _view(_tennis(), _book(0.16, 0.17), 0.20, live=True)          # only a 10c fall
    assert registry.BOTS["Live_19-00"].step(v, dict(st)) == []
    assert live19._make(*live19.SPECS[1]).step(v, dict(st))           # the 10c version buys
    m_nba = _m(**{**NBA_EVENT, "name": "Knicks"}, start=NOW - timedelta(minutes=30))
    v = _view(m_nba, _book(0.11, 0.12), 0.15, live=True)
    assert registry.BOTS["Live_19-00"].step(v, dict(st)) == []        # tennis only
    assert registry.BOTS["Live_19-31"].step(v, dict(st))              # all sports


def test_stop_cuts_a_falling_bet_at_a_loss_and_then_stays_out():
    from psb import live19
    bot = live19._make(*live19.SPECS[19])                             # stop 15c under cost
    st = _held_long(price=0.40)
    assert bot.step(_view(_tennis(), _book(0.29, 0.31), 0.30, live=True, held=100.0), st) == []
    (a,) = bot.step(_view(_tennis(), _book(0.24, 0.26), 0.25, live=True, held=100.0), st)
    assert (a.side, a.tag, a.limit) == ("short", "stop", pytest.approx(0.76))
    assert st["locking"] == "stop"
    # Part-filled: the rest is still bought, at a loss, without a profit check.
    (a,) = bot.step(_view(_tennis(), _book(0.20, 0.22), 0.21, live=True, held=40.0), st)
    assert a.tag == "stop" and a.dollars / a.limit == pytest.approx(40.0)
    assert bot.step(_view(_tennis(), _book(0.10, 0.11), 0.12, live=True, held=0.0), st) == []


def test_live_copies_of_15_x06_and_swisstony_x01_keep_their_settings():
    from psb import livesim
    for twin, base in (("Live_15-x06", "s15-x06"), ("Live_swisstony-x01", "swisstony-x01")):
        if twin in livesim.PAUSED:
            continue
        a, b = registry.BOTS[twin], registry.BOTS[base]
        assert a.live_sim and livesim.BASE_OF[twin] == base and a.rules_key == b.rules_key
    assert registry.BOTS["Live_15-x06"].jump == 0.015 and registry.BOTS["Live_15-x06"].edge == 0.005
    assert registry.BOTS["swisstony-x01"].min_edge == 0.005


def test_underdog_buys_one_dollar_when_the_underdog_jumps_in_any_live_game():
    nba = _m(**{**NBA_EVENT, "name": "Knicks"}, start=NOW - timedelta(minutes=30))
    # Knicks fall 67c -> 65c: the other side, the underdog, jumps 33c -> 35c and is
    # offered at 1 - 0.68 = 32c.
    for key in ("underdog-1", "Live_underdog"):
        (a,) = registry.BOTS[key].step(_view(nba, _book(0.68, 0.69), 0.65, prev=0.67, live=True), {})
        assert a.side == "short" and a.dollars == pytest.approx(1.0) and a.limit <= 0.50
    bot = registry.BOTS["underdog-1"]
    assert not bot.wants(nba, False) and bot.wants(nba, True)                      # live only
    # The favourite jumping up is not bought.
    assert bot.step(_view(nba, _book(0.60, 0.61), 0.65, prev=0.63, live=True), {}) == []
    # A 1c move is not a jump.
    assert bot.step(_view(nba, _book(0.68, 0.69), 0.65, prev=0.66, live=True), {}) == []
    draw = _m(name="Draw", is_draw=True, start=NOW - timedelta(minutes=30))
    assert not bot.wants(draw, True)
    st = {"fills_by_tag": {"entry": 3}}
    assert bot.step(_view(nba, _book(0.68, 0.69), 0.65, prev=0.67, live=True), st) == []   # 3 a game


def test_tennis_makers_quote_only_tennis_and_prematch_bots_buy_only_before_play():
    from psb import tennis
    assert len(tennis.TENNIS_BOTS) == 7 and all(b.key in registry.BOTS for b in tennis.TENNIS_BOTS)
    pre = _m(league="atp", event="atp-a-b-2026-11-18", name="A", title="A vs. B",
             teams=("A", "B"), start=NOW + timedelta(hours=1))
    nba = _m(**{**NBA_EVENT, "name": "Knicks"})
    mk = registry.BOTS["tn-maker-2c"]
    acts = mk.step(_view(pre, _book(0.50, 0.60), 0.55), {})
    assert {(a.side, a.price) for a in acts} == {("long", 0.53), ("short", 0.43)}
    assert mk.step(_view(nba, _book(0.50, 0.60), 0.55), {}) == []         # tennis only
    assert not registry.BOTS["tn-maker-pre"].wants(_tennis(), True)        # before play only
    for key in ("tn-pre-1c", "Live_tn-pre-1c"):
        b = registry.BOTS[key]
        assert not b.wants(_tennis(), True) and b.wants(pre, False)
        (a,) = b.step(_view(pre, _book(0.48, 0.50), 0.55), {})            # 5c under fair
        assert a.side == "long" and a.tag == "entry"
    assert registry.BOTS["tn-pre-2c"].step(_view(pre, _book(0.53, 0.54), 0.55), {}) == []


def test_live_maker_waits_behind_others_at_its_price_and_gets_its_queue_share():
    eng, led = _engine()
    t = paper.Tracked(_tennis(), None)
    for key in ("tn-maker-2c", "Live_tn-maker-2c"):
        eng._apply(key, registry.BOTS[key], t, _book(0.40, 0.45, traded=500), 0.5,
                   [bots.Rest("long", 0.42, 4.2, "quote")], NOW)
    eng._settle_orders(t, _book(0.40, 0.45, last=0.42, traded=504), NOW)   # 4 traded at our price
    assert {f["bot"]: f["contracts"] for f in led.fills} == {"tn-maker-2c": pytest.approx(4)}
    eng._settle_orders(t, _book(0.40, 0.45, last=0.41, traded=514), NOW)   # 10 traded through it
    live = [f["contracts"] for f in led.fills if f["bot"] == "Live_tn-maker-2c"]
    assert live == [5.0]                                                    # half, whole contracts


def test_six_ideas_and_their_live_copies():
    from psb import ideas
    assert len(ideas.IDEAS6) == 6
    for b in ideas.IDEAS6:
        assert b.key in registry.BOTS and registry.BOTS[f"Live_{b.key}"].live_sim
    nba = _m(**{**NBA_EVENT, "name": "Knicks"}, start=NOW - timedelta(minutes=30))
    # Comeback fade: the Knicks (favourite) fall 67c -> 65c, offered at 62c.
    (a,) = registry.BOTS["fav-fade"].step(_view(nba, _book(0.61, 0.62), 0.65, prev=0.67, live=True), {})
    assert a.side == "long"
    # Late underdog: only once the game is late.
    late = _m(**{**NBA_EVENT, "name": "Knicks"}, start=NOW - timedelta(minutes=120))
    v = _view(late, _book(0.63, 0.64), 0.60, live=True)                  # underdog 40c, offered 37c
    assert registry.BOTS["late-underdog"].step(v, {})
    assert registry.BOTS["late-underdog"].step(_view(nba, _book(0.63, 0.64), 0.60, live=True), {}) == []
    # Big jump needs 5c.
    assert registry.BOTS["big-jump"].step(_view(nba, _book(0.60, 0.61), 0.65, prev=0.62, live=True), {}) == []
    assert registry.BOTS["big-jump"].step(_view(nba, _book(0.60, 0.61), 0.68, prev=0.62, live=True), {})
    # Set loser: favourite at 70c before play, 45c now and offered at 40c.
    bot, st = registry.BOTS["tn-set-loser"], {}
    pre = _m(league="atp", event="atp-a-b-2026-11-18", name="A", title="A vs. B",
             teams=("A", "B"), start=NOW + timedelta(hours=1))
    bot.step(_view(pre, _book(0.69, 0.71), 0.70), st)
    (a,) = bot.step(_view(_tennis(), _book(0.39, 0.40), 0.45, live=True), st)
    assert a.side == "long"
    # Pre-game underdog, then a lock once it is 15c above cost.
    bot = registry.BOTS["pre-dog-lock"]
    (a,) = bot.step(_view(pre, _book(0.27, 0.28), 0.32), {})
    assert a.side == "long"
    st = _held_long(price=0.28)
    (a,) = bot.step(_view(_tennis(), _book(0.44, 0.46), 0.45, live=True, held=100.0), st)
    assert a.tag == "lock"
    # x06 on ITF men's only.
    itf = _m(league="itfme", event="itfme-a-b-2026-11-18", name="A", title="A vs. B",
             teams=("A", "B"), start=NOW - timedelta(minutes=30))
    v = lambda m: _view(m, _book(0.60, 0.61), 0.65, prev=0.63, live=True)
    assert registry.BOTS["x06-itfme"].step(v(itf), {}) and registry.BOTS["x06-itfme"].step(v(_tennis()), {}) == []


def _grid_views():
    """Varied live and pre-match views across sports, prices and moves."""
    import itertools
    out = []
    leagues = [("atp", "atp-a-b-2026-11-18"), ("itfme", "itfme-a-b-2026-11-18"),
               ("nba", "nba-ny-tor-2026-11-17"), ("epl", "epl-mnc-ful-2026-11-21")]
    for (lg, ev), start, fair, prev, ask, spread in itertools.product(
            leagues, (NOW + timedelta(hours=1), NOW - timedelta(minutes=30),
                      NOW - timedelta(minutes=130)),
            (0.20, 0.35, 0.45, 0.65, 0.80), (None, -0.06, -0.02, 0.02, 0.06), (-0.05, -0.02, 0.01),
            (0.01, 0.05)):
        m = _m(league=lg, event=ev, name="A", title="A vs. B", teams=("A", "B"), start=start)
        a = round(fair + ask, 2)
        if not 0.02 <= a <= 0.98:
            continue
        p = None if prev is None else round(fair - prev, 3)
        out.append(_view(m, _book(round(a - spread, 2), a), fair, prev=p, live=start <= NOW))
    return out


def test_every_live_copy_decides_exactly_like_its_paper_bot():
    import copy
    from psb import livesim
    views = _grid_views()
    for twin, base in livesim.BASE_OF.items():
        a, b = registry.BOTS[twin], registry.BOTS[base]
        assert a.live_sim and isinstance(a, type(b)), twin
        acted = 0
        for v in views:
            sa, sb = {"fair0": 0.6, "pre_fair": 0.7}, {"fair0": 0.6, "pre_fair": 0.7}
            assert a.wants(v.m, v.live) == b.wants(v.m, v.live), (twin, v.m.slug)
            if not b.wants(v.m, v.live):
                continue
            ra, rb = a.step(v, sa), b.step(v, sb)
            assert ra == rb, (twin, v)
            acted += bool(rb)
        if not twin.startswith(("Live_n", "Live_g", "Live_a")):   # these need context: see their own tests
            assert acted, f"{twin}: no test view made it act"


def test_every_live_copy_fills_through_the_engine(monkeypatch):
    from psb import livesim
    monkeypatch.setattr(livesim.usmarket, "book", lambda slug: _book(0.29, 0.30, qty=400))
    for twin in livesim.BASE_OF:
        eng, led = _engine()
        t = paper.Tracked(_tennis(), None)
        bot = registry.BOTS[twin]
        eng._apply(twin, bot, t, _book(0.29, 0.30), 0.40, [bots.Take("long", 10.0, 0.35, "entry")], NOW)
        eng._apply(twin, bot, t, _book(0.29, 0.30, traded=0), 0.40,
                   [bots.Rest("short", 0.60, 6.0, "quote")], NOW)
        eng._settle_orders(t, _book(0.29, 0.30, last=0.45, traded=40), NOW)   # through the 40c offer
        kinds = {f["kind"] for f in led.fills}
        assert kinds == {"taker", "maker"}, (twin, led.fills)
        assert all(f["contracts"] == int(f["contracts"]) for f in led.fills), twin


# --- the fifty (n01-n50): one scenario each that must make both copies act --------

def _ctx_view(m, bk, fair, prev=None, live=True, held=0.0, now=NOW, **ctx):
    v = _view(m, bk, fair, prev=prev, live=live, held=held, now=now)
    for k, val in ctx.items():
        setattr(v, k, val)
    return v


def _lvl(*levels):
    return tuple(levels)


def _bk(bids, asks, last=None, traded=None, oi=None):
    from psb.usmarket import Book
    return Book(bids, asks, "MARKET_STATE_OPEN", last, traded, oi)


def _ser(points, t0=None):
    """(seconds_ago, fair_long, extra...) -> series ending at NOW."""
    end = NOW.timestamp()
    out = []
    for p in points:
        ago, f = p[0], p[1]
        rest = list(p[2:]) + [None] * (6 - len(p[2:]))
        out.append((end - ago, f, *rest))
    return tuple(out)


def _scenarios():
    from psb.learn import band
    live = _tennis()                                           # ATP, 30 min in
    nba = _m(**{**NBA_EVENT, "name": "Knicks"}, start=NOW - timedelta(minutes=30))
    pre_t = _m(league="atp", event="atp-a-b-2026-11-18", name="A", title="A vs. B",
               teams=("A", "B"), start=NOW + timedelta(minutes=50))
    slug_t = lambda lg, st: _m(league=lg, event=f"{lg}-aaa-bbb-2026-11-18", name="A",
                               title="A vs. B", teams=("A", "B"), start=st,
                               slug=f"aec-{lg}-aaa-bbb-2026-11-18")
    gap = _book(0.48, 0.50)                                    # long ask 50c
    S = {}
    S["n01-pileup"] = [(_ctx_view(nba, _bk(_lvl((0.49, 900)), _lvl((0.50, 100))), 0.53), {})]
    S["n02-snipe"] = [(_ctx_view(nba, _bk(_lvl((0.48, 500)), _lvl((0.50, 10), (0.53, 500))), 0.53), {})]
    S["n03-wall"] = [(_ctx_view(nba, _bk(_lvl((0.49, 2000), (0.47, 50), (0.46, 50), (0.45, 40)),
                                          _lvl((0.50, 100))), 0.53), {})]
    S["n04-spread-snap"] = [(_ctx_view(nba, _bk(_lvl((0.44, 100)), _lvl((0.50, 100))), 0.53), {}),
                            (_ctx_view(nba, _bk(_lvl((0.50, 100)), _lvl((0.51, 100))), 0.54), None)]
    S["n05-empty-shelf"] = [(_ctx_view(nba, _bk((), _lvl((0.60, 100))), 0.55), {})]
    S["n06-deep-book"] = [(_ctx_view(nba, _bk(_lvl((0.48, 2000)), _lvl((0.50, 2000))), 0.56), {})]
    S["n07-whale"] = [(_ctx_view(nba, _bk(_lvl((0.48, 100)), _lvl((0.50, 100)), 0.49, 1000), 0.53), {}),
                      (_ctx_view(nba, _bk(_lvl((0.48, 100)), _lvl((0.50, 100)), 0.50, 1600), 0.53), None)]
    S["n08-volume-spike"] = [(_ctx_view(nba, _bk(_lvl((0.46, 100)), _lvl((0.48, 100)), 0.47, 1000), 0.53), {}),
                             (_ctx_view(nba, _bk(_lvl((0.46, 100)), _lvl((0.48, 100)), 0.47, 1010), 0.53), None),
                             (_ctx_view(nba, _bk(_lvl((0.48, 100)), _lvl((0.50, 100)), 0.50, 1200), 0.53), None)]
    S["n09-silence"] = [(_ctx_view(nba, _bk(_lvl((0.47, 100)), _lvl((0.49, 100)), 0.48, 1000), 0.53,
                                   now=NOW - timedelta(minutes=12)), {}),
                        (_ctx_view(nba, _bk(_lvl((0.47, 100)), _lvl((0.49, 100)), 0.48, 1000), 0.53,
                                   now=NOW - timedelta(minutes=11)), None),
                        (_ctx_view(nba, _bk(_lvl((0.48, 100)), _lvl((0.50, 100)), 0.50, 1010), 0.53), None)]
    agg = lambda tr, lt: (_ctx_view(nba, _bk(_lvl((0.48, 100)), _lvl((0.50, 100)), lt, tr), 0.53), None)
    S["n10-aggressive"] = [(_ctx_view(nba, _bk(_lvl((0.48, 100)), _lvl((0.50, 100)), 0.49, 100), 0.53), {}),
                           agg(110, 0.50), agg(120, 0.50), agg(130, 0.50)]
    S["n11-open-interest"] = [(_ctx_view(nba, _bk(_lvl((0.48, 100)), _lvl((0.50, 100)), oi=1200), 0.53,
                                         series=_ser([(400, 0.50, None, None, None, 1000), (0, 0.53)])), {})]
    S["n12-intl-wall"] = [(_ctx_view(nba, gap, 0.53, intl={"bid": 0.52, "ask": 0.54, "bid_size": 5000,
                                                            "ask_size": 100, "spread": 0.02}), {})]
    S["n13-intl-confidence"] = [(_ctx_view(nba, gap, 0.53, intl={"bid": 0.525, "ask": 0.535, "bid_size": 1,
                                                                  "ask_size": 1, "spread": 0.01},
                                           series=_ser([(200, 0.51, None, None, None, None, None, 0.04),
                                                        (0, 0.53, None, None, None, None, None, 0.01)])), {})]
    S["n14-busy-abroad"] = [(_ctx_view(nba, gap, 0.53, series=_ser(
        [(15, 0.50, None, None, 100), (10, 0.51, None, None, 100), (5, 0.52, None, None, 100),
         (0, 0.53, None, None, 100)])), {})]
    spr = _m(**{**NBA_EVENT, "name": "Knicks"}, kind="spreads", line=-3.5, start=NOW - timedelta(minutes=30))
    ml = {"kind": "moneyline", "long_name": "Knicks", "fair": 0.60, "prev": 0.55}
    S["n15-spread-lag"] = [(_ctx_view(spr, gap, 0.53, others=(ml,)), {"mid_last": 0.49}),
                           (_ctx_view(spr, gap, 0.53, others=(ml,)), None)]
    epl = lambda name, draw: _m(name=name, is_draw=draw, start=NOW - timedelta(minutes=30))
    legs = ({"kind": "moneyline", "ask_long": 0.28, "is_draw": False},
            {"kind": "moneyline", "ask_long": 0.25, "is_draw": True})
    S["n16-three-way"] = [(_ctx_view(epl("Fulham", False), _book(0.33, 0.35), 0.40, others=legs), {})]
    S["n17-womens-dog"] = [(_ctx_view(slug_t("wta", NOW + timedelta(hours=1)), _book(0.65, 0.66), 0.60,
                                      live=False), {})]
    rec = {"from_start": True, "lo": 0.75, "hi": 0.92, "pre": 0.65, "first_live": 0.66}
    S["n18-dominant-closer"] = [(_ctx_view(live, _book(0.87, 0.88), 0.91, market=rec), {})]
    S["n19-competitive-dog"] = [(_ctx_view(live, _book(0.39, 0.40), 0.43,
                                           market={"pre": 0.40, "from_start": True, "lo": 0.36, "hi": 0.50}), {})]
    t_pre = slug_t("atp", NOW + timedelta(hours=1))
    t_live = slug_t("atp", NOW - timedelta(minutes=10))
    ago = lambda h: (NOW - timedelta(hours=h)).isoformat()
    learned = {"wins": {"aaa": [(ago(5), "atp"), (ago(20), "atp")]}, "best_pre": {"aaa": 0.85},
               "league_day": {"nba": (4, 6)}, "league_last": {"nba": [True, True, True]},
               "long_today": {"bbb"}, "h2h": {("aaa", "bbb"): [("2026-11-10", "aaa")]},
               "calib": {("nba", band(0.53)): (25, 40)}, "dog": {("nba", band(0.40)): (15, 25)},
               "best_by_cat": {"nba": "s15-x06"}, "best": "s15-x06", "worst": "s14-overreaction-fade",
               "fill_ratio": {"nba": 0.8}, "since": (NOW - timedelta(hours=5)).isoformat()}
    S["n20-player-form"] = [(_ctx_view(t_pre, gap, 0.53, live=False, learned=learned), {})]
    S["n21-strong-discount"] = [(_ctx_view(t_pre, gap, 0.53, live=False, learned=learned), {})]
    nba_pre = _m(**{**NBA_EVENT, "name": "Knicks"}, start=NOW + timedelta(hours=1))
    S["n22-league-mood"] = [(_ctx_view(nba_pre, _book(0.65, 0.66), 0.60, live=False, learned=learned), {})]
    S["n23-upset-bounce"] = [(_ctx_view(nba_pre, _book(0.58, 0.60), 0.65, live=False, learned=learned), {})]
    S["n24-tired"] = [(_ctx_view(t_pre, gap, 0.53, live=False, learned=learned), {})]
    S["n25-rematch"] = [(_ctx_view(t_pre, gap, 0.53, live=False, learned=learned), {})]
    S["n26-hot-tournament"] = [(_ctx_view(t_pre, gap, 0.53, live=False, learned=learned), {})]
    S["n27-range"] = [(_ctx_view(nba, _book(0.40, 0.41), 0.45,
                                 series=_ser([(590, 0.60), (300, 0.44), (0, 0.45)])), {})]
    S["n28-calm-favourite"] = [(_ctx_view(nba, _book(0.62, 0.63), 0.66,
                                          series=_ser([(1190, 0.66), (600, 0.665), (0, 0.66)])), {})]
    hi_rec = {"from_start": True, "first_live": 0.50, "lo": 0.48, "hi": 0.63}
    S["n29-game-high"] = [(_ctx_view(nba, _book(0.60, 0.62), 0.62, market=hi_rec), {}),
                          (_ctx_view(nba, _book(0.60, 0.61), 0.65, market=hi_rec), None)]
    S["n30-second-wind"] = [(_ctx_view(nba, _book(0.60, 0.62), 0.70), {}),
                            (_ctx_view(nba, _book(0.40, 0.42), 0.45), None),
                            (_ctx_view(nba, _book(0.55, 0.57), 0.60), None)]
    S["n31-fake-move"] = [(_ctx_view(nba, _book(0.58, 0.59), 0.62,
                                     series=_ser([(110, 0.62), (60, 0.55), (0, 0.62)])), {})]
    S["n32-kickoff-carry"] = [(_ctx_view(_m(**{**NBA_EVENT, "name": "Knicks"}, start=NOW + timedelta(minutes=60)),
                                         gap, 0.50, live=False), {}),
                              (_ctx_view(_m(**{**NBA_EVENT, "name": "Knicks"}, start=NOW - timedelta(minutes=2)),
                                         gap, 0.54), None)]
    S["n33-steam-fade"] = [(_ctx_view(pre_t, _book(0.67, 0.68), 0.64, live=False,
                                      series=_ser([(3700, 0.56), (1800, 0.60), (0, 0.64)])), {})]
    long_m = _m(league="atp", event="atp-a-b-2026-11-18", name="A", title="A vs. B",
                teams=("A", "B"), start=NOW - timedelta(minutes=160))
    S["n34-stamina"] = [(_ctx_view(long_m, _book(0.47, 0.48), 0.52, market={"pre": 0.70}), {})]
    soon = _m(**{**NBA_EVENT, "name": "Knicks"}, start=NOW + timedelta(minutes=5))
    S["n35-pre-start-squeeze"] = [(_ctx_view(soon, _book(0.48, 0.50), 0.53, live=False,
                                             series=_ser([(320, 0.53, 0.53, 0.50), (0, 0.53, 0.50, 0.52)])), {})]
    S["n36-first-to-60"] = [(_ctx_view(live, _book(0.57, 0.58), 0.61,
                                       market={"pre": 0.50, "from_start": True}), {})]
    itf = _m(league="itfme", event="itfme-a-b-2026-11-18", name="A", title="A vs. B",
             teams=("A", "B"), start=NOW - timedelta(minutes=30))
    S["n37-split"] = [(_ctx_view(itf, _book(0.60, 0.61), 0.65, prev=0.63), {})]
    S["n38-steam-chase"] = [(_ctx_view(nba_pre, gap, 0.56, live=False,
                                       series=_ser([(1900, 0.51), (0, 0.56)])), {})]
    S["n39-new-listing"] = [(_ctx_view(nba, gap, 0.56, learned=learned,
                                       market={"first_seen": (NOW - timedelta(minutes=10)).isoformat()}), {})]
    S["n40-calibration"] = [(_ctx_view(nba, gap, 0.53, learned=learned), {})]
    S["n41-league-dog"] = [(_ctx_view(nba, _book(0.64, 0.65), 0.60, learned=learned), {})]
    S["n42-low-fee"] = [(_ctx_view(nba, _book(0.87, 0.88), 0.90), {})]
    S["n43-best-per-sport"] = [(_ctx_view(nba, _book(0.60, 0.61), 0.65, prev=0.63, learned=learned), {})]
    S["n44-best-vs-worst"] = [(_ctx_view(nba, _book(0.60, 0.61), 0.65, prev=0.63, learned=learned), {})]
    S["n45-good-fills"] = [(_ctx_view(nba, _book(0.60, 0.61), 0.65, prev=0.63, learned=learned), {})]
    S["n46-rounding"] = [(_ctx_view(live, _book(0.40, 0.50), 0.456), {})]
    S["n47-scale-in"] = [(_ctx_view(nba, _book(0.60, 0.61), 0.65, prev=0.63), {})]
    S["n48-half-lock"] = [(_ctx_view(live, _book(0.44, 0.46), 0.45, held=100.0), _held_long(price=0.28))]
    S["n49-re-entry"] = [(_ctx_view(live, _book(0.11, 0.12), 0.15), {"fair0": 0.30, "locking": True})]
    S["n50-even-money"] = [(_ctx_view(nba, gap, 0.55), {})]
    return S


def test_each_of_the_fifty_and_its_live_copy_acts_in_its_scenario():
    import copy
    from psb import fifty
    S = _scenarios()
    assert sorted(S) == sorted(b.key for b in fifty.FIFTY)
    for key, steps in S.items():
        for k in (key, f"Live_{key}"):
            if k not in registry.BOTS:    # a paused Live_ copy (livesim.PAUSED)
                continue
            bot, st, acts = registry.BOTS[k], None, []
            for v, st0 in steps:
                if st0 is not None:
                    st = copy.deepcopy(st0)
                assert bot.wants(v.m, v.live), (k, "does not want its market")
                acts = bot.step(v, st)
            assert acts, f"{k}: did not act in its scenario"


def test_a_bot_that_errors_is_taken_off_and_the_others_keep_trading(monkeypatch):
    eng, led = _engine()
    m = _m(name="Live", slug="live", start=NOW - timedelta(minutes=10))
    link = reference.Link({"clobTokenIds": '["t0", "t1"]'}, 0, False)
    eng.markets[m.slug] = paper.Tracked(m, link)
    monkeypatch.setattr(paper, "_now", lambda: NOW)
    monkeypatch.setattr(paper.reference, "live_mids", lambda links: {"t0": 0.6})
    monkeypatch.setattr(paper.usmarket, "book", lambda slug: _book(0.5, 0.52))
    broken = registry.BOTS["n50-even-money"]
    monkeypatch.setattr(type(broken), "step", lambda self, v, st: 1 / 0)
    eng.poll(5, 60)
    assert "n50-even-money" not in eng.bots and "ZeroDivisionError" in eng.removed["n50-even-money"]
    assert "Live_n50-even-money" not in eng.bots          # same class, also broken
    assert "n01-pileup" in eng.bots and eng.stats["polls"] == 1


def test_eight_gap_bots_and_live_copies_act_and_extra_leagues_are_opt_in():
    from psb import gaps
    assert len(gaps.GAPS) == 8
    mk = lambda lg, **k: _m(league=lg, event=f"{lg}-aa-bb-2026-11-19", name="A", title="A vs. B",
                            teams=("A", "B"), **k)
    live, pre = NOW - timedelta(minutes=20), NOW + timedelta(hours=1)
    jump = lambda m: _view(m, _book(0.60, 0.61), 0.65, prev=0.63, live=True)
    value = lambda m: _view(m, _book(0.48, 0.50), 0.56)
    cases = {
        "g01-fights": [value(mk("boxing", start=pre)), jump(mk("ufc", start=live))],
        "g02-darts": [value(mk("modus", start=pre)), jump(mk("pdcdarts", start=live))],
        "g04-mlb": [value(mk("mlb", start=pre)), jump(mk("mlb", kind="totals", line=8.5, start=live))],
        "g06-lines-maker": [_view(mk("nba", kind="spreads", line=-3.5, start=pre), _book(0.50, 0.60), 0.55)],
        "g08-draw": [_view(_m(name="Draw", is_draw=True, start=pre), _book(0.20, 0.22), 0.26)],
        "g09-take-profit": [jump(mk("nba", start=live))],
        "g11-reserve": [jump(mk("nba", start=live))],
        "g12-clock": [jump(mk("itfme", start=live))],
    }
    for key, views in cases.items():
        for k in (key, f"Live_{key}"):
            if k not in registry.BOTS:    # a paused Live_ copy
                continue
            for v in views:
                assert registry.BOTS[k].wants(v.m, v.live), (k, v.m.slug)
                assert registry.BOTS[k].step(v, {}), (k, v.m.slug)
    # Extra leagues: only bots that opt in may trade them.
    box = mk("boxing", start=live)
    assert paper.allowed(registry.BOTS["g01-fights"], box)
    assert not paper.allowed(registry.BOTS["s15-x06"], box)
    # Take-profit cashes out at +10c; the reserve wants 3c once 70% is at risk.
    st = _held_long(price=0.40)
    (a,) = registry.BOTS["g09-take-profit"].step(_view(mk("nba", start=live), _book(0.49, 0.51), 0.50,
                                                       live=True, held=100.0), st)
    assert a.tag == "lock"
    v = jump(mk("nba", start=live))
    v.cash = 200.0
    assert registry.BOTS["g11-reserve"].step(v, {}) == []


def test_fifteen_all_sports_bots_and_live_copies_act_on_their_own_data():
    from psb import allsports
    assert len(allsports.ALL15) == 15
    nba = _m(**{**NBA_EVENT, "name": "Knicks"}, start=NOW - timedelta(minutes=30))
    jump = lambda **k: _ctx_view(nba, _book(0.60, 0.61), 0.65, prev=0.63, **k)
    stats = {"by_cat": {"nba": [8, 12, 40.0, 3, 4, 12.0]}, "by_hour": {}, "orders_by_hour": {}}
    calm = [(600 - i * 30, 0.50) for i in range(20)] + [(0, 0.53)]
    spread = {"kind": "spreads", "long_name": "Knicks", "fair": 0.60, "prev": 0.55}
    stale = [(1800, 0.50, None, None, 100), (900, 0.52, None, None, 100), (0, 0.54, None, None, 100)]
    def ser(rows):
        return _ser([(a, f, None, None, *r) if r else (a, f) for a, f, *r in rows])
    cases = {
        "a01-sport-switcher": lambda b: [jump(stats=stats)],
        "a02-slow-sports": lambda b: [jump(stats=stats)],
        "a03-busy-games": lambda b: [jump(series=_ser([(500, 0.6, None, None, 100), (0, 0.65, None, None, 900)]))],
        "a04-confirmed": lambda b: [_ctx_view(nba, _book(0.60, 0.61), 0.65,
                                              series=_ser([(8, 0.63, None, None, None, None, 0.595),
                                                           (0, 0.65, None, None, None, None, 0.605)]))],
        "a05-biggest-gap": lambda b: [_ctx_view(nba, _book(0.48, 0.50), 0.56)],
        "a06-growing-gap": lambda b: [_ctx_view(nba, _book(0.48, 0.50), x) for x in (0.52, 0.54, 0.56)],
        "a07-price-step": lambda b: [_ctx_view(nba, _book(0.48 + (i % 2) * 0.01, 0.50 + (i % 2) * 0.01), 0.56)
                                     for i in range(25)],
        "a08-spread-to-winner": lambda b: [_ctx_view(nba, _book(0.48, 0.50), 0.53, others=(spread,))] * 2,
        "a09-calm-then-storm": lambda b: [_ctx_view(nba, _book(0.48, 0.50), 0.53, series=_ser(calm))],
        "a10-self-hours": lambda b: [jump(stats=stats)],
        "a11-same-time-pair": lambda b: [
            _ctx_view(_m(**{**NBA_EVENT, "event": "nba-bos-mia-2026-11-17", "name": "Celtics"},
                         start=NOW - timedelta(minutes=30)), _book(0.50, 0.51), 0.55,
                      series=_ser([(250, 0.75), (0, 0.55)])),
            _ctx_view(nba, _book(0.60, 0.62), 0.66)],
        "a12-stale-market": lambda b: [_ctx_view(nba, _book(0.48, 0.50), 0.54, series=_ser(stale))],
        "a13-price-lifetime": lambda b: [
            _ctx_view(nba, _book(0.48, 0.50), 0.56, now=NOW - timedelta(seconds=40)),
            _ctx_view(nba, _book(0.48, 0.50), 0.50, now=NOW - timedelta(seconds=10)),
            _ctx_view(nba, _book(0.48, 0.50), 0.56)],
        "a14-win-rate-leash": lambda b: [jump(stats=stats)],
        "a15-size-by-sport": lambda b: [jump(stats=stats)],
    }
    assert sorted(cases) == sorted(b.key for b in allsports.ALL15)
    for key, make in cases.items():
        for k in (key, f"Live_{key}"):
            if k not in registry.BOTS:    # a paused Live_ copy
                continue
            bot = type(registry.BOTS[k])()          # fresh memory
            st, acts = {}, []
            for v in make(bot):
                acts = bot.step(v, st)
            assert acts, k
    # Sizing from its own record: up today in the NBA -> $15; down -> $5.
    (a,) = type(registry.BOTS["a01-sport-switcher"])().step(jump(stats=stats), {})
    assert a.dollars == pytest.approx(15.0)
    bad = {"by_cat": {"nba": [2, 12, -40.0, 0, 6, -30.0]}, "by_hour": {}, "orders_by_hour": {}}
    assert type(registry.BOTS["a14-win-rate-leash"])().step(jump(stats=bad), {}) == []
    (a,) = type(registry.BOTS["a15-size-by-sport"])().step(jump(stats=bad), {})
    assert a.dollars == pytest.approx(5.0)
    hours = {"by_cat": {}, "by_hour": {NOW.hour: (1, 6)}, "orders_by_hour": {}}
    (a,) = type(registry.BOTS["a10-self-hours"])().step(jump(stats=hours), {})
    assert a.dollars == pytest.approx(3.0)


def test_ten_underdog_versions_and_live_copies():
    from psb import underdog
    assert len(underdog.UD_VERSIONS) == 10
    nba = _m(**{**NBA_EVENT, "name": "Knicks"}, start=NOW - timedelta(minutes=30))
    v = _view(nba, _book(0.68, 0.69), 0.65, prev=0.67, live=True)    # underdog 33c -> 35c, offered 32c
    for b in underdog.UD_VERSIONS:
        for k in (b.key, f"Live_{b.key}"):
            bot = registry.BOTS[k]
            vv = v if k.split("_")[-1] != "ud-jump25" else _view(nba, _book(0.70, 0.71), 0.65, prev=0.675,
                                                                 live=True)
            assert bot.wants(vv.m, vv.live) and bot.step(vv, {}), k
    assert not registry.BOTS["ud-no-tennis"].wants(_tennis(), True)
    assert registry.BOTS["ud-jump1"].step(_view(nba, _book(0.68, 0.69), 0.65, prev=0.66, live=True), {})
    # Take profit at +10c and stop at 10c, on a held long underdog bought at 30c.
    bot = registry.BOTS["ud-tp10-sl10"]
    (a,) = bot.step(_view(_tennis(), _book(0.39, 0.41), 0.40, live=True, held=10.0), _held_long(10, 0.30))
    assert a.tag == "lock"
    (a,) = bot.step(_view(_tennis(), _book(0.19, 0.21), 0.20, live=True, held=10.0), _held_long(10, 0.30))
    assert a.tag == "stop"
    # Held to the end: no exit on the plain versions.
    assert registry.BOTS["ud-2usd"].step(_view(_tennis(), _book(0.19, 0.21), 0.20, live=True, held=10.0),
                                         _held_long(10, 0.30)) == []


def test_thirty_live_clock_bots_switch_bot_by_time_of_day():
    from psb import clock
    assert len(clock.CLOCKS) == 30 and len({b.key for b in clock.CLOCKS}) == 30
    plans = {tuple(b.plan.values()) for b in clock.CLOCKS}
    assert len(plans) == 30
    used = {k for p in plans for k in p}
    assert used == {k for k, _ in clock.MORNING + clock.AFTERNOON + clock.NIGHT}
    assert all(k in registry.BOTS for k in used)
    for b in clock.CLOCKS:
        assert registry.BOTS[b.key] is b and b.live_sim
    bot = registry.BOTS["Live_clock-01"]
    m = _m(league="itfme", event="itfme-aa-bb-2026-11-19", name="A", title="A vs. B",
           teams=("A", "B"), start=NOW - timedelta(minutes=20))
    for hour, want in ((8, bot.plan["morning"]), (14, bot.plan["afternoon"]),
                       (20, bot.plan["night"]), (2, bot.plan["night"])):
        v = _view(m, _book(0.60, 0.61), 0.65, prev=0.63, live=True, now=NOW.replace(hour=hour))
        assert bot.choose(v, {}) == want
    # At night it acts as its night #15 version would on a 2c jump.
    st = {}
    v = _view(m, _book(0.60, 0.61), 0.65, prev=0.63, live=True, now=NOW.replace(hour=20))
    assert bot.step(v, st) and st["via"] == bot.plan["night"]
