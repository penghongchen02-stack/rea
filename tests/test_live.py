"""Real-money runner: arming gates, order construction, limits, and order
lifecycle against a fake exchange. No test touches the network."""
import base64
import json
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

from psb import bots, fees, live, paper
from tests.test_paper import NOW, _book, _m


def _secret():
    k = ed25519.Ed25519PrivateKey.generate()
    raw = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                          serialization.NoEncryption())
    return k.public_key(), base64.b64encode(raw).decode()


class FakeExchange:
    """Records requests and answers like the Polymarket US Retail API."""

    def __init__(self):
        self.calls = []
        self.orders = {}       # id -> order dict
        self.next_id = 0
        self.take_fill = None  # (qty, long_px) to report for IOC orders

    def __call__(self, method, url, headers, body):
        path = url.removeprefix(live.API)
        data = json.loads(body) if body else None
        self.calls.append((method, path, data, headers))
        if method == "POST" and path == "/v1/orders":
            self.next_id += 1
            oid = f"o{self.next_id}"
            self.orders[oid] = {"id": oid, "state": "ORDER_STATE_NEW", "cumQuantity": 0, **data}
            out = {"id": oid}
            if data.get("tif") == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL" and self.take_fill:
                q, px = self.take_fill
                out["executions"] = [{"lastShares": str(q), "lastPx": {"value": str(px)},
                                      "commissionNotionalCollected": {"value": "0.02"}}]
            return 200, out
        if method == "GET" and path.startswith("/v1/order/"):
            return 200, {"order": self.orders[path.split("/")[3]]}
        if path.endswith("/cancel") and path.startswith("/v1/order/"):
            self.orders[path.split("/")[3]]["state"] = "ORDER_STATE_CANCELED"
            return 200, {}
        if path == "/v1/orders/open/cancel":
            for o in self.orders.values():
                if o["marketSlug"] in data["slugs"]:
                    o["state"] = "ORDER_STATE_CANCELED"
            return 200, {}
        return 404, {"message": "unexpected"}

    def sent(self, path="/v1/orders"):
        return [c[2] for c in self.calls if c[0] == "POST" and c[1] == path]


def _engine(tmp_path, limits=live.Limits(), armed=True, key="ferrarichampions2026"):
    ex = FakeExchange()
    _, secret = _secret()
    client = live.UsClient(live.Signer("kid", secret), transport=ex) if armed else None
    led = paper.Ledger()
    eng = live.LiveEngine(led, [], fees.WORST_TAKER_THETA, lambda m: None, 24, key, limits,
                          client, tmp_path / "orders.jsonl", tmp_path / "day.json")
    return eng, led, ex


# --- arming -----------------------------------------------------------------

def test_default_config_is_not_armed(tmp_path):
    cfg = live.LiveConfig.load(paper.Path(__file__).resolve().parent.parent / "live.json")
    assert cfg.armed is False and cfg.bot is None
    env = {live.CONFIRM_ENV: live.CONFIRM_VALUE, live.KEY_ENV: "k", live.SECRET_ENV: "s"}
    assert 'config has "armed": false' in live.arm_check(cfg, env, tmp_path / "STOP")


def test_every_gate_is_required(tmp_path):
    cfg = live.LiveConfig(armed=True, bot="swisstony")
    full = {live.CONFIRM_ENV: live.CONFIRM_VALUE, live.KEY_ENV: "k", live.SECRET_ENV: "s"}
    assert live.arm_check(cfg, full, tmp_path / "STOP") == []
    for drop in full:
        env = {k: v for k, v in full.items() if k != drop}
        assert live.arm_check(cfg, env, tmp_path / "STOP")
    assert live.arm_check(cfg, {**full, live.CONFIRM_ENV: "yes"}, tmp_path / "STOP")
    (tmp_path / "STOP").write_text("")
    assert live.arm_check(cfg, full, tmp_path / "STOP")


def test_config_rejects_unknown_keys_and_bots(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"armed": false, "limit": {}}')
    with pytest.raises(live.LiveError):
        live.LiveConfig.load(p)
    p.write_text('{"armed": true, "bot": "nobody"}')
    with pytest.raises(live.LiveError):
        live.LiveConfig.load(p)
    p.write_text('{"armed": "yes"}')
    with pytest.raises(live.LiveError):
        live.LiveConfig.load(p)


