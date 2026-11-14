"""Polymarket US versions of nine crypto traders from polyresearchrobotics.com/reports (user, 11-14 05:40Z), each on
its own $1,000 tryout account. Names start with "new_rep_". They join the builder source "new".

What the traders do on regular Polymarket: buy Up and Down in the same window at different moments, more on the side
they believe in, and hold to the close. The reports' own numbers say the profit is the lean, not the pair: the larger
side wins 80-97% of the time when it is at least twice the smaller, while the spread on pairs is small or negative
and the smaller side is a cost. Their return is 0.3-3% of volume, under the Polymarket US taker fee (about 2.4c a
contract at 50c), so a straight copy that takes offers would lose.

The five changes that make it fit Polymarket US (shown to the user before building):
  1. One contract a market: the Up leg is a buy, the Down leg is a sell. A pair is a buy and a sell at two prices.
  2. Resting orders only to get in (free here). The one taker order is the late close of rule 5.
  3. The buy and the sell are at least 4c apart (2c each side of the model's fair price): no rewards top up a thin gap.
  4. Once a side has filled, no more is added to it unless the model favours that side (the lean, capped at TILT of
     the bank); the other side is worked closer to fair to finish the pair.
  5. Orders are pulled for PAUSE seconds when the coin moves fast, and with FLAT seconds left whatever is held on a
     side the model gives under HOLD is closed; a side the model favours is held to the close (the traders' lean).

Each trader's coins, window lengths, price range, order size, timing and lean come from their report. What the
reports do not give is the split between resting and taking orders, so the resting-only rule is this build's choice.
Sharky6999 is not a both-sides trader: 97% of their buys are at 90c or more, so their copy rests one bid on the
near-certain side late in a window.

Paper fills of resting orders are easier than real ones, so these results are the best case.
"""
import math, time
from .newbots import _New

PAUSE = 5.0          # seconds without orders after a fast move
FAST = 3.0           # a 10-second move this many times the usual size is fast


