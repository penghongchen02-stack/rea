"""Ladder-consistency arbitrage: Above/Below strikes and Price Range buckets on the same expiry are tied by
arithmetic. When the books violate it by more than the fees, buying the cheap combination is a locked profit at
settlement whatever the price does. Scanner first (how often, how deep), trader second (paper)."""
import time, logging, urllib.parse, datetime as dt, collections
from . import feeds

log = logging.getLogger("ladder")
ABOVE = "ASSET_PRICE_MARKET_TYPE_ABOVE_BELOW"; RANGE = "ASSET_PRICE_MARKET_TYPE_RANGE"


class LadderFeed:
    """Discovers the ladders expiring soonest (one listing call per asset window) and fetches live books on demand."""
    def __init__(self, assets=("btc", "eth"), horizon_s=3 * 3600):
        self.assets = set(assets); self.horizon_s = horizon_s; self.legs = {}; self.last_discover = 0.0; self.lock = __import__("threading").Lock()

    def discover(self):
        now = time.time(); fmt = lambda t: dt.datetime.fromtimestamp(t, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        ms = []
        for a, b in ((now, now + self.horizon_s), (now + self.horizon_s, now + 30 * 3600)):     # hourly window, then daily ladders
            d = feeds.get_json(f"{feeds.GATEWAY}/v1/markets?active=true&closed=false&endDateMin={fmt(a)}&endDateMax={fmt(b)}&limit=500")
            ms += (d or {}).get("markets") or []
        fresh = {}
        for raw in ms:
            t = raw.get("assetPriceTerms")
            if not t or t["marketType"] not in (ABOVE, RANGE) or t["asset"]["symbol"] not in self.assets: continue
            m = self.legs.get(raw["slug"])
            if m is None: m = feeds.Market(raw)
            else: m.update(raw)
            m.list_bid = float(raw["bestBidQuote"]["value"]) if raw.get("bestBidQuote") else None
            m.list_ask = float(raw["bestAskQuote"]["value"]) if raw.get("bestAskQuote") else None
            fresh[raw["slug"]] = m
        with self.lock: self.legs = fresh
        self.last_discover = now
        return len(fresh)

    def discover_touch(self, days=70):
        """Weekly/monthly one-touch markets: 'will BTC reach (or dip to) $X before ...'."""
        now = time.time(); fmt = lambda t: dt.datetime.fromtimestamp(t, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = {}
        for a, b in ((now, now + 10 * 86400), (now + 10 * 86400, now + days * 86400)):
            d = feeds.get_json(f"{feeds.GATEWAY}/v1/markets?active=true&closed=false&endDateMin={fmt(a)}&endDateMax={fmt(b)}&limit=500")
            for raw in (d or {}).get("markets") or []:
                t = raw.get("assetPriceTerms")
                if not t or not t["marketType"].endswith("ONE_TOUCH") or t["asset"]["symbol"] not in ("btc", "eth", "sol", "xrp"): continue
                m = self.touch_legs.get(raw["slug"]) if hasattr(self, "touch_legs") else None
                m = feeds.Market(raw) if m is None else (m.update(raw) or m)
                m.list_bid = float(raw["bestBidQuote"]["value"]) if raw.get("bestBidQuote") else None
                m.list_ask = float(raw["bestAskQuote"]["value"]) if raw.get("bestAskQuote") else None
                out[raw["slug"]] = m
        self.touch_legs = out; self.last_touch_discover = now; return len(out)

    def ladders(self):
        """Group legs by (asset, windowEnd)."""
        g = collections.defaultdict(list)
        with self.lock:
            for m in self.legs.values(): g[(m.asset, m.end)].append(m)
        return g

    def fetch_book(self, m, max_age=4.0):
        """Live book for a leg; a snapshot younger than max_age is reused (the universes share the leg objects)."""
        if time.time() - m.book_ts < max_age: return True
        d = feeds.get_json(f"{feeds.GATEWAY}/v1/markets/{m.slug}/book")
        if d and d.get("marketData"): m.apply_book(d["marketData"]); return True
        return False


def leg_fee(theta, p): return theta * p * (1 - p)


class LadderArb:
    """Three consistency checks per ladder, verified on live books before any paper fill:
       (a) monotonic strikes: bid(above Y) > ask(above X) for X < Y  -> sell Y, buy X (payoff >= premium)
       (b) bucket tiling: sum of asks < 1 -> buy every bucket; sum of bids > 1 -> sell every bucket
       (c) complement: ask(above X) + sum of asks of buckets entirely below X < 1 -> buy both (one side pays $1)
    Trades only when the locked profit per set clears fees plus a margin; size = min depth, capped."""
    name = "ladder"
    MIN_EDGE = 0.02; MAX_SETS = 5.0; SCAN_EVERY = 30

    def __init__(self, account, feed):
        self.acct = account; self.feed = feed; self.stats = collections.Counter(); self.last_scan = 0.0; self.opportunities = collections.deque(maxlen=200); self.done = set()

    def scan(self, now):
        if now - self.last_scan < self.SCAN_EVERY: return
        self.last_scan = now
        if now - self.feed.last_discover > 120:
            try: n = self.feed.discover(); self.stats["legs"] = n
            except Exception as e: log.warning("ladder discover: %s", e)
        theta = self.acct.theta
        for (asset, end), legs in self.feed.ladders().items():
            if end - now < 45: continue                              # too close to expiry for stale listing prices
            above = sorted([m for m in legs if m.raw["assetPriceTerms"]["marketType"] == ABOVE and m.strike is not None], key=lambda m: m.strike)
            buckets = sorted([m for m in legs if m.raw["assetPriceTerms"]["marketType"] == RANGE], key=lambda m: (m.r_lo if m.r_lo is not None else -1e18))
            self.stats["ladders_scanned"] += 1
            # (a) monotonic violations from listing quotes
            for i, x in enumerate(above):
                if x.list_ask is None: continue
                for y in above[i + 1:]:
                    if y.list_bid is None: continue
                    prem = y.list_bid - x.list_ask - leg_fee(theta, y.list_bid) - leg_fee(theta, x.list_ask)
                    if prem > self.MIN_EDGE: self.try_pair(asset, end, x, y, prem, now)
            # (b) bucket tiling
            if buckets and all(b.list_ask is not None for b in buckets):
                s = sum(b.list_ask for b in buckets); fee = sum(leg_fee(theta, b.list_ask) for b in buckets)
                if 1 - s - fee > self.MIN_EDGE: self.try_buckets(asset, end, buckets, "buy", 1 - s - fee, now)
            if buckets and all(b.list_bid is not None for b in buckets):
                s = sum(b.list_bid for b in buckets); fee = sum(leg_fee(theta, b.list_bid) for b in buckets)
                if s - 1 - fee > self.MIN_EDGE: self.try_buckets(asset, end, buckets, "sell", s - 1 - fee, now)
            # (c) complement: above X + buckets entirely below X
            for x in above:
                if x.list_ask is None: continue
                below = [b for b in buckets if b.r_hi is not None and b.r_hi < x.strike and (b.r_lo is not None or b.r_kind == "below")]
                if not below or any(b.list_ask is None for b in below): continue
                # the buckets below must tile everything under X: lowest must be a 'below' tail and the highest upper must touch X
                if not any(b.r_kind == "below" for b in below) or max(b.r_hi for b in below) < x.strike - 0.011: continue
                s = x.list_ask + sum(b.list_ask for b in below); fee = leg_fee(theta, x.list_ask) + sum(leg_fee(theta, b.list_ask) for b in below)
                if 1 - s - fee > self.MIN_EDGE: self.try_complement(asset, end, x, below, 1 - s - fee, now)

    def _verify(self, legs):
        for m in legs:
            if time.time() - m.book_ts > 3 and not self.feed.fetch_book(m): return False
        return True

    def try_pair(self, asset, end, x, y, prem_est, now):
        key = ("pair", x.slug, y.slug)
        if key in self.done: return
        self.stats["candidates"] += 1
        if not self._verify([x, y]): return
        if x.best_ask is None or y.best_bid is None: self.stats["no_live_quote"] += 1; return
        theta = self.acct.theta; prem = y.best_bid - x.best_ask - leg_fee(theta, y.best_bid) - leg_fee(theta, x.best_ask)
        self.opportunities.appendleft({"ts": now, "kind": "pair", "asset": asset, "legs": [x.slug, y.slug], "est": round(prem_est, 4), "live": round(prem, 4), "depth": round(min(x.offers[0][1], y.bids[0][1]), 2)})
        if prem <= self.MIN_EDGE: self.stats["vanished_on_live_book"] += 1; return
        n = min(self.acct.size_for(x.best_ask, self.MAX_SETS), x.offers[0][1], y.bids[0][1], self.acct.affordable_sets([("buy", x.best_ask), ("sell", y.best_bid)]))
        n = int(n * 100) / 100
        if n < 0.01: return
        o1 = self.acct.take(x, "buy", n, limit=x.best_ask, tag=f"ladder-pair-buy {x.strike:.0f}", fixed=True)
        o2 = self.acct.take(y, "sell", n, limit=y.best_bid, tag=f"ladder-pair-sell {y.strike:.0f}", fixed=True)
        if o1 or o2: self.stats["trades"] += 1; self.done.add(key); log.info("ladder pair %s: buy above %s @%.2f, sell above %s @%.2f, premium %.3f x %.2f", asset, x.strike, x.best_ask, y.strike, y.best_bid, prem, n)

    def try_buckets(self, asset, end, buckets, side, edge_est, now):
        key = ("buckets", side, end)
        if key in self.done: return
        self.stats["candidates"] += 1
        if not self._verify(buckets): return
        theta = self.acct.theta
        if side == "buy":
            if any(b.best_ask is None for b in buckets): self.stats["no_live_quote"] += 1; return
            s = sum(b.best_ask for b in buckets); edge = 1 - s - sum(leg_fee(theta, b.best_ask) for b in buckets); n = min([self.acct.size_for(buckets[0].best_ask, self.MAX_SETS)] + [b.offers[0][1] for b in buckets])
        else:
            if any(b.best_bid is None for b in buckets): self.stats["no_live_quote"] += 1; return
            s = sum(b.best_bid for b in buckets); edge = s - 1 - sum(leg_fee(theta, b.best_bid) for b in buckets); n = min([self.acct.size_for(buckets[0].best_bid, self.MAX_SETS)] + [b.bids[0][1] for b in buckets])
        self.opportunities.appendleft({"ts": now, "kind": f"buckets-{side}", "asset": asset, "legs": [b.slug for b in buckets][:3] + ["..."], "est": round(edge_est, 4), "live": round(edge, 4), "depth": round(n, 2)})
        n = int(min(n, self.acct.affordable_sets([(side, b.best_ask if side == "buy" else b.best_bid) for b in buckets])) * 100) / 100
        if edge <= self.MIN_EDGE or n < 0.01: self.stats["vanished_on_live_book"] += 1; return
        filled = 0
        for b in buckets:
            o = self.acct.take(b, side, n, limit=b.best_ask if side == "buy" else b.best_bid, tag=f"ladder-buckets-{side}", fixed=True)
            if o and o.filled > 0: filled += 1
        if filled: self.stats["trades"] += 1; self.done.add(key); log.info("ladder buckets %s %s: %d legs, edge %.3f x %.2f", asset, side, filled, edge, n)

    def try_complement(self, asset, end, x, below, edge_est, now):
        key = ("complement", x.slug)
        if key in self.done: return
        self.stats["candidates"] += 1
        legs = [x] + below
        if not self._verify(legs): return
        if any(m.best_ask is None for m in legs): self.stats["no_live_quote"] += 1; return
        theta = self.acct.theta; s = sum(m.best_ask for m in legs); edge = 1 - s - sum(leg_fee(theta, m.best_ask) for m in legs); n = min([self.acct.size_for(x.best_ask, self.MAX_SETS)] + [m.offers[0][1] for m in legs])
        self.opportunities.appendleft({"ts": now, "kind": "complement", "asset": asset, "legs": [x.slug] + [b.slug for b in below][:2] + ["..."], "est": round(edge_est, 4), "live": round(edge, 4), "depth": round(n, 2)})
        n = int(min(n, self.acct.affordable_sets([("buy", m.best_ask) for m in legs])) * 100) / 100
        if edge <= self.MIN_EDGE or n < 0.01: self.stats["vanished_on_live_book"] += 1; return
        filled = 0
        for m in legs:
            o = self.acct.take(m, "buy", n, limit=m.best_ask, tag=f"ladder-complement {x.strike:.0f}", fixed=True)
            if o and o.filled > 0: filled += 1
        if filled: self.stats["trades"] += 1; self.done.add(key); log.info("ladder complement %s above %s + %d buckets, edge %.3f x %.2f", asset, x.strike, len(below), edge, n)

    def snapshot(self):
        return {"stats": dict(self.stats), "recent": list(self.opportunities)[:15]}


def _phi(z):
    import math
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


class LadderModel:
    """Replica of the ladder-pricing wallet: price every strike and bucket of the hourly and daily BTC/ETH ladders
    with a lognormal model (spot carried forward, per-second volatility from the index), then buy legs the book
    offers well below the model: near-sure legs at 0.96-0.99 when the model says >= 0.995, and tiny longshots
    offered at less than half their model odds. 5 contracts per leg, held to settlement."""
    name = "ladder_model"
    MAX_PER_LEG = 5.0
    def __init__(self, account, feed, index, spot):
        self.acct = account; self.feed = feed; self.index = index; self.spot = spot; self.last_scan = 0.0; self.stats = collections.Counter(); self.done = set()

    def prob_yes(self, m, S, sig, now):
        import math
        tau = max(m.end - now, 1.0); sd = sig * S * math.sqrt(tau) + S * 2e-5
        if m.market_type.endswith("ABOVE_BELOW"): return 1 - _phi((m.strike - S) / sd)
        lo = m.r_lo if m.r_lo is not None else -1e18; hi = m.r_hi if m.r_hi is not None else 1e18
        return _phi((hi - S) / sd) - _phi((lo - S) / sd)

    def scan(self, now):
        if now - self.last_scan < 60: return
        self.last_scan = now; theta = self.acct.theta; budget = 2
        for (asset, end), legs in self.feed.ladders().items():
            if end - now < 120: continue
            its, S_idx = self.index.last(asset)
            if S_idx is None: continue
            cb_now, cb_ts = self.spot.last(asset); cb_then = self.spot.at(asset, its) if cb_now else None
            S = S_idx * (cb_now / cb_then) if cb_now and cb_then else S_idx
            sig = max(self.spot.sigma(asset), self.index.sigma(asset))     # raw per-second volatility (the series is smoothed)
            if end - now > 6 * 3600: sig = max(sig, 1.0e-4)   # daily ladders: same long-horizon volatility floor
            for m in legs:
                if m.slug in self.done or m.list_ask is None: continue
                pm = self.prob_yes(m, S, sig, now); ask = m.list_ask
                near_sure = 0.90 <= ask <= 0.99 and pm >= 0.995 and 1 - ask - theta * ask * (1 - ask) > 0.005
                longshot = 0.01 <= ask <= 0.10 and pm >= 2 * ask + 0.02
                if not (near_sure or longshot): continue
                self.stats["candidates"] += 1
                if budget <= 0: break
                budget -= 1
                if not self.feed.fetch_book(m) or m.best_ask is None: self.stats["no_live_quote"] += 1; continue
                ask = m.best_ask
                if not ((0.90 <= ask <= 0.99 and pm >= 0.995) or (0.01 <= ask <= 0.10 and pm >= 2 * ask + 0.02)): self.stats["vanished"] += 1; continue
                n = min(self.MAX_PER_LEG, m.offers[0][1])
                o = self.acct.take(m, "buy", n, limit=ask, tag=f"lm-{'sure' if ask >= 0.9 else 'long'} p{pm:.3f}")
                if o and o.filled > 0: self.stats["trades"] += 1; self.done.add(m.slug); log.info("ladder_model buy %s @%.2f model %.3f x %.1f", m.slug, ask, pm, n)
    def snapshot(self): return {"stats": dict(self.stats)}


class LadderTouch:
    """Replica of 0xf705: prices weekly/monthly one-touch markets ('reach $X before ...') with a barrier model
    (reflection principle: P(touch) ~ 2 * P(end beyond the barrier)), buys legs offered well below model odds and
    sells legs bid well above them, in sizes up to 20 contracts, holding to settlement."""
    name = "touch_model"
    MAX_PER_LEG = 20.0; EDGE = 0.04
    def __init__(self, account, feed, index, spot):
        self.acct = account; self.feed = feed; self.index = index; self.spot = spot; self.last_scan = 0.0; self.stats = collections.Counter(); self.done = set()

    def prob_touch(self, m, S, sig, now):
        import math
        tau = max(m.end - now, 60.0); sd = sig * S * math.sqrt(tau)
        d = (m.strike - S) if m.direction != "down" else (S - m.strike)
        if d <= 0: return 1.0
        return min(1.0, 2 * (1 - _phi(d / sd)))

    def scan(self, now):
        if now - self.last_scan < 60: return
        self.last_scan = now
        if now - getattr(self.feed, "last_touch_discover", 0) > 600:
            try: self.stats["legs"] = self.feed.discover_touch()
            except Exception as e: log.warning("touch discover: %s", e)
        theta = self.acct.theta; budget = 2
        for m in list(getattr(self.feed, "touch_legs", {}).values()):
            if m.slug in self.done or m.end - now < 3600: continue
            its, S_idx = self.index.last(m.asset)
            if S_idx is None: continue
            cb_now, cb_ts = self.spot.last(m.asset); cb_then = self.spot.at(m.asset, its) if cb_now else None
            S = S_idx * (cb_now / cb_then) if cb_now and cb_then else S_idx
            sig = max(self.spot.sigma(m.asset), self.index.sigma(m.asset), 1.0e-4)      # weeks-long horizon: one-second moves understate real volatility (~55%/yr)
            pm = self.prob_touch(m, S, sig, now)
            buy = m.list_ask is not None and m.list_ask <= 0.97 and pm - m.list_ask - theta * m.list_ask * (1 - m.list_ask) > self.EDGE
            sell = m.list_bid is not None and m.list_bid >= 0.03 and m.list_bid - pm - theta * m.list_bid * (1 - m.list_bid) > self.EDGE
            if not (buy or sell): continue
            self.stats["candidates"] += 1
            if budget <= 0: break
            budget -= 1
            if not self.feed.fetch_book(m): self.stats["no_live_quote"] += 1; continue
            if buy and m.best_ask is not None and pm - m.best_ask - theta * m.best_ask * (1 - m.best_ask) > self.EDGE:
                n = min(self.MAX_PER_LEG, m.offers[0][1]); o = self.acct.take(m, "buy", n, limit=m.best_ask, tag=f"touch-buy p{pm:.2f}")
            elif sell and m.best_bid is not None and m.best_bid - pm - theta * m.best_bid * (1 - m.best_bid) > self.EDGE:
                n = min(self.MAX_PER_LEG, m.bids[0][1]); o = self.acct.take(m, "sell", n, limit=m.best_bid, tag=f"touch-sell p{pm:.2f}")
            else: self.stats["vanished"] += 1; continue
            if o and o.filled > 0: self.stats["trades"] += 1; self.done.add(m.slug); log.info("touch_model %s %s model %.2f", o.side, m.slug, pm)
    def snapshot(self): return {"stats": dict(self.stats)}


class XArb:
    """Cross-window arbitrage (bot 64). Every Up/Down window (5m, 15m, 1h) that ends at instant T settles on the
    same 60-second index average as the hourly strikes expiring at T. Only contracts on the SAME asset are compared
    (grouped by asset and settlement instant). Each is an 'above K' contract: Up = above the
    window's price-to-beat, strike leg = above X. If K_a <= K_b then contract a pays whenever b pays, so a must be
    worth at least b. Whenever bid(b) exceeds ask(a) by more than both fees, buy a and sell b: a locked profit of
    the premium at settlement whatever the price does. Checked every second on live books; ladder legs verified on
    their live book before any fill."""
    name = "xarb"
    MIN_EDGE = 0.01; MAX_SETS = 10.0; MAX_REPEATS = 3
    def __init__(self, account, mf, lf):
        self.acct = account; self.mf = mf; self.lf = lf; self.stats = collections.Counter(); self.opps = collections.deque(maxlen=50); self.repeats = collections.Counter()

    def contracts(self, now):
        groups = collections.defaultdict(list)
        for m in list(self.mf.markets.values()):
            if m.kind == "updown" and m.p2b is not None and m.end - now > 8 and m.book_ts > now - 5:
                groups[(m.asset, int(m.end))].append((m.p2b, m, "live"))
        if now - self.lf.last_discover > 60:
            try: self.lf.discover()
            except Exception as e: log.warning("xarb discover: %s", e)
        for m in list(self.lf.legs.values()):
            if m.market_type.endswith("ABOVE_BELOW") and m.strike is not None and (m.asset, int(m.end)) in groups and m.end - now > 8:
                groups[(m.asset, int(m.end))].append((m.strike, m, "list"))
        return groups

    def quotes(self, m, src):
        if src == "live": return m.best_bid, m.best_ask, (m.bids[0][1] if m.bids else 0), (m.offers[0][1] if m.offers else 0)
        return m.list_bid, m.list_ask, None, None

    def scan(self, now):
        theta = self.acct.theta; budget = 2
        for (asset, T), items in self.contracts(now).items():
            if len(items) < 2: continue
            assert all(m.asset == asset for _, m, _ in items)
            self.stats["groups"] += 1
            items.sort(key=lambda x: x[0])
            for i, (Ka, a, sa) in enumerate(items):
                for Kb, b, sb in items[i + 1:]:
                    if a.slug == b.slug: continue
                    bid_a, ask_a, _, _ = self.quotes(a, sa); bid_b, ask_b, _, _ = self.quotes(b, sb)
                    if ask_a is None or bid_b is None: continue
                    prem = bid_b - ask_a - theta * ask_a * (1 - ask_a) - theta * bid_b * (1 - bid_b)
                    if prem > self.stats.get("best_gap_c", -99): self.stats["best_gap_c"] = round(prem * 100, 2)
                    if prem < self.MIN_EDGE: continue
                    key = (a.slug, b.slug)
                    if self.repeats[key] >= self.MAX_REPEATS: continue
                    self.stats["candidates"] += 1
                    if budget <= 0: break
                    budget -= 1
                    # verify ladder legs on their live book
                    ok = True
                    for m, src in ((a, sa), (b, sb)):
                        if src == "list" and (now - m.book_ts > 3) and not self.lf.fetch_book(m): ok = False
                    if not ok: self.stats["no_live_book"] += 1; continue
                    ask_a = a.best_ask; bid_b = b.best_bid
                    if ask_a is None or bid_b is None: self.stats["no_live_quote"] += 1; continue
                    prem = bid_b - ask_a - theta * ask_a * (1 - ask_a) - theta * bid_b * (1 - bid_b)
                    lk = self.acct.liq_key; av_a = a.avail("buy", lk); av_b = b.avail("sell", lk)
                    depth = min(av_a[0][1] if av_a and av_a[0][0] <= ask_a else 0.0, av_b[0][1] if av_b and av_b[0][0] >= bid_b else 0.0)
                    self.opps.appendleft({"ts": now, "T": T, "asset": asset, "buy": a.slug, "K_a": Ka, "ask_a": ask_a, "sell": b.slug, "K_b": Kb, "bid_b": bid_b, "prem": round(prem, 4), "depth": round(depth, 2)})
                    if prem < self.MIN_EDGE: self.stats["vanished"] += 1; continue
                    n = min(self.acct.size_for(ask_a, self.MAX_SETS), depth, self.acct.affordable_sets([("buy", ask_a), ("sell", bid_b)]))
                    n = int(n * 100) / 100
                    if n < 0.01: self.stats["unaffordable"] += 1; continue
                    o1 = self.acct.take(a, "buy", n, limit=ask_a, tag=f"xarb-buy K{Ka:.0f}", fixed=True)
                    o2 = self.acct.take(b, "sell", n, limit=bid_b, tag=f"xarb-sell K{Kb:.0f}", fixed=True)
                    f1 = o1.filled if o1 else 0.0; f2 = o2.filled if o2 else 0.0
                    if abs(f1 - f2) > 0.009:      # never hold a naked leg
                        self.stats["unwound"] += 1
                        if f1 > f2: self.acct.take(a, "sell", round(f1 - f2, 2), limit=None, tag="xarb-unwind", fixed=True)
                        else: self.acct.take(b, "buy", round(f2 - f1, 2), limit=None, tag="xarb-unwind", fixed=True)
                    if f1 > 0 or f2 > 0:
                        self.stats["trades"] += 1; self.repeats[key] += 1
                        log.info("xarb T=%d buy %s (K %.2f) @%.2f sell %s (K %.2f) @%.2f prem %.3f x %.2f", T, a.slug, Ka, ask_a, b.slug, Kb, bid_b, prem, n)
    def snapshot(self): return {"stats": dict(self.stats), "recent": list(self.opps)[:10]}
