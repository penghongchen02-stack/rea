"""Combo (user, 11-12 01:20Z): one account running the three approaches whose edge is in the prices themselves rather
than in guessing direction, with the replicas' risk guards.

1. Arbitrage: XArbFast's cross-window dominance (a window and a strike or longer window that settle on the same number,
   priced apart by more than both fees plus 0.3c); both legs are taken together, so the profit is locked.
2. Settlement lock: LockEarly (from 25 published settlement seconds, the result bounded 4 sd each way, 1c edge).
3. Swing pairs the rest of the time: the FlippingSharks rebuild (buy the cheap side at <= 40c, buy the other side once
   the pair costs <= 90c), BTC 5m and 15m, without its hour and weekend filters (those were its report's, in-sample).

Guards: every piece is sized to at most RISK_FRAC of the bank (arbitrage sets, lock contracts and the swing cap), and
after settled losses of DAY_STOP of the bank in one UTC day nothing new is opened until the next day. The scanners'
books come from the engine's shared snapshots within their own per-second budgets, so the combo adds no polling of its
own beyond theirs. Arbitrage and swing can hold the same window; the account nets them, which leaves the P/L right.
"""
from .reports import FlippingSharks
from .arbs import XArbFast
from .locks import LockEarly


class Combo(FlippingSharks):
    name = "combo"; HOURS_OFF = (); DAYS_OFF = (); REWARD_RATE = 0.0

    def __init__(self, account, engine):
        super().__init__(account)
        cap = self.RISK_FRAC * account.start_cash            # dollars per arbitrage pass / lock
        self.arb = XArbFast(account, engine); self.arb.MAX_SETS = max(1.0, min(XArbFast.MAX_SETS, cap))
        self.lock = LockEarly(account, engine); self.lock.MAX_SETS = max(1.0, min(LockEarly.MAX_SETS * self.scale, cap))

    def on_tick(self, market, ctx, now):
        if not self.stopped_today(now):                      # the scanners limit themselves to one pass a second
            self.arb.scan(now); self.lock.scan(now)
        super().on_tick(market, ctx, now)

    def snapshot(self): return {"arb": self.arb.snapshot(), "lock": self.lock.snapshot()}
