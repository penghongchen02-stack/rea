"""Checks of the real-money bot (paperbot/live.py) against a stand-in exchange: no network, no orders.
Run: python3 test_live.py"""
import os, json, time, tempfile, types, collections
from paperbot import live

class FakeEx:
    """Records what would be sent. Fills are scripted by `fill` (contracts filled of the next order)."""
    sent = []; pos = {}; fill = None; armed_flag = False; fail = None; lie = None
    def __init__(self, d): self.dir = d
    def armed(self):
        if os.path.exists(os.path.join(self.dir, "STOP")) or os.path.exists(os.path.join(self.dir, "HALT")): return False
        return FakeEx.armed_flag
    def call(self, *a, **k): return 200, {}, 1.0
    def balance(self): return {"cash": 100.0, "buying_power": 100.0, "open_orders": 0}
    def positions(self): return dict(FakeEx.pos)
    def preview(self, body): FakeEx.sent.append(("preview", body)); return 200, {"order": body}, 120.0
    def order(self, oid): return 404, {}, 1.0
    def cancel_all(self, slugs=()): FakeEx.sent.append(("cancel", list(slugs))); return 200, {}, 1.0
    def place(self, body, key):
        assert self.armed(), "place() reached while not armed"
        FakeEx.sent.append(("order", body))
        if FakeEx.fail: return FakeEx.fail, {"message": "refused"}, 100.0
        q = body["quantity"] if FakeEx.fill is None else FakeEx.fill; px = float(body["price"]["value"]); slug = body["marketSlug"]
        sgn = {"ORDER_INTENT_BUY_LONG": 1, "ORDER_INTENT_BUY_SHORT": -1, "ORDER_INTENT_SELL_LONG": -1, "ORDER_INTENT_SELL_SHORT": 1}[body["intent"]]
        FakeEx.pos[slug] = round(FakeEx.pos.get(slug, 0.0) + sgn * (q if FakeEx.lie is None else FakeEx.lie), 6)
        if abs(FakeEx.pos[slug]) < 1e-9: FakeEx.pos.pop(slug)
        o = {"state": "ORDER_STATE_FILLED" if q else "ORDER_STATE_CANCELED", "cumQuantity": q, "avgPx": {"value": str(px)}, "commissionNotionalTotalCollected": {"value": "0.05" if q else "0"}}
        ex = [{"type": "EXECUTION_TYPE_FILL", "lastShares": str(q), "lastPx": {"value": str(px)}, "order": o}] if q else [{"type": "EXECUTION_TYPE_CANCELED", "order": o}]
        return 200, {"id": "X1", "executions": ex}, 100.0

class M:
    def __init__(self, slug, bid, ask): self.slug = slug; self.best_bid = bid; self.best_ask = ask; self.min_qty = 0.01; self.state = "MARKET_STATE_OPEN"; self.asset = "btc"; self.end = time.time() + 200
    def remaining(self, now=None): return self.end - (now or time.time())

def bot(**cfg):
    d = tempfile.mkdtemp(); c = dict(live.DEFAULTS); c.update(cfg)
    eng = types.SimpleNamespace(spot=types.SimpleNamespace(listeners=[]), mf=types.SimpleNamespace(markets={}))
    FakeEx.sent = []; FakeEx.pos = {}; FakeEx.fill = None; FakeEx.armed_flag = False; FakeEx.fail = None; FakeEx.lie = None
    b = live.LiveBot(eng, d, c, exchange=FakeEx(d), keeper=False); b.bal = b.ex.balance(); b.bal_ts = time.time()
    return b, eng

def sig(b, eng, slug="cpc-btc-updown-5m-x", side="buy", px=0.40, q=5.0, bid=0.39, ask=0.40):
    eng.mf.markets[slug] = M(slug, bid, ask); b._entry((slug, side, px, q, time.time(), "t"), time.perf_counter()); return eng.mf.markets[slug]