class PairMaker(_New):
    """clip: share of the bank in one order; tilt: most of the bank at risk on the side the model favours (the other
    side never holds more than one clip); conv: how far from 50% the model must be to favour a side; margin: cents
    from fair for a new order (half that for the order that finishes a pair); band: what a side may cost; el/stop_el:
    seconds into the window it starts/stops adding; left: seconds left at which adding stops; flat: seconds left at
    which orders are pulled and an unfavoured holding is closed; hold: the model's probability at which a holding is
    kept to the close; hours: UTC hours it trades (None = all); only_fav: quote only the favoured side and never the
    other (the near-certain buyer)."""
    def __init__(self, account, name, clip=0.01, tilt=0.03, conv=0.10, margin=0.02, band=(0.10, 0.90), el=15, stop_el=None, left=45,
                 flat=30, hold=0.60, hours=None, only_fav=False, **kw):
        super().__init__(account, name, **kw)
        self.clip = clip; self.tilt = max(tilt, clip); self.conv = conv; self.margin = max(margin, 0.02); self.band = band
        self.el = el; self.stop_el = stop_el; self.left = left; self.flat = flat; self.hold = hold; self.hours = hours; self.only_fav = only_fav

    @staticmethod
    def level(levels, limit, side):
        """The best resting price that is not beyond `limit`."""
        limit = round(limit, 4)
        for px, _ in levels[:6]:
            if (px <= limit + 1e-9) if side == "buy" else (px >= limit - 1e-9): return px
        return round(limit, 2)

    def pull(self, slug, st):
        if st["q"] is not None: self.acct.cancel_all(slug); st["q"] = None

    def on_tick(self, market, ctx, now):
        if market.asset not in self.assets or market.dur not in self.durs: return
        slug = market.slug; a = self.acct; st = self.mem.setdefault(slug, {"q": None, "pause": 0.0, "shut": False})
        el = now - market.start; left = market.remaining(now)
        pos = a.positions.get(slug); inv = pos.qty if pos and not pos.closed else 0.0
        if not self.ready(market, now) or el < self.el: self.pull(slug, st); return
        p, det = self.fair(market, ctx, now)
        if p is None: self.pull(slug, st); return
        bb, ba = market.best_bid, market.best_ask
        if left < self.flat:                                          # rule 5: the end of the window
            self.pull(slug, st)
            if abs(inv) >= max(market.min_qty, 0.01) and not st["shut"] and left > 2:
                side_p = p if inv > 0 else 1 - p
                if side_p < self.hold:
                    o = a.take(market, "sell" if inv > 0 else "buy", round(abs(inv), 2), limit=bb if inv > 0 else ba, tag=f"{self.name} close unpaired {side_p:.2f}", fixed=True)
                    st["shut"] = bool(o and o.filled > 0)
                else: st["shut"] = True                               # the favoured side is held to the close
            return
        mv = self.trend(ctx, market.asset, now, 10); sig = det.get("sigma_1s") or 0.0
        if mv is not None and sig > 0 and abs(mv) > FAST * sig * math.sqrt(10): st["pause"] = now + PAUSE      # rule 5: fast move
        if now < st["pause"]: self.pull(slug, st); return
        adding = left >= self.left and (self.stop_el is None or el <= self.stop_el) and (self.hours is None or time.gmtime(now).tm_hour in self.hours)
        bank = a.bank(); risk = self.held(slug); d = self.clip * bank
        fav = 1 if p >= 0.5 + self.conv else -1 if p <= 0.5 - self.conv else 0
        bid = min(self.level(market.bids, p - (self.margin / 2 if inv < 0 else self.margin), "buy"), ba - 0.01)
        ask = max(self.level(market.offers, p + (self.margin / 2 if inv > 0 else self.margin), "sell"), bb + 0.01)
        bq = aq = 0.0
        if inv < 0 and not self.only_fav: bq = abs(inv)                             # rule 4: finish the pair
        elif adding and inv >= 0 and self.band[0] <= bid <= self.band[1] and (fav > 0 or not self.only_fav):
            room = (self.tilt if fav > 0 else self.clip) * bank - risk              # more than one clip only on the favoured side
            if room >= 0.25 * d: bq = min(d, room) / bid
        if inv > 0 and not self.only_fav: aq = inv
        elif adding and inv <= 0 and self.band[0] <= 1 - ask <= self.band[1] and (fav < 0 or not self.only_fav):
            room = (self.tilt if fav < 0 else self.clip) * bank - risk
            if room >= 0.25 * d: aq = min(d, room) / (1 - ask)
        bq = round(bq, 2); aq = round(aq, 2); lo = max(market.min_qty, 0.01)
        q = ((round(bid, 2), bq) if bq >= lo and 0.02 <= bid <= 0.98 else None, (round(ask, 2), aq) if aq >= lo and 0.02 <= ask <= 0.98 else None)
        if q != st["q"]:
            a.cancel_all(slug); st["q"] = q
            if q[0]: a.place(market, "buy", q[0][0], q[0][1], post_only=True, tag=f"{self.name} bid fair {p:.2f}", fixed=True)
            if q[1]: a.place(market, "sell", q[1][0], q[1][1], post_only=True, tag=f"{self.name} ask fair {p:.2f}", fixed=True)


