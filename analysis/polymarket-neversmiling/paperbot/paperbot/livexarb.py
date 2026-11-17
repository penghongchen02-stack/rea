"""Shadow xarb_fast (user, 11-17 12:20Z): what the cross-window arbitrage would get at the real delay. SHADOW ONLY.

This module cannot send an order: it has no code that does. It finds gaps exactly as the paper xarb_fast does
(paperbot/arbs.py XArbFast: two contracts on one coin that settle at the same instant, where the one that must be
worth more is bid above the other's ask by more than both fees). Where the paper bot books both legs at once at the
prices it saw, this one:

  1. sends both legs to the exchange's preview endpoint at the same time (it checks an order and places nothing), so
     the two round trips are the real ones;
  2. when both answers are back, reads both books again and counts what is still there at the prices it wanted;
  3. books a shadow set only for the contracts both legs still show. Contracts one leg shows and the other does not
     are a "one-legged" fill: a real bot would be left holding half an arbitrage, so they are unwound in the shadow
     at the other side of that book and the cost is recorded.

Shadow sets are held to settlement and settled like the paper engine does. The result is the paper bot's result at
the real delay, with the cost of one-legged fills in it.

A real-money version needs more than a switch: two orders cannot be made to fill together, so it would need a rule
for the leg that fills alone. That is not built.

Run: python3 -u -m paperbot.livexarb run <name> <port>. Files in paperbot-data/<name>: xarb.json (settings),
state.json, status.json, events.jsonl, live.log.
"""
import os, json, time, math, logging, threading, collections

from .arbs import XArbFast
from .ladder import LadderFeed
from .sim import Account
from .live import Exchange, order_body
from . import ws, feeds

log = logging.getLogger("livexarb")
DEFAULTS = {
    "assets": ["btc"],
    "theta": 0.095,             # taker fee coefficient (Tier 1)
    "shadow_bank_usd": 100.0,   # the balance the shadow sizes against
    "max_usd_per_arb": 20.0,    # most that one arbitrage may tie up (the buy leg's price plus the sell leg's margin, a set)
}


def load_config(dir_):
    os.makedirs(dir_, exist_ok=True); p = os.path.join(dir_, "xarb.json"); cfg = dict(DEFAULTS)
    if os.path.exists(p):
        saved = json.load(open(p)); unknown = set(saved) - set(DEFAULTS)
        if unknown: raise ValueError(f"unknown settings in xarb.json: {sorted(unknown)}")
        cfg.update(saved)
    else:
        with open(p, "w") as f: json.dump(cfg, f, indent=1)
    return cfg


def still_there(m, side, px):
    """Contracts the book shows now at the wanted price or better."""
    if side == "buy": return sum(q for p, q in m.offers if p <= px + 1e-9)
    return sum(q for p, q in m.bids if p >= px - 1e-9)