n = 0
def ok(cond, what):
    global n; n += 1; assert cond, what; print("ok", what)

# 1. order format (the four cases the exchange's preview accepted)
B = live.order_body
ok(B("s", "buy", 0.4, 2)["intent"] == "ORDER_INTENT_BUY_LONG" and B("s", "buy", 0.4, 2)["price"]["value"] == "0.4", "Up opens as BUY_LONG at the YES ask")
ok(B("s", "sell", 0.6, 2)["intent"] == "ORDER_INTENT_BUY_SHORT", "Down opens as BUY_SHORT at the YES bid")
ok(B("s", "sell", 0.6, 2, close=True)["intent"] == "ORDER_INTENT_SELL_LONG" and B("s", "buy", 0.6, 2, close=True)["intent"] == "ORDER_INTENT_SELL_SHORT", "closing orders")
ok(B("s", "buy", 0.4, 2)["tif"] == "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL" and B("s", "buy", 0.4, 2)["type"] == "ORDER_TYPE_LIMIT", "always a limit order, immediate-or-cancel")
for bad in (0.0, 0.995, 1.2):
    try: B("s", "buy", bad, 1); ok(False, "bad price accepted")
    except ValueError: pass
ok(True, "prices outside 0.01-0.99 are refused before sending")

# 2. the real Exchange refuses to place unless armed, without touching the network
d = tempfile.mkdtemp(); ex = live.Exchange(d) if live.ws.available() else None
if ex:
    ex.call = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used"))
    try: ex.place(B("s", "buy", 0.4, 1), "k"); ok(False, "placed while not armed")
    except RuntimeError: ok(True, "place() refuses when the ARMED file is absent")
    open(os.path.join(d, "ARMED"), "w").write("yes"); ok(not ex.armed(), "ARMED file with the wrong words does not arm")
    open(os.path.join(d, "ARMED"), "w").write(live.ARM_PHRASE + "\n"); ok(ex.armed(), "ARMED file with the exact words arms")
    open(os.path.join(d, "STOP"), "w").write("x"); ok(not ex.armed(), "a STOP file overrides ARMED")

# 3. shadow mode: previews only, a fill only when the quote is still there, settled like a real one
b, eng = bot()
m = sig(b, eng); ok([k for k, _ in FakeEx.sent] == ["preview"], "shadow sends a preview and no order")
ok(b.st["shadow"][m.slug]["qty"] == 5.0 and not b.st["pos"], "quote still there: shadow fill booked")
b.on_settle(m, True); ok(abs(b.st["shadow_stats"]["pnl"] - (5 * 0.6 - round(0.095 * 5 * 0.4 * 0.6, 2))) < 1e-9, "shadow win settles at $1 less price and fee")
m2 = sig(b, eng, slug="s2", ask=0.42); ok("s2" not in b.st["shadow"], "quote gone (ask moved up): no shadow fill")
m3 = sig(b, eng, slug="s3", side="sell", px=0.60, bid=0.60, ask=0.61); b.on_settle(m3, True)
ok(b.st["shadow_stats"]["settled"] == 2 and b.st["shadow_stats"]["won"] == 1, "a Down bet loses when the window closes Up")

# 4. armed: a real order, position read back, result booked
b, eng = bot(); FakeEx.armed_flag = True
m = sig(b, eng); ok(FakeEx.sent[0][0] == "order" and b.st["pos"][m.slug]["qty"] == 5.0, "armed sends one order and books the fill")
b.on_settle(m, False); ok(abs(b.st["total_pnl"] - (-5 * 0.4 - 0.05)) < 1e-9 and not b.st["pos"], "a losing Up bet costs price and fee")
m = sig(b, eng, slug="d1", side="sell", px=0.60, bid=0.60, ask=0.61); ok(b.st["pos"]["d1"]["qty"] == -5.0, "a Down bet is held as a negative position")
b.on_settle(m, False); ok(abs(b.st["day_pnl"] - (-2.05 + 5 * 0.6 - 0.05)) < 1e-9, "a winning Down bet earns the YES price less fee")
FakeEx.fill = 0; m = sig(b, eng, slug="n1"); ok("n1" not in b.st["pos"] and not os.path.exists(b._path("HALT")), "nothing filled: no position, no halt")
FakeEx.fill = 2.0; m = sig(b, eng, slug="p1"); ok(b.st["pos"]["p1"]["qty"] == 2.0, "a partial fill books only what filled")

