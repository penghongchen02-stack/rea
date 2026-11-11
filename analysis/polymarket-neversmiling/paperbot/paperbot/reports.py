"""Replicas of the crypto Up/Down traders in the PR&R trader reports (polyresearchrobotics.com/reports; user, 11-11 22:40Z).
Each trades its own coins, window lengths and style, and sizes its own orders from the trader's reported fill sizes
(log-normal through the reported median and P95, capped at the reported maximum), not the dashboards' $1-$30 range.
They run in the tryout universe with $1,000 each. The other reports (sports, esports, weather, politics) cannot trade
here: the engine only sees crypto Up/Down markets.

Both-sides traders (Whiskas, Doggystyie, NeverSmiling, FlippingSharks, Bonereaper) rest bids on both sides (see Pair),
as their reports describe them providing liquidity; the others take.

Up/Down on one market is one YES position here: buying Down is selling YES at the bid (cost 1 - bid per contract),
so a bot that holds both sides holds the net, which pays the same as the pair at settlement.

Rules are the reports' published playbooks where they exist; where a report gives only observed behaviour, the rule
follows that behaviour (noted per bot). Signals the reports could not see (spot momentum, fair value) use the same
settlement model as every other bot (fair.p_up).
"""
import math, time
from .strategies import LastCallHold
from .fair import p_up

FIVE, FIFTEEN = 300, 900


class Replica(LastCallHold):
    """One report's trader. Config (class attributes of the subclass):
    ASSETS, DURS, OPEN_MIN (s after open), LEFT_MIN/LEFT_MAX (s before close), HOURS_OFF (UTC), PERIOD (s between
    fills), SIZE = (median $, p95 $, max $), MARKET_CAP ($ per market), plus the style hooks in decide()."""
    ASSETS = ("btc",); DURS = (FIVE,); OPEN_MIN = 0; LEFT_MIN = 3; LEFT_MAX = 10 ** 6; HOURS_OFF = ()
    PERIOD = 6.0; SIZE = (5.0, 30.0, 100.0); MARKET_CAP = 200.0; MIN_ASSET_SECONDS = 0
    EXIT_Z = -99.0          # no protective exit: every report trader holds to settlement unless decide() sells

    def __init__(self, account, seed=31):
        super().__init__(account, seed=seed); self.assets = set(self.ASSETS); self.durs = set(self.DURS)

    # ---- helpers ---------------------------------------------------------------
    def dollars(self, mult=1.0):
        med, p95, mx = self.SIZE; s = math.log(max(p95 / med, 1.0001)) / 1.645
        return min(mx, max(0.5, med * math.exp(s * self.rng.gauss(0, 1)))) * mult

    def buy(self, market, st, side, usd, tag):
        """side 'up' buys YES at the ask; 'down' buys NO by selling YES at the bid. usd = dollars to spend."""
        room = self.MARKET_CAP - st.get("spent", 0.0)
        usd = min(usd, room)
        if usd < 0.5: return None
        if side == "up":
            px = market.best_ask
            if px is None: return None
            n = round(usd / px, 2); o = self.acct.take(market, "buy", n, limit=px, tag=tag, fixed=True)
        else:
            px = market.best_bid
            if px is None: return None
            n = round(usd / (1 - px), 2); o = self.acct.take(market, "sell", n, limit=px, tag=tag, fixed=True)
        if o and o.filled > 0:
            cost = o.filled * (px if side == "up" else 1 - px)
            st["spent"] = st.get("spent", 0.0) + cost; st[side] = st.get(side, 0.0) + cost
        return o

    def on_tick(self, market, ctx, now):
        if market.asset not in self.assets or market.dur not in self.durs: return
        slug = market.slug; st = self.m(slug); left = market.remaining(now)
        if left < self.LEFT_MIN or left > self.LEFT_MAX or market.state not in (None, "MARKET_STATE_OPEN"): return
        if now - market.start < self.OPEN_MIN or now < st["next_take"]: return
        if time.gmtime(now).tm_hour in self.HOURS_OFF: return
        bb, ba = market.best_bid, market.best_ask
        if bb is None or ba is None: return
        p, det = p_up(market, ctx["index"], ctx["spot"], now)
        if p is None or det.get("index_age", 99) > 8: return
        st["next_take"] = now + self.PERIOD
        self.decide(market, st, p, det, now, left, bb, ba)

    def decide(self, market, st, p, det, now, left, bb, ba): raise NotImplementedError


