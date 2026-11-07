"""Main loop: feeds -> books -> strategies -> fills -> settlement; persists to SQLite and serves the dashboard."""
import os, sys, time, json, threading, logging, sqlite3, http.server, socketserver, functools, collections
from . import feeds
from .sim import Account
from .strategies import OldModel, Improved
from .fair import p_up

DATA = os.environ.get("PAPERBOT_DATA", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "paperbot-data"))
WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
PORT = int(os.environ.get("PAPERBOT_PORT", "8080")); START_CASH = float(os.environ.get("PAPERBOT_CASH", "100"))
os.makedirs(DATA, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                    handlers=[logging.FileHandler(os.path.join(DATA, "bot.log")), logging.StreamHandler(sys.stdout)])
log = logging.getLogger("run")


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False); self.lock = threading.Lock()
        c = self.db.cursor()
        c.execute("create table if not exists fills(ts real, bot text, slug text, side text, price real, qty real, fee real, liq text, tag text, realized real)")
        c.execute("create table if not exists settlements(ts real, bot text, slug text, asset text, horizon text, up int, p2b real, settle_px real, final_qty real, pnl_settle real, pnl_total real, fees real, fills int)")
        c.execute("create table if not exists equity(ts real, bot text, equity real, cash real, bp real, fees real)")
        c.execute("create table if not exists index_log(ts int, asset text, value real)")
        self.db.commit()
    def insert(self, table, rec):
        with self.lock:
            keys = list(rec.keys()); self.db.execute(f"insert into {table}({','.join(keys)}) values({','.join('?' * len(keys))})", [rec[k] for k in keys]); self.db.commit()
    def query(self, sql, args=()):
        with self.lock: return self.db.execute(sql, args).fetchall()