def test_armed_config_without_confirmation_refuses_to_start(tmp_path, monkeypatch):
    c = tmp_path / "live.json"
    c.write_text('{"armed": true, "bot": "swisstony"}')
    monkeypatch.delenv(live.CONFIRM_ENV, raising=False)
    with pytest.raises(SystemExit, match="blocked"):
        live.main(["--config", str(c), "--state", str(tmp_path / "s"), "--minutes", "0"])


# --- signing and order construction -------------------------------------------

def test_signature_matches_the_documented_message():
    pub, secret = _secret()
    h = live.Signer("kid", secret).headers("POST", "/v1/orders", ts_ms=1700000000000)
    assert h["X-PM-Access-Key"] == "kid" and h["X-PM-Timestamp"] == "1700000000000"
    pub.verify(base64.b64decode(h["X-PM-Signature"]), b"1700000000000POST/v1/orders")


def test_price_is_always_the_long_side_and_never_pays_more():
    assert live.long_price("long", 0.437, 0.01) == pytest.approx(0.43)
    assert live.long_price("short", 0.437, 0.01) == pytest.approx(0.57)   # pays 0.43 for short
    assert live.long_price("short", 0.40, 0.01) == pytest.approx(0.60)
    assert live.long_price("long", 0.4375, 0.005) == pytest.approx(0.435)
    with pytest.raises(live.LiveError):
        live.long_price("long", 0.005, 0.01)
    b = live.order_body("m", "short", 0.40, 12, 0.01, taker=False)
    assert b["intent"] == "ORDER_INTENT_BUY_SHORT" and b["price"]["value"] == "0.6"
    assert b["participateDontInitiate"] is True and b["tif"] == "TIME_IN_FORCE_GOOD_TILL_CANCEL"
    t = live.order_body("m", "long", 0.55, 3, 0.01, taker=True)
    assert t["tif"] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL" and t["quantity"] == 3


# --- shadow mode --------------------------------------------------------------

def test_shadow_mode_sends_nothing_and_logs_intended_orders(tmp_path):
    eng, led, ex = _engine(tmp_path, armed=False)
    t = paper.Tracked(_m(), None)
    eng._apply("ferrarichampions2026", eng.bots["ferrarichampions2026"], t, _book(0.40, 0.45),
               0.5, [bots.Rest("long", 0.42, 4.2, "dominant")], NOW)
    assert ex.calls == [] and len(led.orders) == 1       # paper order, nothing sent
    rows = [json.loads(x) for x in (tmp_path / "orders.jsonl").read_text().splitlines()]
    assert rows[0]["sent"] is False and rows[0]["price"] == 0.42


# --- armed mode ---------------------------------------------------------------

def test_take_sends_capped_ioc_and_records_real_execution(tmp_path):
    eng, led, ex = _engine(tmp_path, live.Limits(max_order_usd=5))
    ex.take_fill = (10, 0.45)
    t = paper.Tracked(_m(), None)
    bot = eng.bots["ferrarichampions2026"]
    eng._apply(eng.key, bot, t, _book(0.40, 0.45), 0.6, [bots.Take("long", 100.0, 0.46, "dominant")], NOW)
    (body,) = ex.sent()
    assert body["quantity"] == 10 and body["price"]["value"] == "0.46"   # $5 cap / 0.46
    assert body["intent"] == "ORDER_INTENT_BUY_LONG"
    f = led.fills[-1]
    assert (f["price"], f["contracts"], f["fee"], f["kind"]) == (0.45, 10, 0.02, "taker")


def test_short_take_records_the_short_side_price(tmp_path):
    eng, led, ex = _engine(tmp_path)
    ex.take_fill = (5, 0.62)          # long-side execution price
    t = paper.Tracked(_m(), None)
    eng._apply(eng.key, eng.bots[eng.key], t, _book(0.62, 0.65), 0.3,
               [bots.Take("short", 2.0, 0.40, "hedge")], NOW)
    assert ex.sent()[0]["intent"] == "ORDER_INTENT_BUY_SHORT"
    f = led.fills[-1]
    assert f["side"] == "short" and f["price"] == pytest.approx(0.38) and f["contracts"] == 5


def test_rest_is_kept_when_reissued_and_replaced_when_moved(tmp_path):
    eng, led, ex = _engine(tmp_path)
    t = paper.Tracked(_m(), None)
    bot = eng.bots[eng.key]
    rest = [bots.Rest("long", 0.42, 4.2, "dominant")]
    eng._apply(eng.key, bot, t, _book(0.40, 0.45), 0.5, rest, NOW)
    eng._apply(eng.key, bot, t, _book(0.40, 0.45), 0.5, rest, NOW)
    assert len(ex.sent()) == 1 and len(eng.live) == 1             # kept, not churned
    eng._apply(eng.key, bot, t, _book(0.40, 0.45), 0.5, [bots.Rest("long", 0.41, 4.1, "dominant")], NOW)
    assert len(ex.sent()) == 2 and ex.orders["o1"]["state"] == "ORDER_STATE_CANCELED"
    eng._apply(eng.key, bot, t, _book(0.40, 0.45), 0.5, [], NOW)   # not re-issued
    assert eng.live == {} and ex.orders["o2"]["state"] == "ORDER_STATE_CANCELED"


