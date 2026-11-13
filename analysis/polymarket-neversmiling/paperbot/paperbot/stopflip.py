"""Stop-and-flip bots (user, 11-13 09:30Z): fifteen versions of one idea on their own fast dashboard (port 8092, $1,000
each, the bot chooses its own bet).

The idea: in a 5-minute window, with 3 to 5 minutes left, back a side; if the position loses a set share of its value,
get out (the stop); optionally back the other side (the flip), optionally with a bet sized to win back what the window
has lost so far (the recovery), up to a cap per window. Everything is checked on every pass of the fast loop.

Corrected confidence. The model's chance (fair.p_up) is only used to pick the side. How likely that side is to win
comes from what the lab's 2,185 distinct mid-window entries actually did (paperbot-data/bonelab, 11-10 to 11-13; each
was an entry where the model claimed at least 3c of edge after fees): a side bought below 40c won 12-17c LESS often
than its price, at 40-50c 3c more, at 50-60c 11c more, at 60-70c about 15c more. CAL holds that as win rate minus
price, by price; a bot here enters only if that corrected chance beats the price and the fee by MIN_EDGE, which in
practice means the side already ahead (priced about 47c or more); since 11:00Z a first bet is also limited to
ENTRY_BAND and the bots trade Bitcoin and Ether only (SOL lost most in the first hour: wider gaps, bigger jumps). Late in a window the model said 95-100% and those
bets won 92-93% whatever it said (4,100 entries of the late bots), so the corrected chance is capped at CAP.

Sizes: "conf" bets $2 at the minimum edge up to $20 at 10c of corrected edge; "bank" bets 0.5%-2% of the settled bank
the same way and halves it after three losing windows in a row. No window may hold more than WINDOW_CAP of the bank.
A bet is only placed while the gap between the side's buying and selling price is under half the stop's distance.
Fees are about 2.4c a contract each way near 50c, so a 5% stop near 50c costs about three times the 5% it shows.
"""
import math, time
from .strategies import Base
from .fair import p_up

CAL = ((0.25, -0.14), (0.35, -0.12), (0.45, 0.03), (0.55, 0.10), (0.65, 0.15), (0.80, 0.10))   # (price, win rate - price)
CAP = 0.93; MIN_EDGE = 0.02; MODEL_EDGE = 0.03; WINDOW_CAP = 0.05
# First bets only between these prices (user, 11-13 11:00Z). In the first hour the bots paid 65c on average for sides that
# won 12 of 23 distinct windows: CAL's figures above 60c rest on 63 cases (against 1,660 for 40-60c) and were trusted too
# far. A flip after a stop is not bound by this; it takes the other side at whatever it costs.
ENTRY_BAND = (0.47, 0.60)


def corrected(x):
    """Chance that a side the model favours wins, given its price x (see the module note)."""
    if x <= CAL[0][0]: g = CAL[0][1]
    elif x >= CAL[-1][0]: g = CAL[-1][1]
    else:
        for (x0, g0), (x1, g1) in zip(CAL, CAL[1:]):
            if x0 <= x <= x1: g = g0 + (g1 - g0) * (x - x0) / (x1 - x0); break
    return min(CAP, x + g)


