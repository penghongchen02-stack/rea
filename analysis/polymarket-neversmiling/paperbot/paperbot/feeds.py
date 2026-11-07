"""Live data feeds: Polymarket US gateway (markets, books, BRTI candles) and Coinbase spot."""
import json, os, ssl, threading, time, urllib.parse, urllib.request, collections, logging, datetime as dt, re

log = logging.getLogger("feeds")
GATEWAY = "https://gateway.polymarket.us"
ASSETS = ["btc", "eth", "sol", "xrp"]
HORIZONS = {"5m": 300, "15m": 900, "1h": 3600}
CA = os.environ.get("SSL_CERT_FILE") or ("/root/.ccr/ca-bundle.crt" if os.path.exists("/root/.ccr/ca-bundle.crt") else None)
_CTX = ssl.create_default_context(cafile=CA) if CA else ssl.create_default_context()


class RateLimiter:
    """Adaptive: the gateway limit (25/s) is shared per source IP, so the real budget is unknown. Start at per_sec,
    halve on every 429 (floor 3/s), and recover 10% every 30 s without one."""
    def __init__(self, per_sec):
        self.base = per_sec; self.rate = per_sec; self.last = 0.0; self.lock = threading.Lock(); self.last_429 = 0.0; self.last_recover = time.monotonic()
    def wait(self):
        with self.lock:
            now = time.monotonic()
            if now - self.last_recover > 30 and now - self.last_429 > 30:
                self.rate = min(self.base, self.rate * 1.1); self.last_recover = now
            gap = now - self.last; min_gap = 1.0 / self.rate
            if gap < min_gap: time.sleep(min_gap - gap)
            self.last = time.monotonic()
    def hit_429(self):
        with self.lock:
            self.rate = max(3.0, self.rate / 2); self.last_429 = time.monotonic()

# Cloudflare throttles the default "Python-urllib/x" agent far below the documented 25/s (measured: ~25% 429s at
# 3 req/s); a plain client name of our own gets none at the same rate.
USER_AGENT = "rea-paperbot/1.0"
_rl = RateLimiter(8)
RATE_LIMITED = [0]