def test_resting_fill_is_recorded_from_the_exchange_including_cancel_races(tmp_path):
    eng, led, ex = _engine(tmp_path)
    t = paper.Tracked(_m(), None)
    bot = eng.bots[eng.key]
    eng._apply(eng.key, bot, t, _book(0.40, 0.45), 0.5, [bots.Rest("long", 0.42, 4.2, "dominant")], NOW)
    ex.orders["o1"]["cumQuantity"] = 4
    eng._settle_orders(t, _book(0.40, 0.45), NOW)
    assert [f["contracts"] for f in led.fills] == [4] and eng.live["o1"].filled == 4
    ex.orders["o1"]["cumQuantity"] = 6           # fills while we cancel
    eng._apply(eng.key, bot, t, _book(0.40, 0.45), 0.5, [], NOW)
    assert [f["contracts"] for f in led.fills] == [4, 2] and eng.live == {}


def test_exposure_cap_limits_new_orders(tmp_path):
    eng, led, ex = _engine(tmp_path, live.Limits(max_order_usd=100, max_exposure_usd=10))
    led.record(eng.key, "other", "long", 0.5, 16, 0.0, "taker", "x", None, NOW)   # $8 at risk
    t = paper.Tracked(_m(), None)
    eng._apply(eng.key, eng.bots[eng.key], t, _book(0.40, 0.45), 0.6,
               [bots.Rest("long", 0.40, 50, "dominant")], NOW)
    assert ex.sent()[0]["quantity"] == 5                         # only $2 left -> 5 @ 0.40


def test_daily_loss_halts_and_cancels_everything(tmp_path):
    eng, led, ex = _engine(tmp_path, live.Limits(max_daily_loss_usd=5))
    t = paper.Tracked(_m(), None)
    eng._apply(eng.key, eng.bots[eng.key], t, _book(0.40, 0.45), 0.5,
               [bots.Rest("long", 0.42, 4.2, "dominant")], NOW)
    led.record(eng.key, "lost", "long", 0.6, 10, 0.0, "taker", "x", None, NOW)
    led.settled["lost"] = 0.0                                    # -$6 today
    eng.check_daily_loss()
    assert eng.halted and eng.live == {} and ex.orders["o1"]["state"] == "ORDER_STATE_CANCELED"
    eng._apply(eng.key, eng.bots[eng.key], t, _book(0.40, 0.45), 0.5,
               [bots.Take("long", 5, 0.45, "dominant")], NOW)
    assert len(ex.sent()) == 1                                   # nothing new after halt


def test_daily_loss_survives_a_restart(tmp_path):
    eng, led, _ = _engine(tmp_path, live.Limits(max_daily_loss_usd=5))
    led.record(eng.key, "lost", "long", 0.4, 10, 0.0, "taker", "x", None, NOW)
    led.settled["lost"] = 0.0                                    # -$4 so far
    again = live.LiveEngine(led, [], fees.WORST_TAKER_THETA, lambda m: None, 24, eng.key,
                            eng.limits, eng.client, tmp_path / "o.jsonl", tmp_path / "day.json")
    led.record(eng.key, "lost2", "long", 0.2, 10, 0.0, "taker", "x", None, NOW)
    led.settled["lost2"] = 0.0                                   # -$6 total today
    again.check_daily_loss()
    assert again.halted


def test_dropped_market_cancels_its_real_orders(tmp_path, monkeypatch):
    eng, led, ex = _engine(tmp_path)
    m = _m()
    t = paper.Tracked(m, None)
    eng.markets[m.slug] = t
    eng._apply(eng.key, eng.bots[eng.key], t, _book(0.40, 0.45), 0.5,
               [bots.Rest("long", 0.42, 4.2, "dominant")], NOW)
    monkeypatch.setattr(paper.usmarket, "discover", lambda lg, lines, **kw: [])
    eng.leagues = ["epl"]
    eng.discover()
    assert eng.live == {} and ex.orders["o1"]["state"] == "ORDER_STATE_CANCELED"