ALL = ("btc", "eth", "sol", "xrp")
# name: (what it does, settings). The figures in each line are the trader's, from their report.
BOTS = {
    "new_rep_ohanism": ("NEW copy of Ohanism (+3.0% on $8.57M; all four coins, 5- and 15-minute; one side only in two windows of three, mostly bought at 60-90c): leans hard, up to 5% of the bank on the favoured side",
                        {"assets": ALL, "durs": (300, 900), "clip": 0.01, "tilt": 0.05, "conv": 0.08, "band": (0.10, 0.92), "el": 30, "hold": 0.55}),
    "new_rep_gabigol": ("NEW copy of Gabigol (+2.1% on $1.45M; Bitcoin, Ether, Solana 5-minute; tiny orders, 60% of money at 90c+): half-size orders, any price 3-97c, leans to 4%",
                        {"assets": ("btc", "eth", "sol"), "clip": 0.005, "tilt": 0.04, "conv": 0.10, "band": (0.03, 0.97), "el": 15, "left": 20, "flat": 15}),
    "new_rep_ratue": ("NEW copy of Ratue (+1.9% on $0.99M; Bitcoin, Ether, Solana 15-minute only; second side four minutes after the first): patient 3c-from-fair orders, leans to 4%",
                      {"assets": ("btc", "eth", "sol"), "durs": (900,), "clip": 0.008, "tilt": 0.04, "conv": 0.10, "margin": 0.03, "band": (0.03, 0.97), "el": 45, "left": 60, "flat": 45}),
    "new_rep_b1c58": ("NEW copy of B1C58 (+1.6% on $1.76M; Bitcoin 5-minute only; both sides in 99% of windows, near balance, most buys 30-60c): balanced pairs, lean capped at 2%",
                      {"assets": ("btc",), "clip": 0.01, "tilt": 0.02, "conv": 0.12, "band": (0.15, 0.85), "el": 15, "left": 30}),
    "new_rep_080bec": ("NEW copy of 080BEC (+1.5% on $1.75M; Bitcoin 5-minute only, from 15 s after the open to 30 s before the close; the twin of B1C58): the same with a 6c gap between its buy and sell",
                       {"assets": ("btc",), "clip": 0.01, "tilt": 0.02, "conv": 0.12, "margin": 0.03, "band": (0.15, 0.85), "el": 15, "left": 30}),
    "new_rep_sixx7": ("NEW copy of Sixx7 (+0.9% on $2.21M; Bitcoin, Solana, XRP 5- and 15-minute; $2.50 orders, best at 40-50c, lost under 30c): small orders at 30-70c only",
                      {"assets": ("btc", "sol", "xrp"), "durs": (300, 900), "clip": 0.004, "tilt": 0.02, "conv": 0.10, "band": (0.30, 0.70), "el": 20}),
    "new_rep_vidarx": ("NEW copy of VidarX (+0.9% on $11.99M; second side 10 s after the first, most buys 20-70c, 27% of windows leaned 3 to 1): all four coins 5-minute, leans to 3%",
                       {"assets": ALL, "clip": 0.01, "tilt": 0.03, "conv": 0.10, "band": (0.10, 0.90), "el": 15}),
    "new_rep_0x04b6d7": ("NEW copy of 0x04B6D7 (+0.7% on $5.03M; all four coins 5- and 15-minute; in within seconds of the open, done in two minutes, 04:00-20:00 UTC only): orders in the first two minutes, those hours only",
                         {"assets": ALL, "durs": (300, 900), "clip": 0.01, "tilt": 0.03, "conv": 0.10, "band": (0.05, 0.95), "el": 10, "stop_el": 120, "hours": tuple(range(4, 20))}),
    "new_rep_sharky6999": ("NEW copy of Sharky6999 (+0.3% on $21.26M; 97% of buys at 90c or more, wins 98.9%): rests one bid at 90-98c on the near-certain side in the last two minutes, held to the close",
                           {"assets": ALL, "clip": 0.02, "tilt": 0.02, "conv": 0.43, "band": (0.90, 0.98), "el": 180, "left": 10, "flat": 0, "hold": 0.0, "only_fav": True}),
}
LABELS = {k: v[0] for k, v in BOTS.items()}


def build_new_rep_registry(engine=None):
    def make(k, kw): return lambda acct: PairMaker(acct, k, **kw)
    return {k: make(k, kw) for k, (_, kw) in BOTS.items()}