class Pair(Replica):
    """Both sides of a window, as a maker. On Polymarket US, Up and Down share one book, so taking both sides costs
    $1 plus the spread; the reports' traders get a pair under $1 by resting bids. Here: rest a bid for Up at the best
    bid and an offer of YES at the best ask (= a bid for Down at 1 - ask), so a filled pair costs 1 - spread. Rested
    only when that pair cost is at most PAIR_CAP and each leg's price is in BAND; the model-favoured side gets TILT x
    the dollars once |z| >= TILT_Z, or CHEAP_WEIGHT weights the cheaper side (FlippingSharks). Orders are refreshed
    every PERIOD; MARKET_CAP counts filled dollars plus what is resting."""
    PAIR_CAP = 0.98; BAND = (0.01, 0.99); TILT = 1.0; TILT_Z = 1.0; CHEAP_WEIGHT = None; SKIP_WEAK_ABOVE_1 = False
    def filled(self, st):
        return sum(o.filled * (o.price if o.side == "buy" else 1 - o.price) for o in st.get("orders", []))
    def decide(self, market, st, p, det, now, left, bb, ba):
        acct = self.acct; acct.cancel_all(market.slug)                 # refresh the quotes
        up_c, dn_c = bb, 1 - ba; pair = up_c + dn_c; z = det.get("z", 0.0)
        strong = abs(z) >= self.TILT_Z
        if pair > self.PAIR_CAP and not (self.SKIP_WEAK_ABOVE_1 and strong): return
        if self.SKIP_WEAK_ABOVE_1 and pair > 1.0 and not strong: return
        room = self.MARKET_CAP - self.filled(st)
        if room < 1.0: return
        w_up = w_dn = 1.0
        if self.CHEAP_WEIGHT is not None:
            sub = self.CHEAP_WEIGHT[0] + self.rng.random() * (self.CHEAP_WEIGHT[1] - self.CHEAP_WEIGHT[0])
            w_up, w_dn = (1.0, sub) if up_c <= dn_c else (sub, 1.0)
        elif strong and self.TILT > 1:
            w_up, w_dn = (self.TILT, 1.0) if z > 0 else (1.0, self.TILT)
        usd = min(self.dollars() * 2, room); tot = w_up + w_dn; orders = st.setdefault("orders", [])
        if self.BAND[0] <= up_c <= self.BAND[1] and up_c > 0:
            o = acct.place(market, "buy", bb, round(usd * w_up / tot / up_c, 2), post_only=True, tag=f"{self.name} bid up {up_c:.2f}", fixed=True)
            if o: orders.append(o)
        if self.BAND[0] <= dn_c <= self.BAND[1] and dn_c > 0:
            o = acct.place(market, "sell", ba, round(usd * w_dn / tot / dn_c, 2), post_only=True, tag=f"{self.name} bid down {dn_c:.2f}", fixed=True)
            if o: orders.append(o)
        st["spent"] = self.filled(st)


class Whiskas(Pair):
    """x-MoneyForWhiskas: BTC/ETH 5m, both sides within ~6 s, pair below $0.98, avoid $0.90+ legs, uniform $8 clips
    (P95 $42, max $135), never sells; optional 2x tilt on a spot signal (report rules 1-7). Entries 30-295 s before close."""
    name = "rep_whiskas"; ASSETS = ("btc", "eth"); LEFT_MIN = 30; LEFT_MAX = 295; PERIOD = 6.0     # rests both bids
    SIZE = (8.20, 41.59, 134.53); MARKET_CAP = 250.0; PAIR_CAP = 0.98; BAND = (0.01, 0.90); TILT = 2.0; TILT_Z = 1.0


class Doggystyie(Pair):
    """Doggystyie: BTC 5m, both sides every window, fills $15-50 (median $17.81, cap $84), $200 per market, spread
    over the first 60-180 s, never sells, optional tilt to the favoured side (report rules 1-8). Its profit was
    liquidity rewards (trading ROI -1.00%), which this sim does not pay."""
    name = "rep_doggystyie"; ASSETS = ("btc",); OPEN_MIN = 0; LEFT_MIN = 120; LEFT_MAX = 300; PERIOD = 8.0
    SIZE = (17.81, 48.72, 83.52); MARKET_CAP = 200.0; PAIR_CAP = 1.02; TILT = 2.0; TILT_Z = 1.5


class NeverSmiling(Pair):
    """NeverSmiling: BTC/ETH/SOL/XRP 5m and 15m, between 10% and 90% of the window, legs $0.30-0.70, both sides with
    a 2-5x tilt to the favoured side, skip a pair above $1.00 without a 2x signal, clips $3.51 median (P95 $16.41,
    max $267), about $161 per market, skip 14-16 UTC (report rules 1-9)."""
    name = "rep_neversmiling"; ASSETS = ("btc", "eth", "sol", "xrp"); DURS = (FIVE, FIFTEEN); PERIOD = 6.0
    SIZE = (3.51, 16.41, 267.09); MARKET_CAP = 161.0; PAIR_CAP = 0.97; BAND = (0.30, 0.70); TILT = 3.0; TILT_Z = 1.0
    SKIP_WEAK_ABOVE_1 = True; HOURS_OFF = (14, 15, 16)
    def on_tick(self, market, ctx, now):
        el = (now - market.start) / market.dur
        if 0.10 <= el <= 0.90: super().on_tick(market, ctx, now)


