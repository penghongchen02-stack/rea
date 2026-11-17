"""Checks of the shadow xarb_fast (paperbot/livexarb.py) against stand-ins: no network. Run: python3 test_livexarb.py"""
import time, tempfile, types
from paperbot import livexarb, feeds
feeds.get_json = lambda *a, **k: None          # no network in these checks

class Ex:
    code = 200
    def __init__(self, d=None): pass
    def preview(self, body): Ex.sent.append(body); return Ex.code, {"order": body}, 80.0
    def call(self, *a, **k): return 200, {}, 1.0
Ex.sent = []

class M:
    def __init__(self, slug, bids, offers, kind="updown"):
        self.slug = slug; self.bids = bids; self.offers = offers; self.kind = kind; self.min_qty = 0.01; self.consumed = {}; self.end = time.time() + 100; self.asset = "btc"
    @property
    def best_bid(self): return self.bids[0][0] if self.bids else None
    @property
    def best_ask(self): return self.offers[0][0] if self.offers else None

def bot(**cfg):
    d = tempfile.mkdtemp(); c = dict(livexarb.DEFAULTS); c.update(cfg); Ex.sent = []; Ex.code = 200
    eng = types.SimpleNamespace(mf=None, lf=types.SimpleNamespace(fetch_book=lambda m, max_age=0: True), idx=None, spot=None)
    return livexarb.ShadowXArb(eng, d, c, exchanges=[Ex(), Ex()])

n = 0
def ok(c, what):
    global n; n += 1; assert c, what; print("ok", what)
legs = lambda a, b, pa, pb: [(a, "buy", pa, "x"), (b, "sell", pb, "y")]

# both quotes still there: a shadow set, the locked profit, and the dominance payoff at settlement
b = bot(); A = M("a", [(0.39, 50)], [(0.40, 50)]); B = M("b", [(0.50, 50)], [(0.51, 50)])
r = b.execute(legs(A, B, 0.40, 0.50), 10, ("a", "b"), "t", 0.05); T = b.st["tot"]
ok(r and T["both"] == 1 and T["sets"] == 10 and len(Ex.sent) == 2, "both quotes still there: one shadow set, two previews, nothing else sent")
ok({x["intent"] for x in Ex.sent} == {"ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"}, "the two legs are a buy of one contract and a sale of the other")
fee = round(0.095 * 0.4 * 0.6 * 10, 2) + round(0.095 * 0.5 * 0.5 * 10, 2)
ok(abs(T["locked"] - (10 * 0.10 - fee)) < 1e-9, "locked profit is the gap less both fees")
b.on_settle(A, True); b.on_settle(B, True); ok(abs(b.st["tot"]["pnl"] - (10 * 0.10 - fee)) < 1e-9 and not b.st["lots"], "both pay: the result is the locked profit")
b = bot(); b.execute(legs(A, B, 0.40, 0.50), 10, ("a", "b"), "t", 0.05); b.on_settle(A, True); b.on_settle(B, False)
ok(abs(b.st["tot"]["pnl"] - (10 * 0.10 - fee + 10)) < 1e-9, "the bought one pays and the sold one does not: locked profit plus $1 a set")
b = bot(); b.execute(legs(A, B, 0.40, 0.50), 10, ("a", "b"), "t", 0.05); b.on_settle(A, False); b.on_settle(B, False)
ok(abs(b.st["tot"]["pnl"] - (10 * 0.10 - fee)) < 1e-9, "neither pays: still the locked profit")

# the size limit
b = bot(max_usd_per_arb=9.0); b.execute(legs(A, B, 0.40, 0.50), 50, ("a", "b"), "t", 0.05)
ok(b.st["tot"]["sets"] == 10.0, "one arbitrage ties up at most the limit ($9 at 90c a set is 10 sets)")
b = bot(shadow_bank_usd=30.0)
for i in range(3): b.execute(legs(M("a%d" % i, [(0.39, 50)], [(0.40, 50)]), M("b%d" % i, [(0.50, 50)], [(0.51, 50)]), 0.40, 0.50), 50, (i,), "t", 0.05)
ok(b.open_cost() <= 30.0 + 1e-9 and b.st["tot"]["sets"] < 34, "open shadow lots never tie up more than the bank")

# quotes gone
b = bot(); A2 = M("a", [(0.44, 50)], [(0.45, 50)]); B2 = M("b", [(0.50, 50)], [(0.51, 50)])
r = b.execute(legs(A2, B2, 0.40, 0.50), 10, ("a", "b"), "t", 0.05); T = b.st["tot"]
ok(not r and T["one_leg"] == 1 and T["sets"] == 0 and not b.st["lots"], "the buy quote moved away: no set, counted as one-legged")
ok(abs(T["leg_cost"] - (10 * 0.01 + round(0.095 * 0.25 * 10, 2) + round(0.095 * 0.51 * 0.49 * 10, 2))) < 1e-9 and T["pnl"] == -T["leg_cost"], "the lone sale is bought back at the ask: spread and two fees are the cost")
b = bot(); r = b.execute(legs(M("a", [(0.44, 5)], [(0.45, 5)]), M("b", [(0.45, 5)], [(0.46, 5)]), 0.40, 0.50), 10, ("a", "b"), "t", 0.05)
ok(not r and b.st["tot"]["neither"] == 1 and b.st["tot"]["leg_cost"] == 0, "both quotes gone: nothing happens and nothing is lost")
b = bot(); r = b.execute(legs(M("a", [(0.39, 50)], [(0.40, 4)]), M("b", [(0.50, 50)], [(0.51, 50)]), 0.40, 0.50), 10, ("a", "b"), "t", 0.05); T = b.st["tot"]
ok(T["sets"] == 4 and T["both"] == 1 and T["leg_cost"] > 0 and not r, "one leg only partly there: the smaller size is the set, the surplus is unwound")
b = bot(); Ex.code = 400; r = b.execute(legs(A, B, 0.40, 0.50), 10, ("a", "b"), "t", 0.05)
ok(not r and b.st["tot"]["sets"] == 0, "a preview the exchange refuses books nothing")
src = open(livexarb.__file__).read()
ok(".place(" not in src and '"/v1/orders"' not in src and "ARMED" not in src, "the module has no call that sends an order")
print(f"\nall {n} checks passed")