# 5. limits
b, eng = bot(day_loss_usd=1.0); FakeEx.armed_flag = True
m = sig(b, eng); b.on_settle(m, False); n0 = len(FakeEx.sent); sig(b, eng, slug="x2")
ok(len(FakeEx.sent) == n0, "daily loss stop: no further order that day")
b, eng = bot(total_loss_usd=1.0); FakeEx.armed_flag = True
m = sig(b, eng); b.on_settle(m, False); sig(b, eng, slug="x2"); ok(os.path.exists(b._path("HALT")) and b.mode() == "HALTED", "total loss stop halts the bot")
b, eng = bot(); FakeEx.armed_flag = True
eng.mf.markets["old"] = M("old", 0.39, 0.40); b._entry(("old", "buy", 0.40, 5.0, time.time(), "t"), time.perf_counter() - 1.0)
ok(not FakeEx.sent, "a signal older than the age cap is dropped")
b, eng = bot(max_bets_day=1); FakeEx.armed_flag = True
sig(b, eng); sig(b, eng, slug="x2"); ok(len([1 for k, _ in FakeEx.sent if k == "order"]) == 1, "bets-a-day cap")
b, eng = bot(); FakeEx.armed_flag = True; open(b._path("STOP"), "w").write("x")
sig(b, eng); ok(all(k != "order" for k, _ in FakeEx.sent), "STOP file: no order even though armed")
b, eng = bot(); b.bal = {"cash": 0.004, "buying_power": 0.004, "open_orders": 0}; FakeEx.armed_flag = True
ok(b.bet_usd(True) == 0.0, "a balance below the smallest bet means no bet")
b, eng = bot(); ok(b.bet_usd(True) == 1.0, "$100 balance: 1% is $1"); b.bal = {"buying_power": 2000.0}; ok(b.bet_usd(True) == 5.0, "bet is capped at $5")

# 6. things going wrong
b, eng = bot(); FakeEx.armed_flag = True; FakeEx.lie = 3.0
sig(b, eng); ok(os.path.exists(b._path("HALT")), "the answer and the exchange's position disagree: halt")
b, eng = bot(max_errors=2); FakeEx.armed_flag = True; FakeEx.fail = 400
sig(b, eng); h1 = os.path.exists(b._path("HALT")); sig(b, eng, slug="x2")
ok(not h1 and os.path.exists(b._path("HALT")) and not b.st["pos"], "repeated refusals halt the bot; no position is booked")

# 7. the protective exit
b, eng = bot(); FakeEx.armed_flag = True
m = sig(b, eng); b._exit((m.slug, "sell", 0.30, 5.0, time.time(), "exit"), time.perf_counter())
ok(FakeEx.sent[-1][1]["intent"] == "ORDER_INTENT_SELL_LONG" and m.slug in b.st["pos"] and b.st["pos"][m.slug]["qty"] == 0.0, "exit sells the Up position at the bid")
b.on_settle(m, True); ok(abs(b.st["total_pnl"] - (5 * (0.30 - 0.40) - 0.10)) < 1e-9, "an exited bet's result is the exit price less entry and both fees")

# 8. reading the documented answer shapes
ok(live.parse_fill({"id": "1", "executions": []}) is None and live.parse_fill("oops") is None, "an answer without the order is treated as unknown")
ok(live.parse_fill({"order": {"state": "ORDER_STATE_CANCELED", "cumQuantity": 0}})[0] == 0, "a cancelled order reads as nothing filled")
print(f"\nall {n} checks passed")