class ShadowXArb(XArbFast):
    GAP_LOG = None          # the gap logger belongs to the paper bot

    def __init__(self, engine, dir_, cfg, exchanges=None):
        self.dir = dir_; self.cfg = cfg; self.engine = engine
        acct = Account("shadow_xarb_fast", cash=cfg["shadow_bank_usd"], theta=cfg["theta"]); acct.universe = "livex"
        super().__init__(acct, engine)
        self.ex = exchanges or [Exchange(dir_), Exchange(dir_)]          # one connection a leg, so both previews travel together
        self.lock = threading.RLock(); self.events = collections.deque(maxlen=60); self.rt = collections.deque(maxlen=500)
        self.st = {"lots": {}, "tot": {"gaps": 0, "both": 0, "one_leg": 0, "neither": 0, "sets": 0.0, "locked": 0.0, "leg_cost": 0.0, "settled_sets": 0, "pnl": 0.0, "fees": 0.0}}
        self.held = {}          # slug -> market, for the lots still open
        self._load(); self._rehydrate()

    def _rehydrate(self):
        """After a restart the open lots are known but their markets are not: read each one's terms again so it can be settled."""
        import urllib.parse
        for slug in list(self.st["lots"]):
            try:
                raw = feeds.get_json(f"{feeds.GATEWAY}/v1/markets?slug={urllib.parse.quote(slug)}"); ms = (raw or {}).get("markets") or []
                if ms: self.held[slug] = feeds.Market(ms[0])
                else: log.warning("open lot in %s: market not found", slug)
            except Exception as e: log.warning("open lot in %s: %s", slug, e)

    # ---- state -----------------------------------------------------------------------
    def _path(self, n): return os.path.join(self.dir, n)

    def _load(self):
        try: saved = json.load(open(self._path("state.json")))
        except (OSError, ValueError): return
        self.st["tot"].update(saved.get("tot", {})); self.st["lots"] = saved.get("lots", {})

    def _save(self):
        with self.lock:
            tmp = self._path("state.json.tmp")
            with open(tmp, "w") as f: json.dump(self.st, f)
            os.replace(tmp, self._path("state.json"))

    def _event(self, kind, **kw):
        rec = {"ts": round(time.time(), 3), "kind": kind, **kw}; self.events.appendleft(rec)
        try:
            with open(self._path("events.jsonl"), "a") as f: f.write(json.dumps(rec, default=str) + "\n")
        except OSError as e: log.warning("cannot write events: %s", e)

    def open_cost(self):
        """Dollars tied up in open shadow lots: the price of what was bought, the margin of what was sold."""
        return sum(abs(q) * (px if q > 0 else 1 - px) for lots in self.st["lots"].values() for q, px, fee in lots)

    # ---- the paper bot calls this where it would fill both legs -------------------------------
    def execute(self, legs, n, key, label, edge):
        (a, sa, pa, ta), (b, sb, pb, tb) = legs          # buy a at pa, sell b at pb
        set_cost = pa + (1 - pb); room = self.cfg["shadow_bank_usd"] - self.open_cost()
        in_pair = sum(abs(q) * (px if q > 0 else 1 - px) for slug in (a.slug, b.slug) for q, px, fee in self.st["lots"].get(slug, []))
        n = min(n, (self.cfg["max_usd_per_arb"] - in_pair) / max(set_cost, 0.01), room / max(set_cost, 0.01))      # the limit is for the pair, however many passes take it
        n = math.floor(n * 100) / 100
        if n < max(a.min_qty or 0.01, b.min_qty or 0.01): self.stats["unaffordable"] += 1; return False
        res = [None, None]
        def send(i, m, side, px):
            res[i] = self.ex[i].preview(order_body(m.slug, side, px, n))
        th = [threading.Thread(target=send, args=(0, a, "buy", pa)), threading.Thread(target=send, args=(1, b, "sell", pb))]
        t0 = time.perf_counter()
        for t in th: t.start()
        for t in th: t.join()
        both_ms = (time.perf_counter() - t0) * 1000.0; self.rt.append(both_ms)
        ok = all(r and r[0] == 200 for r in res)
        for m in (a, b):                                   # a strike's book comes by request: read it again now
            if m.kind != "updown": self.lf.fetch_book(m, max_age=0.0)
        fa = min(n, still_there(a, "buy", pa)) if ok else 0.0; fb = min(n, still_there(b, "sell", pb)) if ok else 0.0
        fa = math.floor(fa * 100) / 100; fb = math.floor(fb * 100) / 100; got = min(fa, fb); T = self.st["tot"]
        lk = self.acct.liq_key                              # the displayed size is used up, so the next pass does not count it again
        for m, side, px in ((a, "buy", pa), (b, "sell", pb)): m.consumed[(lk, side, px)] = m.consumed.get((lk, side, px), 0.0) + n
        leg_cost = 0.0; legged = 0.0
        with self.lock:
            T["gaps"] += 1
            if got >= 0.01:
                fee_a = round(self.fee(pa) * got, 2); fee_b = round(self.fee(pb) * got, 2)
                self.st["lots"].setdefault(a.slug, []).append([got, pa, fee_a]); self.st["lots"].setdefault(b.slug, []).append([-got, pb, fee_b])
                self.held[a.slug] = a; self.held[b.slug] = b
                T["both"] += 1; T["sets"] += got; T["locked"] += got * (pb - pa) - fee_a - fee_b; T["fees"] += fee_a + fee_b
            if abs(fa - fb) >= 0.01:                        # one leg would have filled more than the other: unwind the surplus
                m, side, px, extra = (a, "buy", pa, fa - got) if fa > fb else (b, "sell", pb, fb - got)
                out = m.best_bid if side == "buy" else m.best_ask
                if out is None: out = px - 0.05 if side == "buy" else px + 0.05
                leg_cost = extra * abs(px - out) + round(self.fee(px) * extra, 2) + round(self.fee(out) * extra, 2); legged = extra
                T["leg_cost"] += leg_cost; T["pnl"] -= leg_cost
                if got < 0.01: T["one_leg"] += 1
            elif got < 0.01: T["neither"] += 1
            self._save()
        self._event("gap", buy=a.slug, sell=b.slug, ask=pa, bid=pb, edge=round(edge, 4), wanted=n, buy_left=fa, sell_left=fb, sets=got,
                    one_legged=round(legged, 2), leg_cost=round(leg_cost, 4), ms=round(both_ms, 1), preview_ok=ok,
                    answer=None if ok else [str(r)[:200] for r in res])
        log.info("gap %s -> %s edge %.4f wanted %.2f: buy leg %.2f, sell leg %.2f still there after %.0f ms", a.slug, b.slug, edge, n, fa, fb, both_ms)
        return got >= 0.01 and legged < 0.01

    def on_settle(self, m, up):
        with self.lock:
            lots = self.st["lots"].pop(m.slug, None); self.held.pop(m.slug, None)
            if not lots: return
            pnl = sum(q * ((1.0 if up else 0.0) - px) - fee for q, px, fee in lots); T = self.st["tot"]
            T["pnl"] += pnl; T["settled_sets"] += sum(abs(q) for q, _, _ in lots) / 2.0
            self._event("settled", slug=m.slug, up=bool(up), contracts=round(sum(q for q, _, _ in lots), 2), pnl=round(pnl, 4)); self._save()

    def status(self, feeds_info):
        rt = sorted(self.rt); T = self.st["tot"]
        return {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "mode": "SHADOW ONLY", "assets": self.cfg["assets"], "limits": self.cfg,
                "totals": {k: round(v, 2) if isinstance(v, float) else v for k, v in T.items()}, "open_cost": round(self.open_cost(), 2),
                "open": {s: [round(sum(q for q, _, _ in l), 2), len(l)] for s, l in self.st["lots"].items()},
                "round_trip_ms": {"n": len(rt), "median": round(rt[len(rt) // 2], 1), "p90": round(rt[int(len(rt) * 0.9)], 1), "max": round(rt[-1], 1)} if rt else None,
                "scan": dict(self.stats), "feeds": feeds_info, "events": list(self.events)[:25]}


class Standalone:
    """Its own process: order books for the coin's 5-minute, 15-minute and 1-hour windows by stream, the hourly strikes by
    request, one scan a second (the paper bot's pace)."""
    def __init__(self, data_dir, name, port):
        self.name = name; self.port = port; self.dir = os.path.join(data_dir, name); self.cfg = load_config(self.dir); self.started = time.time()
        assets = list(self.cfg["assets"])
        self.mf = feeds.MarketFeed(assets=assets); self.idx = feeds.IndexFeed(); self.spot = feeds.SpotFeed(); self.lf = LadderFeed(assets=assets)
        self.ws = ws.WSBooks(self.mf); self.errors = 0; self.bot = ShadowXArb(self, self.dir, self.cfg)

    def _loop(self, fn, gap, what):
        while True:
            try: fn()
            except Exception as e: log.warning("%s: %s", what, e)
            time.sleep(gap)

    def _index(self):
        if time.time() - getattr(self, "_full", 0) > 30: self.idx.poll(); self._full = time.time()
        else: self.idx.poll(lookback=8)

    def _warm(self):
        for ex in self.bot.ex: ex.call("GET", "/v1/account/balances", timeout=4.0)

    def settle(self, m):
        """The paper engine's rule (paperbot/run.py settle_market)."""
        self.mf.refresh_terms(m); up = None; sp = m.settle_px
        if sp is not None: up = m.yes_wins(sp)
        elif time.time() - m.end < (45 if m.kind == "updown" else 900): return False
        else:
            v = self.idx.value_at(m.asset, m.end - 1)
            if v is not None: sp = round(v, m.decimals); up = m.yes_wins(sp)
        if up is None:
            if time.time() - m.end < 240: return False
            log.warning("could not settle %s; using last price", m.slug); up = (m.last_px or 0.5) >= 0.5
        self.bot.on_settle(m, up); return True

    def _status(self):
        st = self.bot.status({"books": self.ws.status(), "windows": len(self.mf.markets), "strikes": len(self.lf.legs), "index_age_s": round(time.time() - (self.idx.last(self.cfg["assets"][0])[0] or 0), 1),
                              "rate_limited": feeds.RATE_LIMITED[0], "loop_errors": self.errors})
        tmp = os.path.join(self.dir, "status.json.tmp")
        with open(tmp, "w") as f: json.dump(st, f, default=str)
        os.replace(tmp, os.path.join(self.dir, "status.json"))

    def run(self):
        for fn, gap, what in ((self._index, 0.5, "index poll"), (self.mf.discover, 5, "discover"), (self._warm, 4, "keep-alive"), (self._status, 3, "status")):
            threading.Thread(target=self._loop, args=(fn, gap, what), daemon=True).start()
        for fn in (self.ws.run, self._http): threading.Thread(target=fn, daemon=True).start()
        log.info("shadow xarb_fast started, coins %s, dashboard on port %d", self.cfg["assets"], self.port)
        while True:
            t0 = time.time()
            try:
                self.mf.poll_books(); now = time.time()
                for m in list(self.mf.markets.values()):
                    if m.p2b is None and now - m.start > 5 and int(now) % 5 == 0: self.mf.refresh_terms(m)
                    if now >= m.end + 2 and m.slug not in self.bot.held: self.mf.retire(m.slug)
                for slug, m in list(self.bot.held.items()):          # every market with open lots, windows and strikes alike
                    if now >= m.end + 2 and self.settle(m): self.mf.retire(slug)
                if now - self.started >= 30: self.bot.scan(now)
            except Exception as e: self.errors += 1; log.exception("loop: %s", e)
            time.sleep(max(0.05, 0.25 - (time.time() - t0)))

    def _http(self):
        import http.server, socketserver
        d = self.dir; page = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "livexarb.html")
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                try:
                    if self.path.startswith("/status.json"): body = open(os.path.join(d, "status.json"), "rb").read(); ct = "application/json"
                    elif self.path in ("/", "/index.html"): body = open(page, "rb").read(); ct = "text/html; charset=utf-8"
                    else: self.send_error(404); return
                    self.send_response(200); self.send_header("Content-Type", ct); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                except OSError: self.send_error(503)
        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.ThreadingTCPServer(("0.0.0.0", self.port), H) as httpd: httpd.serve_forever()


if __name__ == "__main__":
    import sys
    data = os.environ.get("PAPERBOT_DATA", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "paperbot-data"))
    if len(sys.argv) < 4 or sys.argv[1] != "run": print("usage: python3 -u -m paperbot.livexarb run <name> <port>"); sys.exit(2)
    name = sys.argv[2]; port = int(sys.argv[3]); d = os.path.join(data, name); os.makedirs(d, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", handlers=[logging.FileHandler(os.path.join(d, "live.log")), logging.StreamHandler()])
    if not ws.available(): log.error("no API key: the shadow needs it for the order-book stream and the preview"); sys.exit(1)
    Standalone(data, name, port).run()