class FlippingSharks(Pair):
    """FlippingSharks: BTC 5m, both legs within ~7 s, more on the cheaper side (other leg 10-40%), 60-295 s before close,
    skip 4, 16, 19, 20 UTC, hold (report rules 1-7). Fills median $4.21, P95 $74.81, max $222.75; about $222 per market.
    (Live 11-11: now mostly one-sided cheap BTC/ETH; the report's rules are used.)"""
    name = "rep_flippingsharks"; ASSETS = ("btc",); LEFT_MIN = 60; LEFT_MAX = 295; PERIOD = 7.0
    SIZE = (4.21, 74.81, 222.75); MARKET_CAP = 222.0; PAIR_CAP = 1.00; CHEAP_WEIGHT = (0.10, 0.40); HOURS_OFF = (4, 16, 19, 20)


class Bonereaper(Pair):
    """Bonereaper: BTC/ETH/SOL 5m and BTC/ETH 15m, from 30 s after open to 15 s before close, both sides when the pair
    is at most $1.10, tilt to the spot-confirmed side only at 2x+ (|z| >= 2), hold; per market at most 1% of bankroll
    ($10 here), fills median $3.94 (P95 $37.93). 60% of its capital was at $0.90-1.00. Its profit was liquidity rewards
    (trading ROI +0.20%), which this sim does not pay."""
    name = "rep_bonereaper"; ASSETS = ("btc", "eth", "sol"); DURS = (FIVE, FIFTEEN); OPEN_MIN = 30; LEFT_MIN = 15; PERIOD = 5.0
    SIZE = (3.94, 37.93, 196.02); MARKET_CAP = 10.0; PAIR_CAP = 1.10; TILT = 3.0; TILT_Z = 2.0


class PBot6(Replica):
    """PBOT-6: BTC/ETH/SOL/XRP 5m and 15m from the start of each window; the side whose model probability beats its
    mid by 8c+ gets a high-conviction entry, 3-8c a small balanced entry; the other side is added 60-120 s later only if
    the pair stays under $0.98; hold (report rules 1-7). Clips $1-50 (median $3.69, P95 $27.35, max $660)."""
    name = "rep_pbot6"; ASSETS = ("btc", "eth", "sol", "xrp"); DURS = (FIVE, FIFTEEN); LEFT_MIN = 30; PERIOD = 3.0
    SIZE = (3.69, 27.35, 660.0); MARKET_CAP = 150.0
    def decide(self, market, st, p, det, now, left, bb, ba):
        mid = (bb + ba) / 2; gap = p - mid
        side = "up" if gap > 0 else "down"
        if abs(gap) >= 0.08: self.buy(market, st, side, self.dollars(2.0), f"{self.name} high {side} gap {gap:+.2f}"); st.setdefault("t1", now)
        elif abs(gap) >= 0.03 and not st.get("t1"): self.buy(market, st, side, self.dollars(0.5), f"{self.name} low {side} gap {gap:+.2f}"); st["t1"] = now
        t1 = st.get("t1")
        if t1 and not st.get("hedged") and 60 <= now - t1 <= 120 and ba + (1 - bb) < 0.98:
            other = "down" if st.get("up", 0) >= st.get("down", 0) else "up"
            self.buy(market, st, other, self.dollars(0.5), f"{self.name} hedge {other}"); st["hedged"] = True


class SlipMe(Replica):
    """SLIP-ME: BTC 5m. Both legs in the first ~30 s (60 shares each); minutes 1-4 add 60 to the side the model favours;
    cheap underdog hedges under $0.20 (120 shares) until one side holds 2x the other; final 90 s add 60 to the dominant
    side below $0.97; never sells (report rules 1-8). Fixed share clips; fills median $19.20, max $117.60, up to ~$887 per
    market (capped at $300 here)."""
    name = "rep_slipme"; ASSETS = ("btc",); PERIOD = 10.0; MARKET_CAP = 300.0
    def shares(self, market, st, side, n, tag):
        px = market.best_ask if side == "up" else (1 - market.best_bid)
        return self.buy(market, st, side, n * px, tag)
    def decide(self, market, st, p, det, now, left, bb, ba):
        el = now - market.start; z = det.get("z", 0.0); fav = "up" if z > 0 else "down"
        up, dn = st.get("up", 0.0), st.get("down", 0.0); skew = max(up, dn) / max(min(up, dn), 1e-9)
        if el <= 30 and not st.get("opened"):
            self.shares(market, st, "up", 60, f"{self.name} open up"); self.shares(market, st, "down", 60, f"{self.name} open down"); st["opened"] = True
        elif 60 <= el <= 240 and abs(z) >= 0.5:
            self.shares(market, st, fav, 60, f"{self.name} add {fav} z{z:.1f}")
        if skew < 2.0:
            for side, c in (("up", ba), ("down", 1 - bb)):
                if c < 0.20: self.shares(market, st, side, 120, f"{self.name} hedge {side} {c:.2f}")
        if left <= 90:
            dom = "up" if up >= dn else "down"; c = ba if dom == "up" else 1 - bb
            if c < 0.97: self.shares(market, st, dom, 60, f"{self.name} late {dom} {c:.2f}")


