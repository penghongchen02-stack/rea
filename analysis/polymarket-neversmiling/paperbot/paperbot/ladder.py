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
        now = time.time(); lo = dt.datetime.fromtimestamp(now, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"); hi = dt.datetime.fromtimestamp(now + self.horizon_s, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        d = feeds.get_json(f"{feeds.GATEWAY}/v1/markets?active=true&closed=false&endDateMin={lo}&endDateMax={hi}&limit=500")
        ms = (d or {}).get("markets") or []; fresh = {}
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

    def ladders(self):
        """Group legs by (asset, windowEnd)."""
        g = collections.defaultdict(list)
        with self.lock:
            for m in self.legs.values(): g[(m.asset, m.end)].append(m)
        return g

    def fetch_book(self, m):
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
    MIN_EDGE = 0.02; MAX_SETS = 5.0

    def __init__(self, account, feed):
        self.acct = account; self.feed = feed; self.stats = collections.Counter(); self.last_scan = 0.0; self.opportunities = collections.deque(maxlen=200); self.done = set()

    def scan(self, now):
        if now - self.last_scan < 8: return
        self.last_scan = now
        if now - self.feed.last_discover > 60:
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
        n = min(self.MAX_SETS, x.offers[0][1], y.bids[0][1])
        if n < 0.01: return
        o1 = self.acct.take(x, "buy", n, limit=x.best_ask, tag=f"ladder-pair-buy {x.strike:.0f}")
        o2 = self.acct.take(y, "sell", n, limit=y.best_bid, tag=f"ladder-pair-sell {y.strike:.0f}")
        if o1 or o2: self.stats["trades"] += 1; self.done.add(key); log.info("ladder pair %s: buy above %s @%.2f, sell above %s @%.2f, premium %.3f x %.2f", asset, x.strike, x.best_ask, y.strike, y.best_bid, prem, n)

    def try_buckets(self, asset, end, buckets, side, edge_est, now):
        key = ("buckets", side, end)
        if key in self.done: return
        self.stats["candidates"] += 1
        if not self._verify(buckets): return
        theta = self.acct.theta
        if side == "buy":
            if any(b.best_ask is None for b in buckets): self.stats["no_live_quote"] += 1; return
            s = sum(b.best_ask for b in buckets); edge = 1 - s - sum(leg_fee(theta, b.best_ask) for b in buckets); n = min([self.MAX_SETS] + [b.offers[0][1] for b in buckets])
        else:
            if any(b.best_bid is None for b in buckets): self.stats["no_live_quote"] += 1; return
            s = sum(b.best_bid for b in buckets); edge = s - 1 - sum(leg_fee(theta, b.best_bid) for b in buckets); n = min([self.MAX_SETS] + [b.bids[0][1] for b in buckets])
        self.opportunities.appendleft({"ts": now, "kind": f"buckets-{side}", "asset": asset, "legs": [b.slug for b in buckets][:3] + ["..."], "est": round(edge_est, 4), "live": round(edge, 4), "depth": round(n, 2)})
        if edge <= self.MIN_EDGE or n < 0.01: self.stats["vanished_on_live_book"] += 1; return
        filled = 0
        for b in buckets:
            o = self.acct.take(b, side, n, limit=b.best_ask if side == "buy" else b.best_bid, tag=f"ladder-buckets-{side}")
            if o and o.filled > 0: filled += 1
        if filled: self.stats["trades"] += 1; self.done.add(key); log.info("ladder buckets %s %s: %d legs, edge %.3f x %.2f", asset, side, filled, edge, n)

    def try_complement(self, asset, end, x, below, edge_est, now):
        key = ("complement", x.slug)
        if key in self.done: return
        self.stats["candidates"] += 1
        legs = [x] + below
        if not self._verify(legs): return
        if any(m.best_ask is None for m in legs): self.stats["no_live_quote"] += 1; return
        theta = self.acct.theta; s = sum(m.best_ask for m in legs); edge = 1 - s - sum(leg_fee(theta, m.best_ask) for m in legs); n = min([self.MAX_SETS] + [m.offers[0][1] for m in legs])
        self.opportunities.appendleft({"ts": now, "kind": "complement", "asset": asset, "legs": [x.slug] + [b.slug for b in below][:2] + ["..."], "est": round(edge_est, 4), "live": round(edge, 4), "depth": round(n, 2)})
        if edge <= self.MIN_EDGE or n < 0.01: self.stats["vanished_on_live_book"] += 1; return
        filled = 0
        for m in legs:
            o = self.acct.take(m, "buy", n, limit=m.best_ask, tag=f"ladder-complement {x.strike:.0f}")
            if o and o.filled > 0: filled += 1
        if filled: self.stats["trades"] += 1; self.done.add(key); log.info("ladder complement %s above %s + %d buckets, edge %.3f x %.2f", asset, x.strike, len(below), edge, n)

    def snapshot(self):
        return {"stats": dict(self.stats), "recent": list(self.opportunities)[:15]}