class Engine:
    def __init__(self):
        self.mf = feeds.MarketFeed(); self.idx = feeds.IndexFeed(); self.spot = feeds.SpotFeed()
        self.store = Store(os.path.join(DATA, "paper.db"))
        self.accounts = {"old": Account("old", START_CASH), "improved": Account("improved", START_CASH)}
        self.strats = [OldModel(self.accounts["old"]), Improved(self.accounts["improved"])]
        self.started = time.time(); self.equity_hist = collections.deque(maxlen=20000); self.crosscheck = {}; self.lock = threading.Lock()
        self.last_state = {}; self.loop_count = 0; self.errors = 0
        self._load()

    def _load(self):
        p = os.path.join(DATA, "accounts.json")
        if os.path.exists(p):
            try:
                snap = json.load(open(p))
                for name, s in snap.get("accounts", {}).items():
                    a = self.accounts[name]; a.cash = s["cash"]; a.fees = s["fees"]; a.maker_fills = s["maker_fills"]; a.taker_fills = s["taker_fills"]
                    a.start_cash = s.get("start_cash", START_CASH)
                    for slug, ps in s.get("positions", {}).items():
                        pos = a.positions[slug]; pos.qty = ps["qty"]; pos.avg = ps["avg"]; pos.realized = ps["realized"]; pos.fees = ps["fees"]; pos.fills = ps["fills"]; pos.closed = ps["closed"]
                    a.settlements = s.get("settlements", [])
                self.started = snap.get("started", self.started)
                for row in self.store.query("select ts, bot, equity from equity order by ts"): self.equity_hist.append(row)
                log.info("resumed state from %s", p)
            except Exception as e:
                log.warning("could not resume: %s", e)

    def _save(self):
        snap = {"started": self.started, "accounts": {}}
        for name, a in self.accounts.items():
            snap["accounts"][name] = {"cash": a.cash, "fees": a.fees, "maker_fills": a.maker_fills, "taker_fills": a.taker_fills, "start_cash": a.start_cash,
                "positions": {s: {"qty": p.qty, "avg": p.avg, "realized": p.realized, "fees": p.fees, "fills": p.fills, "closed": p.closed} for s, p in a.positions.items()},
                "settlements": a.settlements[-500:]}
        tmp = os.path.join(DATA, "accounts.json.tmp"); json.dump(snap, open(tmp, "w")); os.replace(tmp, os.path.join(DATA, "accounts.json"))

    # ---- threads ---------------------------------------------------------
    def t_index(self):
        last_log = 0
        while True:
            try:
                self.idx.poll()
                if time.time() - last_log > 10:
                    for a in feeds.ASSETS:
                        ts, v = self.idx.last(a)
                        if ts: self.store.insert("index_log", {"ts": ts, "asset": a, "value": v})
                    last_log = time.time()
            except Exception as e: log.warning("index poll: %s", e)
            time.sleep(2)

    def t_discover(self):
        while True:
            try: self.mf.discover()
            except Exception as e: log.warning("discover: %s", e)
            time.sleep(5)

    def t_crosscheck(self):
        time.sleep(90)
        while True:
            try: self.crosscheck = feeds.crosscheck_brti(self.idx); log.info("BRTI crosscheck %s", self.crosscheck)
            except Exception as e: log.warning("crosscheck: %s", e)
            time.sleep(900)

    def t_http(self):
        handler = functools.partial(Handler, engine=self)
        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.ThreadingTCPServer(("0.0.0.0", PORT), handler) as httpd:
            log.info("dashboard on port %d", PORT); httpd.serve_forever()

    # ---- main loop -------------------------------------------------------
    def settle_market(self, m):
        # authoritative: settlementPrice from the market; fallback: our own 60-second average of the index
        self.mf.refresh_terms(m); up = None; sp = m.settle_px; src = "exchange"
        if sp is not None and m.p2b is not None: up = sp >= m.p2b
        elif time.time() - m.end < 45: return False        # give the exchange time to publish settlementPrice
        else:
            avg, n = self.idx.avg(m.asset, m.end - 60, m.end)
            if avg is not None and n >= 55 and m.p2b is not None: sp = round(avg, m.decimals); up = sp >= m.p2b; src = "index-average"
        if up is None:
            if time.time() - m.end < 240: return False     # wait for data
            log.warning("could not settle %s; using last price", m.slug); up = (m.last_px or 0.5) >= 0.5
        for s in self.strats: s.on_close(m)
        for a in self.accounts.values():
            rec = a.settle(m, up, sp)
            if rec: self.store.insert("settlements", {k: v for k, v in rec.items() if k != "ts"} | {"ts": rec["ts"], "up": int(up)})
        log.info("settled %s up=%s p2b=%s settle=%s (%s)", m.slug, up, m.p2b, sp, src); return True

    def loop(self):
        ctx = {"index": self.idx, "spot": self.spot}; last_save = 0; last_eq = 0
        while True:
            t0 = time.time(); self.loop_count += 1
            try:
                self.mf.poll_books(); now = time.time()
                for m in list(self.mf.markets.values()):
                    if m.p2b is None and now - m.start > 5 and int(now) % 5 == 0: self.mf.refresh_terms(m)
                    if now >= m.end + 2:
                        if self.settle_market(m): self.mf.retire(m.slug)
                        continue
                    if m.book_ts < now - 10: continue
                    for a in self.accounts.values(): a.on_book(m)
                    for s in self.strats:
                        try: s.on_tick(m, ctx, now)
                        except Exception as e: self.errors += 1; log.exception("strategy %s on %s: %s", s.name, m.slug, e)
                    for a in self.accounts.values(): a.on_book(m)
                # persist new fills
                for a in self.accounts.values():
                    while a.fills:
                        f = a.fills.pop(0); self.store.insert("fills", f); a.events.appendleft(f)
                if now - last_eq >= 5:
                    for a in self.accounts.values():
                        eq = a.equity(self.mf.markets); self.equity_hist.append((now, a.name, eq))
                        self.store.insert("equity", {"ts": now, "bot": a.name, "equity": eq, "cash": a.cash, "bp": a.buying_power(), "fees": a.fees})
                    last_eq = now
                self.build_state()
                if now - last_save > 10: self._save(); last_save = now
            except Exception as e:
                self.errors += 1; log.exception("loop: %s", e)
            time.sleep(max(0.0, 1.0 - (time.time() - t0)))

    def build_state(self):
        now = time.time(); mk = {}
        for m in self.mf.markets.values():
            s = m.snapshot(); p, det = p_up(m, self.idx, self.spot, now); s["p_up"] = round(p, 3) if p is not None else None; s["model"] = det
            s["remaining"] = round(m.remaining(now)); s["positions"] = {}; s["orders"] = {}
            for name, a in self.accounts.items():
                pos = a.positions.get(m.slug); s["positions"][name] = round(pos.qty, 2) if pos and not pos.closed else 0.0
                s["orders"][name] = [{"side": o.side, "px": o.price, "qty": round(o.remaining, 2), "tag": o.tag} for o in a.open_orders(m.slug)]
            mk[m.slug] = s
        idx = {a: dict(zip(("ts", "value"), self.idx.last(a))) for a in feeds.ASSETS}
        spot = {a: dict(zip(("value", "ts"), self.spot.last(a))) for a in feeds.ASSETS}
        hist = {}
        for ts, name, eq in self.equity_hist: hist.setdefault(name, []).append([round(ts), round(eq, 3)])
        for name in hist:
            if len(hist[name]) > 1500:
                step = len(hist[name]) // 1500 + 1; hist[name] = hist[name][::step] + hist[name][-1:]
        settles = []
        for a in self.accounts.values(): settles += a.settlements[-60:]
        settles.sort(key=lambda r: -r["ts"])
        fills = []
        for a in self.accounts.values(): fills += list(a.events)[:60]
        fills.sort(key=lambda r: -r["ts"])
        self.last_state = {"ts": now, "started": self.started, "uptime": round(now - self.started), "loop": self.loop_count, "errors": self.errors,
                           "accounts": {n: a.snapshot(self.mf.markets) for n, a in self.accounts.items()}, "markets": mk, "index": idx, "spot": spot,
                           "feeds": {"index_age": round(now - self.idx.last_ok, 1) if self.idx.last_ok else None, "coinbase_connected": self.spot.connected, "coinbase_msgs": self.spot.msgs,
                                     "books_polled": len(self.mf.markets), "brti_crosscheck": self.crosscheck},
                           "equity_history": hist, "settlements": settles[:80], "fills": fills[:80], "config": {"start_cash": START_CASH, "theta": 0.095, "assets": feeds.ASSETS, "horizons": list(feeds.HORIZONS)}}

    def start(self):
        for fn in (self.t_index, self.t_discover, self.t_crosscheck, self.t_http, self.spot.run):
            threading.Thread(target=fn, daemon=True).start()
        time.sleep(3); self.loop()


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, engine=None, **kw):
        self.engine = engine; super().__init__(*a, directory=WEB, **kw)
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/state.json"):
            body = json.dumps(self.engine.last_state).encode(); self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        if self.path.startswith("/history.json"):
            rows = self.engine.store.query("select ts, bot, slug, asset, horizon, up, p2b, settle_px, final_qty, pnl_settle, pnl_total, fees, fills from settlements order by ts desc limit 2000")
            body = json.dumps({"settlements": rows}).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        if self.path == "/": self.path = "/index.html"
        super().do_GET()


if __name__ == "__main__":
    Engine().start()