class SirMartingale(Replica):
    """SirMartingale: BTC 5m, BTC 15m, ETH 5m; one side only, bought when it is priced below the model's fair value (spot
    led), small fills (median $5.50, P90 $39, max $366) stepping up when averaging down, and sold into strength before
    settlement (average buy $0.43, average sell $0.80); inactive 23:00-02:00 UTC (report rules 1-7). Residual shares
    are held to settlement."""
    name = "rep_sirmartingale"; ASSETS = ("btc", "eth"); DURS = (FIVE, FIFTEEN); LEFT_MIN = 10; PERIOD = 4.0
    SIZE = (5.50, 39.15, 366.30); MARKET_CAP = 120.0; HOURS_OFF = (23, 0, 1); EDGE = 0.05; STEPS = (1.0, 1.2, 1.7, 2.4)
    def on_tick(self, market, ctx, now):
        if market.asset == "eth" and market.dur == FIFTEEN: return
        super().on_tick(market, ctx, now)
    def decide(self, market, st, p, det, now, left, bb, ba):
        q = self.pos(market.slug); pos = self.acct.positions.get(market.slug)
        if abs(q) > 1e-9 and pos:                      # sell into strength
            if q > 0 and (bb >= 0.80 or bb >= pos.avg + 0.35):
                self.acct.take(market, "sell", q, limit=bb, tag=f"{self.name} sell {bb:.2f}", fixed=True); st["done"] = True; return
            if q < 0 and ((1 - ba) >= 0.80 or (1 - ba) >= (1 - pos.avg) + 0.35):
                self.acct.take(market, "buy", -q, limit=ba, tag=f"{self.name} sell down {1 - ba:.2f}", fixed=True); st["done"] = True; return
        if st.get("done"): return
        k = st.get("buys", 0)
        if k >= len(self.STEPS): return
        if p - ba >= self.EDGE and q >= 0: side, c = "up", ba
        elif (1 - p) - (1 - bb) >= self.EDGE and q <= 0: side, c = "down", 1 - bb
        else: return
        if k and c >= st.get("last_px", 1.0): return     # only adds when averaging down
        if self.buy(market, st, side, self.dollars(self.STEPS[k]), f"{self.name} buy {side} {c:.2f} edge {abs(p - (ba if side == 'up' else bb)):.2f}"):
            st["buys"] = k + 1; st["last_px"] = c


class BTC5MScour(Replica):
    """BTC5MScour: BTC 5m, final 60-120 s, buys the resolving side at $0.90-0.99 (sweeping stale asks) unless spot is
    within 0.05% of the price to beat; holds; fills median $34.08, P95 $242.50, max $878.56, about $546 per market
    (capped at $250 here) (report rules 1-6)."""
    name = "rep_btc5mscour"; ASSETS = ("btc",); LEFT_MIN = 5; LEFT_MAX = 120; PERIOD = 3.0
    SIZE = (34.08, 242.50, 878.56); MARKET_CAP = 250.0
    def decide(self, market, st, p, det, now, left, bb, ba):
        K = det.get("K"); m = det.get("mean")
        if not K or m is None or abs(m - K) / K < 0.0005: return
        if m > K and 0.90 <= ba <= 0.99 and st.get("down", 0) == 0: self.buy(market, st, "up", self.dollars(), f"{self.name} up {ba:.2f}")
        elif m < K and 0.90 <= 1 - bb <= 0.99 and st.get("up", 0) == 0: self.buy(market, st, "down", self.dollars(), f"{self.name} down {1 - bb:.2f}")


REPLICAS = {c.name: c for c in (Whiskas, Doggystyie, NeverSmiling, FlippingSharks, Bonereaper, PBot6, SlipMe, SirMartingale, BTC5MScour)}


def build_report_registry():
    return {k: (lambda acct, cls=cls: cls(acct)) for k, cls in REPLICAS.items()}