def get_json(url, timeout=15, retries=3):
    """GET JSON from the gateway. Cloudflare fronts gateway.polymarket.us with `cache-control: public, max-age=30`,
    so a plain URL returns a book up to 30 s old (and a cached 429 keeps coming back for 30 s). A unique `_=`
    parameter makes every request a cache miss and gives the live book. See USER_AGENT below for the 429s."""
    for i in range(retries):
        _rl.wait()
        try:
            u = f"{url}{'&' if '?' in url else '?'}_={time.time_ns()}"
            req = urllib.request.Request(u, headers={"Accept": "application/json", "Cache-Control": "no-cache", "User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
            if e.code == 429:
                RATE_LIMITED[0] += 1; _rl.hit_429()
                if RATE_LIMITED[0] % 20 == 1: log.warning("429 rate limited (%d so far, limiter now %.1f/s) on %s", RATE_LIMITED[0], _rl.rate, url[:90])
                if i == 0: time.sleep(0.3); continue
                return None
            log.warning("HTTP %s %s", e.code, url); time.sleep(0.5 + i)
        except Exception as e:
            log.warning("GET failed %s: %s", url, e); time.sleep(0.5 + i)
    return None


def window_slug(asset, horizon, start_ts):
    t = dt.datetime.fromtimestamp(start_ts, dt.UTC)
    prefix = "cpc" if asset == "btc" else f"cpc{asset}"      # observed listing convention: cpc-btc-..., cpceth-eth-..., cpcsol-sol-..., cpcxrp-xrp-...
    return f"{prefix}-{asset}-updown-{horizon}-{t:%Y-%m-%d-%H%M}z"


def parse_ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None


class Market:
    """One Up/Down window. Prices refer to the YES ("Up") instrument."""
    def __init__(self, raw):
        self.slug = raw["slug"]; self.update(raw)
        self.bids = []; self.offers = []; self.book_ts = 0.0; self.state = None; self.book_src = "rest"
        self.last_px = None; self.last_qty = 0.0; self.shares_traded = 0.0; self.last_trade_ts = 0.0
        self.tape = collections.deque(maxlen=2000)   # (ts, price, qty) inferred from stats deltas
        self.stats = {}

    def update(self, raw):
        self.raw = raw; t = raw["assetPriceTerms"]
        self.asset = t["asset"]["symbol"]; self.horizon = t["horizon"]
        self.end = parse_ts(t["windowEnd"]); self.start = parse_ts(t.get("windowStart")) or parse_ts(raw.get("startDate")) or (self.end - 3600)
        self.dur = self.end - self.start
        self.p2b = float(t["priceToBeat"]["value"]) if t.get("priceToBeat") else None
        self.settle_px = float(t["settlementPrice"]["value"]) if t.get("settlementPrice") else None
        self.status = raw.get("status"); self.closed = raw.get("closed", False)
        self.fee_coef = float(raw.get("feeCoefficient") or 0.095); self.tick = float(raw.get("orderPriceMinTickSize") or 0.01)
        self.min_qty = float(raw.get("minimumTradeQty") or 0.01); self.question = raw.get("question", "")
        self.decimals = int(t.get("priceDecimals") or 2)
        mt = t.get("marketType", ""); self.market_type = mt
        self.kind = "updown" if mt.endswith("UP_DOWN") else "ladder"
        self.strike = float(t["strike"]["value"]) if t.get("strike") else None
        self.r_lo = float(t["rangeLower"]["value"]) if t.get("rangeLower") else None
        self.r_hi = float(t["rangeUpper"]["value"]) if t.get("rangeUpper") else None
        rk = t.get("rangeKind", ""); self.r_kind = "below" if rk.endswith("BELOW") else ("above" if rk.endswith("ABOVE") else "interior")
        if self.kind == "ladder" and self.r_kind == "below" and self.r_lo is None: self.r_lo = -1e18
        if self.kind == "ladder" and self.r_kind == "above" and self.r_hi is None: self.r_hi = 1e18
        self.list_bid = None; self.list_ask = None
        self.direction = "up" if str(t.get("direction", "")).endswith("_UP") else ("down" if str(t.get("direction", "")).endswith("_DOWN") else None)
        self.horizon = t.get("horizon")

    def yes_wins(self, settle_px):
        """Settlement rule per family: Up/Down and Above/Below are 'at or above'; ranges are inclusive on both bounds."""
        if self.kind == "updown": return self.p2b is not None and settle_px >= self.p2b
        if self.market_type.endswith("ABOVE_BELOW"): return self.strike is not None and settle_px >= self.strike
        if self.market_type.endswith("RANGE"): return (self.r_lo is None or settle_px >= self.r_lo) and (self.r_hi is None or settle_px <= self.r_hi)
        if self.market_type.endswith("ONE_TOUCH") and self.strike is not None:
            return settle_px >= self.strike if self.direction != "down" else settle_px <= self.strike
        return None

    def apply_book(self, md):
        bids = [(float(b["px"]["value"]), float(b["qty"])) for b in md.get("bids", [])]
        offers = [(float(b["px"]["value"]), float(b["qty"])) for b in md.get("offers", [])]
        self.bids = sorted(bids, reverse=True); self.offers = sorted(offers); self.state = md.get("state")
        self.book_ts = time.time(); st = md.get("stats") or {}; self.stats = st
        try:
            traded = float(st.get("sharesTraded") or 0); lp = st.get("lastTradePx"); lq = float(st.get("lastTradeQty") or 0)
            lts = parse_ts(st.get("lastTradeSetTime")) or 0
            if lp and traded > self.shares_traded and lts > self.last_trade_ts:
                px = float(lp["value"]); delta = traded - self.shares_traded
                self.tape.append((lts, px, delta)); self.last_px = px; self.last_qty = lq; self.last_trade_ts = lts
            self.shares_traded = max(self.shares_traded, traded)
        except Exception as e:
            log.debug("stats parse %s", e)

    @property
    def best_bid(self): return self.bids[0][0] if self.bids else None
    @property
    def best_ask(self): return self.offers[0][0] if self.offers else None
    @property
    def mid(self):
        if self.best_bid is not None and self.best_ask is not None: return (self.best_bid + self.best_ask) / 2
        return self.best_bid if self.best_bid is not None else self.best_ask

    def remaining(self, now=None): return self.end - (now or time.time())

    def snapshot(self):
        return {"slug": self.slug, "asset": self.asset, "horizon": self.horizon, "start": self.start, "end": self.end,
                "p2b": self.p2b, "settle_px": self.settle_px, "status": self.status, "state": self.state,
                "bid": self.best_bid, "ask": self.best_ask, "last": self.last_px, "shares_traded": self.shares_traded,
                "bid_depth": sum(q for _, q in self.bids[:5]), "ask_depth": sum(q for _, q in self.offers[:5]), "book_age": round(time.time() - self.book_ts, 1) if self.book_ts else None}


class MarketFeed:
    """Discovers the live Up/Down windows and polls their books (REST; the websocket needs an API key)."""
    def __init__(self, assets=ASSETS, horizons=HORIZONS):
        self.assets = assets; self.horizons = horizons; self.markets = {}; self.lock = threading.Lock()
        self.missing = {}; self.closed_recent = collections.OrderedDict()

    def discover(self):
        now = time.time()
        for a in self.assets:
            for h, d in self.horizons.items():
                start = int(now // d) * d
                for s in (start, start + d):
                    slug = window_slug(a, h, s)
                    if slug in self.markets or slug in self.closed_recent: continue
                    if self.missing.get(slug, 0) > now: continue
                    raw = get_json(f"{GATEWAY}/v1/markets?slug={urllib.parse.quote(slug)}")
                    ms = (raw or {}).get("markets") or []
                    if ms and ms[0].get("assetPriceTerms"):
                        with self.lock: self.markets[slug] = Market(ms[0]); log.info("listed %s p2b=%s", slug, self.markets[slug].p2b)
                    else:
                        self.missing[slug] = now + 10

    def refresh_terms(self, m):
        raw = get_json(f"{GATEWAY}/v1/markets?slug={urllib.parse.quote(m.slug)}")
        ms = (raw or {}).get("markets") or []
        if ms: m.update(ms[0])

    def poll_books(self):
        now = time.time()
        for m in list(self.markets.values()):
            if m.end < now - 5 or m.start > now + 20: continue
            # request budget: 5-minute books every second, 15-minute/hourly every 3 s, held ladder legs every 30 s,
            # and the final 20 s of any window every second
            if getattr(m, "kind", "updown") != "updown": interval = 60
            elif m.end - now < 20: interval = 0.9
            elif m.dur <= 300: interval = 2
            else: interval = 6
            if now - m.book_ts < interval: continue
            if m.book_src == "ws" and now - m.book_ts < 15: continue          # streamed book is live; REST only as a fallback
            d = get_json(f"{GATEWAY}/v1/markets/{m.slug}/book")
            if d and d.get("marketData"): m.apply_book(d["marketData"]); m.book_src = "rest"

    def retire(self, slug):
        with self.lock:
            m = self.markets.pop(slug, None)
            if m: self.closed_recent[slug] = time.time()
            while len(self.closed_recent) > 200: self.closed_recent.popitem(last=False)
        return m


class IndexFeed:
    """BRTI (and the other reference indices) as 1-second candles from Polymarket US's asset-prices endpoint."""
    def __init__(self, assets=ASSETS, keep=3600):
        self.assets = assets; self.series = {a: collections.OrderedDict() for a in assets}; self.keep = keep
        self.lock = threading.Lock(); self.last_ok = 0.0

    def poll(self, lookback=180):
        now = int(time.time()); q = "&".join(f"assets=crypto:{a}" for a in self.assets)
        d = get_json(f"{GATEWAY}/v1/asset-prices/history?{q}&from={now - lookback}&to=0&bucket=ASSET_PRICE_BUCKET_1S")
        if not d: return
        with self.lock:
            for s in d.get("series", []):
                a = s["asset"]["symbol"]; ser = self.series.setdefault(a, collections.OrderedDict())
                for c in s.get("candles", []):
                    if c.get("tickCount", 0) > 0: ser[int(c["timestamp"])] = float(c["close"])
                while ser and next(iter(ser)) < now - self.keep: ser.popitem(last=False)
            self.last_ok = time.time()

    def last(self, a):
        with self.lock:
            ser = self.series.get(a)
            if not ser: return None, None
            ts = next(reversed(ser)); return ts, ser[ts]

    def avg(self, a, t0, t1):
        """Mean of the 1-second index values with t0 <= ts < t1; returns (mean, n). Direct lookups, no scan."""
        with self.lock:
            ser = self.series.get(a) or {}
            vals = [ser[ts] for ts in range(int(t0), int(t1)) if ts in ser]
        return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)

    def sigma(self, a, seconds=1200):
        """Per-second log-return std over the recent window (floored; fallback 2e-4). Cached for 10 s."""
        cache = self.__dict__.setdefault("_sig_cache", {}); now = time.time()
        hit = cache.get(a)
        if hit and now - hit[0] < 10: return hit[1]
        val = self._sigma(a, seconds); cache[a] = (now, val); return val

    def _sigma(self, a, seconds=1200):
        with self.lock: items = list((self.series.get(a) or {}).items())
        if len(items) < 60: return 2e-4
        items = items[-seconds:]; import math
        step = 5   # 5-second differences scaled back to 1 second: less biased by the index's smoothing than 1-second ticks
        rets = [math.log(items[i][1] / items[i - step][1]) for i in range(step, len(items)) if items[i][0] - items[i - step][0] == step and items[i - step][1] > 0]
        if len(rets) < 30: return 2e-4
        m = sum(rets) / len(rets); var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
        return max((var / step) ** 0.5, 4e-5)


class SpotFeed:
    """Coinbase Exchange websocket ticker: sub-second spot used as the fast signal on top of the index."""
    PRODUCTS = {"btc": "BTC-USD", "eth": "ETH-USD", "sol": "SOL-USD", "xrp": "XRP-USD"}
    def __init__(self):
        self.price = {}; self.ts = {}; self.lock = threading.Lock(); self.connected = False; self.msgs = 0
        self.hist = {a: collections.deque(maxlen=600) for a in self.PRODUCTS}   # (ts, price) for the last ~minutes

    def run(self):
        import asyncio, websockets
        async def main():
            while True:
                try:
                    async with websockets.connect("wss://ws-feed.exchange.coinbase.com", ssl=_CTX, open_timeout=30, ping_interval=20) as ws:
                        await ws.send(json.dumps({"type": "subscribe", "product_ids": list(self.PRODUCTS.values()), "channels": ["ticker"]}))
                        self.connected = True
                        async for raw in ws:
                            m = json.loads(raw)
                            if m.get("type") == "ticker" and m.get("price"):
                                a = next((k for k, v in self.PRODUCTS.items() if v == m["product_id"]), None)
                                if a:
                                    with self.lock:
                                        self.price[a] = float(m["price"]); self.ts[a] = time.time(); self.msgs += 1; self.hist[a].append((self.ts[a], self.price[a]))
                except Exception as e:
                    self.connected = False; log.warning("coinbase ws: %s", e); await asyncio.sleep(3)
        asyncio.run(main())

    def last(self, a):
        with self.lock: return self.price.get(a), self.ts.get(a)

    def at(self, a, ts):
        """Spot price at (or just before) a timestamp, from the recent history."""
        with self.lock:
            best = None
            for t, px in self.hist.get(a, ()):
                if t <= ts + 0.5: best = px
                else: break
            return best


def crosscheck_brti(index_feed):
    """Compare Polymarket's BRTI candles with the values embedded in CF Benchmarks' public BRTI page."""
    try:
        import subprocess
        html = subprocess.run(["curl", "-sS", "-m", "40", "https://www.cfbenchmarks.com/data/indices/BRTI"], capture_output=True, text=True, timeout=60).stdout
        m = re.search(r'"rtis":(\[\{.*?\}\])', html)
        if not m: return {"ok": False, "reason": "no rtis block"}
        rtis = json.loads(m.group(1)); ser = index_feed.series.get("btc") or {}
        best = None
        for off in (-3, -2, -1, 0, 1, 2, 3):      # the page's timestamps may be publication time; align on the best offset
            diffs = [abs(float(x["value"]) - ser[int(x["time"]) // 1000 + off]) for x in rtis if int(x["time"]) // 1000 + off in ser]
            if diffs:
                med = sorted(diffs)[len(diffs) // 2]
                if best is None or med < best[1]: best = (off, med, max(diffs), len(diffs))
        if not best: return {"ok": False, "reason": "no overlap"}
        return {"ok": True, "ts": time.time(), "compared": best[3], "offset_s": best[0], "median_abs_diff": round(best[1], 2), "max_abs_diff": round(best[2], 2),
                "page_last_ts": int(rtis[-1]["time"]) // 1000 if rtis else None}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:120]}
