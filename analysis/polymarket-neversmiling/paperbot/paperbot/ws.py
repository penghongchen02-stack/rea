"""Polymarket US markets websocket: live books for every tracked market from one connection instead of REST polling.

Needs an API key (Key ID + Secret Key from polymarket.us/developer) in the environment as POLYMARKET_KEY_ID and
POLYMARKET_SECRET_KEY. Each handshake is signed (Ed25519 over timestamp + "GET" + path) as the REST API is; the
secret never leaves this process. Without the variables the engine keeps polling REST and this module is idle.
"""
import os, time, json, base64, asyncio, logging, threading
from . import feeds

log = logging.getLogger("ws")
URL = "wss://api.polymarket.us/v1/ws/markets"; PATH = "/v1/ws/markets"
KEY_ID = os.environ.get("POLYMARKET_KEY_ID"); SECRET = os.environ.get("POLYMARKET_SECRET_KEY")


def available(): return bool(KEY_ID and SECRET) and "PASTE" not in (KEY_ID + SECRET).upper()   # placeholders left in the settings


def auth_headers(path=PATH):
    from cryptography.hazmat.primitives.asymmetric import ed25519
    pk = ed25519.Ed25519PrivateKey.from_private_bytes(base64.b64decode(SECRET)[:32])
    ts = str(int(time.time() * 1000)); sig = base64.b64encode(pk.sign(f"{ts}GET{path}".encode())).decode()
    return {"X-PM-Access-Key": KEY_ID, "X-PM-Timestamp": ts, "X-PM-Signature": sig, "User-Agent": feeds.USER_AGENT}


class WSBooks:
    """Subscribes to full market data for every market the engine tracks (windows plus adopted ladder legs), in
    batches of at most 100 slugs, re-subscribing as the set changes. Each message is applied to the Market like a
    REST book, so strategies, fills and the dashboard see no difference except the age."""
    def __init__(self, mf):
        self.mf = mf; self.connected = False; self.msgs = 0; self.last_msg = 0.0; self.errors = 0; self.subs = {}   # request_id -> set(slugs)
        self.snake = False; self.lock = threading.Lock()

    def status(self): return {"enabled": available(), "connected": self.connected, "msgs": self.msgs, "age": round(time.time() - self.last_msg, 1) if self.last_msg else None, "subscribed": sum(len(s) for s in self.subs.values()), "errors": self.errors}

    def run(self):
        if not available(): log.info("markets websocket disabled (no POLYMARKET_KEY_ID / POLYMARKET_SECRET_KEY)"); return
        asyncio.run(self.main())

    def _sub_msg(self, rid, slugs):
        if self.snake: return json.dumps({"subscribe": {"request_id": rid, "subscription_type": 1, "market_slugs": sorted(slugs)}})
        return json.dumps({"subscribe": {"requestId": rid, "subscriptionType": "SUBSCRIPTION_TYPE_MARKET_DATA", "marketSlugs": sorted(slugs)}})

    def _unsub_msg(self, rid):
        return json.dumps({"unsubscribe": {"request_id" if self.snake else "requestId": rid}})

    def apply(self, msg):
        md = msg.get("marketData") or msg.get("market_data")
        if not md: return
        slug = md.get("marketSlug") or md.get("market_slug")
        m = self.mf.markets.get(slug)
        if m is None: return
        m.apply_book(md); m.book_src = "ws"
        self.msgs += 1; self.last_msg = time.time()

    async def main(self):
        import websockets
        backoff = 2; rid_n = 0
        while True:
            try:
                async with websockets.connect(URL, additional_headers=auth_headers(), ssl=feeds._CTX, open_timeout=30, ping_interval=20) as ws:
                    self.connected = True; backoff = 2; self.subs = {}; log.info("markets websocket connected")
                    last_sync = 0.0; conn_at = time.time()      # a quiet stream is judged from this connection's start
                    while True:
                        now = time.time()
                        if now - last_sync > 2:
                            last_sync = now
                            wanted = set(self.mf.markets); have = set().union(*self.subs.values()) if self.subs else set()
                            # the server caps subscriptions per connection, so when the market set changes, replace every
                            # request with the fewest batches of at most 100 slugs instead of adding one request per change
                            if wanted != have:
                                for rid in list(self.subs): await ws.send(self._unsub_msg(rid))
                                self.subs = {}; slugs = sorted(wanted)
                                for i in range(0, len(slugs), 100):
                                    rid_n += 1; rid = f"md-{rid_n}"; batch = set(slugs[i:i + 100])
                                    await ws.send(self._sub_msg(rid, batch)); self.subs[rid] = batch
                        try: raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                        except asyncio.TimeoutError:
                            if now - max(self.last_msg, conn_at) > 60: raise RuntimeError("no messages for 60 s")
                            continue
                        msg = json.loads(raw)
                        if "heartbeat" in msg:
                            try: await ws.send(json.dumps({"heartbeat": {}}))
                            except Exception: pass
                            continue
                        if msg.get("error"):
                            self.errors += 1; log.warning("markets websocket error: %s", msg)
                            text = str(msg).lower()
                            # a field-format rejection switches to snake_case; the subscription cap is not one
                            if not self.snake and "max subscriptions" not in text and ("subscription" in text or "unknown" in text):
                                self.snake = True; self.subs = {}; log.info("markets websocket: retrying with snake_case fields")
                            continue
                        self.apply(msg)
            except Exception as e:
                self.connected = False; self.errors += 1; log.warning("markets websocket: %s (reconnect in %ds)", e, backoff)
                await asyncio.sleep(backoff); backoff = min(60, backoff * 2)
