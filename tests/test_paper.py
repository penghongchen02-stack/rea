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


def test_swisstony_skips_esports():
    m = _m(league="cs2", name="ICE", event="cs2-ice-bca-2026-11-17", title="ICE vs. BCA")
    assert bots.BOTS["swisstony"].step(_view(m, _book(0.60, 0.61), 0.70), {}) == []


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
    for key, b in bots.BOTS.items():
        if key.endswith("-v2"):
            assert b.rules_key == key[:-3] and b.rules_key in bots.BOTS


def test_ferrari_v2_trades_one_line_per_game_and_caps_dominance():
    old, new = bots.BOTS["ferrarichampions2026"], bots.BOTS["ferrarichampions2026-v2"]
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
    old, new = bots.BOTS["gamblingisallyourneed"], bots.BOTS["gamblingisallyourneed-v2"]
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


def test_swisstony_and_kch123_v2_need_two_cents_after_fee():
    m = _m(league="cbb", event="cbb-a-b-2026-11-17", title="A vs. B")
    # Ask 0.56, fee ~2.3c, fair 0.595: ~1.2c edge after fee.
    assert bots.BOTS["swisstony"].step(_view(m, _book(0.55, 0.56), 0.595), {})
    assert bots.BOTS["swisstony-v2"].step(_view(m, _book(0.55, 0.56), 0.595), {}) == []
    assert bots.BOTS["kch123"].step(_view(m, _book(0.55, 0.56), 0.595), {})
    assert bots.BOTS["kch123-v2"].step(_view(m, _book(0.55, 0.56), 0.595), {}) == []


def test_sinenooneei_v2_anchors_small_and_only_after_a_real_move():
    b = bots.BOTS["sinenooneei-v2"]
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
    assert bots.BOTS["randomgamebet"].step(v, {})[0].dollars == 50
    assert bots.BOTS["randomgamebet-v2"].step(v, {})[0].dollars == 15


def test_bots_added_later_get_their_own_start_time():
    led = paper.Ledger(started_at="2026-11-17T11:03:25Z")
    led.record("kch123", "m", "long", 0.5, 10, 0.0, "taker", "probe", 0.55, NOW)
    paper.Engine(led, [], fees.WORST_TAKER_THETA, _Log(), 24)
    assert set(led.bot_started) == set(bots.BOTS)
    assert led.bot_started["kch123"] == "2026-11-17T11:03:25Z"      # traded since the start
    assert led.bot_started["kch123-v2"] > "2026-11-17T11:03:25Z"    # joined now


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
        assert (i.key in bots.BOTS) == (i.status in ("running", "proxy"))


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
    b = bots.BOTS["s39-flat-10"]
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
    assert bots.BOTS["s40-daily-stop"].step(v, {}) == []
    assert bots.BOTS["s42-sport-budget"].step(v, {}) == []
    v.stats = {"pnl_today": 0.0, "last_results": [5, 5, 5], "open_by_cat": {}}
    assert bots.BOTS["s41-streak-cooler"].step(v, {})[0].dollars == 5


def test_sequential_pair_buys_the_second_side_only_under_98c():
    b, st = bots.BOTS["s07-underpriced-pair"], {}
    m = _m(name="Manchester City")
    assert b.step(_view(m, _book(0.50, 0.52), 0.55), st)[0].tag == "first"
    b.on_fill(st, bots.Fill("long", 0.52, 19, "first"), NOW)
    # Short asks 1 - 0.50 = 0.50: pair 1.02 -> wait.
    assert b.step(_view(m, _book(0.50, 0.52), 0.5, held=19), st) == []
    # Short asks 1 - 0.58 = 0.42: pair 0.94 + fee -> buy it.
    assert b.step(_view(m, _book(0.58, 0.60), 0.6, held=19), st)[0].tag == "second"


def test_home_side_follows_the_us_league_order(monkeypatch):
    monkeypatch.setattr(simple_bots, "home_participant",
                        lambda lg, parts: parts[1] if lg == "cbb" else None)
    m = _m(league="cbb", event="cbb-a-b-2026-11-18", name="Away U", title="Away U vs. Home St",
           teams=("Away U", "Home St"))
    assert simple_bots.home_side(_view(m, _book(0.5, 0.52), 0.5)) == "short"