class StopFlip(Base):
    def __init__(self, account, name, stop=0.05, flips=0, recover=0.0, flip_if=None, sizing="conf", window=(180, 290), tp=None,
                 rest=False, assets=("btc", "eth"), two_step=False, day_stop=None):
        super().__init__(account)
        self.name = name; self.stop = stop; self.flips = flips; self.recover = recover; self.flip_if = flip_if; self.sizing = sizing
        self.t_lo, self.t_hi = window; self.tp = tp; self.rest = rest; self.assets = set(assets); self.two_step = two_step; self.day_stop = day_stop

    # ---- helpers -----------------------------------------------------------------
    def fee(self, x): return self.acct.theta * x * (1 - x)

    def side_px(self, market, side):
        """(price to buy that side now, price to sell it now), in that side's own cents."""
        bb, ba = market.best_bid, market.best_ask
        return (ba, bb) if side == "up" else (1 - bb, 1 - ba)

    def tight(self, market, side):
        """The book is tight enough to trade this side: the gap between its buying and selling price is under half the
        stop's distance. Otherwise the stop would fire the moment the bet is on (first seen on SOL, a 5c gap)."""
        buy, sell = self.side_px(market, side)
        return buy - sell <= 0.5 * self.stop * buy + 1e-9

    def edge(self, market, side, p):
        """Corrected edge of buying `side` now, or None if the model does not favour it by MODEL_EDGE."""
        buy, _ = self.side_px(market, side); pm = p if side == "up" else 1 - p
        if not 0.05 <= buy <= 0.95 or pm - buy - self.fee(buy) < MODEL_EDGE: return None
        return corrected(buy) - buy - self.fee(buy)

    def first_edge(self, market, side, p):
        """edge(), for a window's first bet: only inside ENTRY_BAND."""
        buy, _ = self.side_px(market, side)
        return self.edge(market, side, p) if ENTRY_BAND[0] <= buy <= ENTRY_BAND[1] else None

    def dollars(self, e):
        k = min(1.0, max(0.0, e - MIN_EDGE) / 0.08)
        if self.sizing == "bank":
            usd = (0.005 + 0.015 * k) * self.acct.bank()
            last = [r for r in self.acct.settlements if r.get("fills")][-3:]
            if len(last) == 3 and all(r["pnl_total"] - r.get("fees", 0.0) < 0 for r in last): usd *= 0.5
            return usd
        return 2.0 + 18.0 * k

    def day_loss(self, now):
        day0 = now - now % 86400
        return -sum(r.get("pnl_total", 0.0) - r.get("fees", 0.0) for r in self.acct.settlements if r.get("ts", 0) >= day0)

    def buy_side(self, market, side, usd, tag, rest=False):
        buy, _ = self.side_px(market, side); acct = self.acct
        pos = acct.positions.get(market.slug); held = abs(pos.qty) * buy if pos else 0.0
        usd = min(usd, WINDOW_CAP * acct.bank() - held)
        qty = round(usd / buy, 2)
        if usd <= 0 or qty < market.min_qty: return None
        s = "buy" if side == "up" else "sell"
        if rest:      # wait at the touch: no fee if someone trades into it
            px = market.best_bid if s == "buy" else market.best_ask
            return acct.place(market, s, px, qty, post_only=True, tag=tag + " rest", fixed=True)
        return acct.take(market, s, qty, limit=market.best_ask if s == "buy" else market.best_bid, tag=tag, fixed=True)

    # ---- the loop ----------------------------------------------------------------
    def on_tick(self, market, ctx, now):
        if market.asset not in self.assets or market.dur != 300: return
        left = market.remaining(now)
        if left < 3 or market.state not in (None, "MARKET_STATE_OPEN") or market.best_bid is None or market.best_ask is None: return
        slug = market.slug; acct = self.acct
        st = self.mem.setdefault(slug, {"flips": 0, "next": 0.0, "want": None, "done": False, "added": False, "rest_at": None})
        pos = acct.positions.get(slug); q = pos.qty if pos else 0.0

        if abs(q) > 1e-9:                                   # holding: stop, take-profit, second step
            side = "up" if q > 0 else "down"; paid = pos.avg if q > 0 else 1 - pos.avg
            buy, sell = self.side_px(market, side)
            hit = "stop" if sell <= paid * (1 - self.stop) else ("tp" if self.tp is not None and sell >= paid * (1 + self.tp) else None)
            if hit:
                acct.cancel_all(slug)
                acct.take(market, "sell" if q > 0 else "buy", round(abs(q), 2), limit=None, tag=f"sf-{hit} {sell:.2f} from {paid:.2f}", fixed=True)
                p2 = acct.positions.get(slug)
                if p2 is None or abs(p2.qty) < 1e-9:      # fully out
                    if hit == "stop" and st["flips"] < self.flips and left > 20: st["want"] = "down" if side == "up" else "up"
                    else: st["done"] = True
                return
            if self.two_step and not st["added"] and buy >= paid + 0.03 and left > 30:      # it moved our way: add the other half
                st["added"] = True; self.buy_side(market, side, abs(q) * paid, f"sf-add {buy:.2f}")
            return

        if st["done"] or now < st["next"]: return
        st["next"] = now + 0.5
        if st["rest_at"] and now - st["rest_at"] > 20: acct.cancel_all(slug); st["rest_at"] = None      # a waiting entry that did not fill
        if acct.open_orders(slug): return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None: return

        if st["want"]:                                      # the flip after a stop
            side = st["want"]; buy, _ = self.side_px(market, side)
            if left < 20 or not 0.05 <= buy <= 0.95: st["done"] = True; return
            if not self.tight(market, side): return                                    # wait for a tighter book
            if self.flip_if == "trend":
                spot = ctx["spot"]; px, ts = spot.last(market.asset); old = spot.at(market.asset, now - 60)
                if not px or not old or (px > old) != (side == "up"): return           # wait for the coin itself to move that way
            elif self.flip_if == "conf":
                e = self.edge(market, side, p)
                if e is None or e < MIN_EDGE: return
            lost = max(0.0, -(pos.realized - pos.fees)) if pos else 0.0
            if self.recover > 0:      # sized so that a win pays back this share of what the window has lost so far
                usd = max(self.dollars(MIN_EDGE), self.recover * lost / max(0.05, 1 - buy - self.fee(buy)) * buy)
            else:
                e = self.edge(market, side, p); usd = self.dollars(e if e is not None else MIN_EDGE)
            o = self.buy_side(market, side, usd, f"sf-flip{st['flips'] + 1} {side} {buy:.2f} lost {lost:.2f}")
            if o is not None and o.filled > 0: st["flips"] += 1; st["want"] = None
            elif o is None: st["done"] = True              # the window's cap is used up
            return

        if not self.t_lo <= left <= self.t_hi: return      # first entry
        if self.day_stop is not None and self.day_loss(now) >= self.day_stop * acct.start_cash: return
        best = max(((self.first_edge(market, s, p), s) for s in ("up", "down")), key=lambda t: -1 if t[0] is None else t[0])
        e, side = best
        if e is None or e < MIN_EDGE or not self.tight(market, side): return
        usd = self.dollars(e) * (0.5 if self.two_step else 1.0)
        o = self.buy_side(market, side, usd, f"sf-in {side} e{e * 100:.0f}c", rest=self.rest)
        if o is not None and self.rest and o.filled <= 0: st["rest_at"] = now


