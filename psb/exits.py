"""#19 versions that lock in a profit instead of always holding to the end.

On Polymarket a held player is cashed out by buying the same number of
contracts of the other player: one of each pays exactly $1.00 whatever
happens, so the profit is fixed at $1.00 minus what the pair cost. These
bots do that once per match (finishing a part-filled lock on later polls),
then make no new entries in it:

  * s19-lock70: lock when the held player's international price reaches 70c.
  * s19-trail:  lock when the held player has risen 25c+ above what we paid
                and then falls 10c from his best price since.

Both lock only when the pair costs at most 97c after the fee, so the locked
result is at least +3c per contract. Entries are #19's, unchanged.

A stop (`stop_loss`, used by some Live_19 versions in live19.py) does the same
buy when the held player falls that far below what we paid, accepting the
loss it fixes; its buys are tagged "stop".
"""
from __future__ import annotations

from . import fees
from .bots import Take, View
from .simple_bots import S19TennisSetFade

LOCK_TAG = "lock"
STOP_TAG = "stop"


def _other(side: str) -> str:
    return "short" if side == "long" else "long"


class ProfitLock:
    """Mixin: before #19's own step, check whether to lock the held player."""
    lock_at: float | None = None      # lock once the held side's fair reaches this
    trail_rise: float | None = None   # ...or once it rose this far above our cost
    trail_drop: float = 0.10          # ...and then fell this far from its peak
    min_profit: float = 0.03          # locked profit per contract, after the fee
    stop_loss: float | None = None    # cut the bet once fair is this far under our cost

    def step(self, v: View, st: dict):
        # Once triggered, a lock or stop keeps buying the rest (a thin book or a
        # part fill can leave some open) and the match gets no new entries.
        # st["locking"] is True for a lock and "stop" for a stop.
        mode = st.get("locking") or None
        side = v.held_side()
        if side is not None:
            n = abs(v.held)
            held_n, avg = self.leg(st, side)
            f = v.fair(side)
            st["peak"] = max(st.get("peak", f), f)
            want = mode
            if want is None and self._should_lock(f, avg, st["peak"]):
                want = True
            if want is None and self.stop_loss is not None and f <= avg - self.stop_loss + 1e-9:
                want = "stop"
            if want is not None:
                act = self._lock(v, side, n, held_n - self.spent(st), stop=want == "stop")
                if act is not None:
                    st["locking"] = want
                    return [act]
        if mode:
            return []
        return super().step(v, st)

    def _should_lock(self, fair: float, avg: float, peak: float) -> bool:
        if self.lock_at is not None and fair >= self.lock_at:
            return True
        return (self.trail_rise is not None and peak >= avg + self.trail_rise
                and fair <= peak - self.trail_drop)

    def _lock(self, v: View, side: str, n: float, margin: float, stop: bool = False) -> Take | None:
        """Buy n of the other side. `margin` is the held side's gross contracts
        minus everything spent on this match: after the lock every outcome pays
        the gross contracts, so the result is margin minus what the lock costs.
        A stop buys whatever result that fixes; a lock only a min_profit one."""
        o = _other(side)
        a = v.ask(o)
        if a is None or n < 1e-9:
            return None
        if not stop and a + fees.taker_fee(1, a, v.taker_theta) > margin / n - self.min_profit + 1e-9:
            return None   # locking now would fix less than min_profit: keep holding
        # Buy at the best ask only, for at most n contracts; a thinner book is
        # finished on later polls rather than paying up a level.
        return Take(o, n * a, a, STOP_TAG if stop else LOCK_TAG)


def _make(key: str, title: str, **settings):
    cls = type(f"X_{key.replace('-', '_')}", (ProfitLock, S19TennisSetFade),
               {"key": key, "title": title, "rules": S19TennisSetFade().rules_key, **settings})
    return cls


S19Lock70 = _make("s19-lock70", "#19 lock-in at 70c", lock_at=0.70)
S19Trail = _make("s19-trail", "#19 trailing lock-in (25c up, 10c off peak)",
                 trail_rise=0.25, trail_drop=0.10)

EXITS = (S19Lock70(), S19Trail())
