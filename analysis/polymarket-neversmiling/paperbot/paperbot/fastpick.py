"""Fast pick-off (user, 11-12 10:00Z): react to Coinbase spot inside the tick, not on the engine's one-pass-a-second loop.

Coinbase spot leads the settlement index (CF Benchmarks' rate, settled as a 60-second average) and usually the book.
On every Coinbase tick, in the feed's own thread, the bot checks the coin's open 5-minute windows: if spot has just
jumped (>= JUMP_SD standard deviations over the last LOOK seconds) while the window's mid has not moved since before the
jump, and the model, recomputed on this tick, still shows EDGE after the taker fee, it plans a take at the touch on
spot's side and reserves that displayed size. The decision takes well under a millisecond here; the plan is applied to
the account on the engine's next pass at the price and size seen at decision time (accounts are only ever changed
from the engine thread). One shot per window, entries with 20-285 s left, never above MAX_PRICE, sized by the
dashboard's share of the bank (or $10), held to settlement with LastCallHold's protective exit.

What this does not show: a real order still has to travel to the exchange and queue behind faster traders, so the
quote seen at decision time is often gone. Paper fills at the decision-time quote are the best case; treat this bot's
results as an upper bound on what speed could earn.
"""
import time, math, logging, collections
from .strategies import LastCallHold
from .sim import Order
from . import fair

log = logging.getLogger("fast")


class FastPick(LastCallHold):
    name = "fast_pickoff"
    ASSETS = ("btc", "eth", "sol", "xrp"); JUMP_SD = 2.5; LOOK = 1.0; EDGE = 0.02; MAX_PRICE = 0.90; LEFT = (20, 285); STALE = 0.011
    ORDER_USD = 10.0

    def __init__(self, account, engine):
        super().__init__(account, seed=41); self.engine = engine
        self.ticks = {a: collections.deque(maxlen=600) for a in self.ASSETS}
        self.mids = {}; self.done = set(); self.queue = collections.deque(maxlen=200)
        self.stats = collections.Counter(); self.lat = collections.deque(maxlen=1000)
        engine.spot.listeners.append(self.on_spot)

    # ---- Coinbase thread: decide --------------------------------------------------
    def on_spot(self, asset, px, ts):
        t0 = time.perf_counter()
        if asset not in self.ticks: return
        tk = self.ticks[asset]; tk.append((ts, px)); self.stats["ticks"] += 1
        then = None
        for t, p in tk:
            if t <= ts - self.LOOK: then = p
            else: break
        acct = self.acct
        for m in list(self.engine.mf.markets.values()):
            if m.asset != asset or m.kind != "updown" or m.dur != 300: continue
            bb, ba = m.best_bid, m.best_ask
            if bb is None or ba is None: continue
            mid = (bb + ba) / 2; hist = self.mids.setdefault(m.slug, collections.deque(maxlen=120)); hist.append((ts, mid))
            if then is None or m.slug in self.done: continue
            left = m.end - ts
            if not (self.LEFT[0] <= left <= self.LEFT[1]) or ts - m.book_ts > 3 or m.state not in (None, "MARKET_STATE_OPEN"): continue
            sig = self.engine.spot.sigma(asset) * px * math.sqrt(self.LOOK); jump = (px - then) / max(sig, 1e-12)
            if abs(jump) < self.JUMP_SD: continue
            old = None
            for t, v in hist:
                if t <= ts - 0.8: old = v
                else: break
            if old is None or abs(mid - old) > self.STALE: continue           # the book already moved: nothing stale to hit
            p, det = fair._p_up(m, self.engine.idx, self.engine.spot, ts)       # not the per-second cache: this tick
            if p is None: continue
            th = acct.theta
            if jump > 0 and ba <= self.MAX_PRICE and p - ba - th * ba * (1 - ba) > self.EDGE: side, lim, unit = "buy", ba, ba
            elif jump < 0 and 1 - bb <= self.MAX_PRICE and (1 - p) - (1 - bb) - th * bb * (1 - bb) > self.EDGE: side, lim, unit = "sell", bb, 1 - bb
            else: continue
            usd = acct.pct_dollars() if acct.size_mode == "pct" else self.ORDER_USD
            if acct.size_mode == "pct": usd = min(usd, acct.window_pct * acct.bank())
            lv = [x for x in m.avail(side, acct.liq_key) if x[0] == lim]
            q = round(min(usd / max(unit, 0.01), lv[0][1] if lv else 0.0), 2)
            if q < m.min_qty: self.stats["no_size"] += 1; continue
            k = (acct.liq_key, side, lim); m.consumed[k] = m.consumed.get(k, 0.0) + q       # this displayed size is taken
            ms = (time.perf_counter() - t0) * 1000.0
            self.done.add(m.slug); self.lat.append(ms); self.stats["signals"] += 1
            self.queue.append((m.slug, side, lim, q, ts, f"fast-{'up' if side == 'buy' else 'down'} j{jump:+.1f} {ms:.2f}ms"))
        self.stats["us_total"] += int((time.perf_counter() - t0) * 1e6)

    # ---- engine thread: book the planned fills, manage exits -----------------------
    def on_tick(self, market, ctx, now):
        while self.queue:
            slug, side, px, q, ts, tag = self.queue.popleft(); m = self.engine.mf.markets.get(slug); a = self.acct
            if m is None: self.stats["gone"] += 1; continue
            need = a.cash_needed(slug, side, px, q)
            if need > 1e-9 and need > a.buying_power() + 1e-9: a.rejected += 1; self.stats["rejected"] += 1; continue
            a._fill(m, Order(slug, side, px, q, False, tag), px, q, maker=False); self.stats["fills"] += 1
            log.info("[fast] %s %s %.2f x %.2f, decided %.0f ms before booking (%s)", slug, side, px, q, (now - ts) * 1000, tag)
        if market.asset in self.ASSETS and market.dur == 300 and market.remaining(now) >= 3 and market.state in (None, "MARKET_STATE_OPEN"):
            self.manage(market, ctx, now)

    def snapshot(self):
        lat = sorted(self.lat); n = self.stats["ticks"] or 1
        return {"stats": dict(self.stats), "avg_tick_ms": round(self.stats["us_total"] / n / 1000.0, 3),
                "decision_ms": {"median": round(lat[len(lat) // 2], 3), "max": round(lat[-1], 3)} if lat else None}