VERSIONS = {
    # name: (what it does, settings)
    "sf01_stop_and_wait": ("5% stop, no flip", {}),
    "sf02_flip_once": ("5% stop, then backs the other side once", {"flips": 1}),
    "sf03_flip_and_win_it_back": ("5% stop, up to 3 flips, each sized to win back the window's loss", {"flips": 3, "recover": 1.0}),
    "sf04_flip_only_with_the_trend": ("5% stop, up to 2 recovery flips, only once the coin has moved that way over the last minute", {"flips": 2, "recover": 1.0, "flip_if": "trend"}),
    "sf05_grow_when_winning_shrink_when_losing": ("bets 0.5-2% of the bank, halved after 3 losing windows; 5% stop, one flip", {"flips": 1, "sizing": "bank"}),
    "sf06_wide_stop_no_flip": ("15% stop, no flip", {"stop": 0.15}),
    "sf07_wide_stop_flip_once": ("15% stop, one flip", {"stop": 0.15, "flips": 1}),
    "sf08_win_back_half": ("10% stop, up to 2 flips sized to win back half the loss", {"stop": 0.10, "flips": 2, "recover": 0.5}),
    "sf09_take_profit": ("10% stop, sells at a 15% gain, no flip", {"stop": 0.10, "tp": 0.15}),
    "sf10_flip_only_if_confident": ("10% stop, one flip, only if the corrected chance favours the other side", {"stop": 0.10, "flips": 1, "flip_if": "conf"}),
    "sf11_waiting_entry_no_fee": ("first bet waits at the touch (no fee); 10% stop, one flip", {"stop": 0.10, "flips": 1, "rest": True}),
    "sf12_bitcoin_only_trend_flip": ("Bitcoin only; 10% stop, up to 2 recovery flips with the trend", {"stop": 0.10, "flips": 2, "recover": 1.0, "flip_if": "trend", "assets": ("btc",)}),
    "sf13_later_start": ("enters with 2-3 minutes left; 10% stop, one flip", {"stop": 0.10, "flips": 1, "window": (120, 180)}),
    "sf14_half_now_half_if_right": ("half the bet first, the rest once it is 3c ahead; 10% stop, no flip", {"stop": 0.10, "two_step": True}),
    "sf15_trend_flip_with_a_day_limit": ("as sf04, and no new windows after losing 5% of the bank in a day", {"flips": 2, "recover": 1.0, "flip_if": "trend", "day_stop": 0.05}),
}
LABELS = {k: v[0] for k, v in VERSIONS.items()}


def build_stopflip_registry():
    return {k: (lambda acct, k=k, kw=kw: StopFlip(acct, k, **kw)) for k, (_, kw) in VERSIONS.items()}
