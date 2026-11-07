"""Main loop: shared feeds -> books -> every universe's strategies -> fills -> settlement.

A universe is a complete set of bot accounts with its own sizing rule, database, resume file and dashboard port.
  std    : fixed contract clips, port 8080, data in PAPERBOT_DATA/
  dollar : every order sized to a random $0.10-$10, port 8081, data in PAPERBOT_DATA/dollar/
Every bot in strategies.build_registry() (plus drop-ins under paperbot/bots/) is created in every universe.
"""
import os, sys, time, json, threading, logging, sqlite3, http.server, socketserver, functools, collections
from . import feeds
from .sim import Account
from .strategies import build_registry, LADDER_BOTS
from .arbs import ARB_BOTS, make_arbs
from .ladder import LadderFeed, LadderArb, LadderModel, LadderTouch, XArb
from .fair import p_up

DATA = os.environ.get("PAPERBOT_DATA", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "paperbot-data"))
WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
START_CASH = float(os.environ.get("PAPERBOT_CASH", "100"))
UNIVERSES = [
    {"name": "std", "port": int(os.environ.get("PAPERBOT_PORT", "8080")), "size_mode": "contracts", "dir": DATA, "pos_mult": 1.0},
    {"name": "dollar", "port": int(os.environ.get("PAPERBOT_PORT2", "8081")), "size_mode": "dollar", "dir": os.path.join(DATA, "dollar"), "pos_mult": 5.0,
     "size_min": float(os.environ.get("PAPERBOT_SIZE_MIN", "0.1")), "size_max": float(os.environ.get("PAPERBOT_SIZE_MAX", "10"))},
    {"name": "dollar30", "port": int(os.environ.get("PAPERBOT_PORT3", "8082")), "size_mode": "dollar", "dir": os.path.join(DATA, "dollar30"), "pos_mult": 15.0,
     "size_min": 0.1, "size_max": 30.0, "cash": float(os.environ.get("PAPERBOT_CASH3", "1000"))},
]
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


class Universe:
    def __init__(self, cfg, engine):
        self.cfg = cfg; self.name = cfg["name"]; self.port = cfg["port"]; self.dir = cfg["dir"]; os.makedirs(self.dir, exist_ok=True)
        self.store = Store(os.path.join(self.dir, "paper.db")); self.engine = engine
        self.cash = cfg.get("cash", START_CASH)
        mk = lambda k: Account(k, self.cash, size_mode=cfg["size_mode"], size_min=cfg.get("size_min", 0.1), size_max=cfg.get("size_max", 10.0))
        reg = build_registry(cfg.get("pos_mult", 1.0))
        self.accounts = {k: mk(k) for k in list(reg) + list(LADDER_BOTS) + list(ARB_BOTS)}
        active = os.environ.get("PAPERBOT_ACTIVE", "all").split(",")
        self.active = list(self.accounts) if active == ["all"] else active
        self.strats = [reg[k](self.accounts[k]) for k in self.active if k in reg]
        self.ladder = LadderArb(self.accounts["ladder"], engine.lf); self.ladder_model = LadderModel(self.accounts["ladder_model"], engine.lf, engine.idx, engine.spot)
        self.touch_model = LadderTouch(self.accounts["touch_model"], engine.lf, engine.idx, engine.spot)
        self.xarb = XArb(self.accounts["xarb"], engine.mf, engine.lf)
        self.arbs = make_arbs(self.accounts, engine)
        self.equity_hist = collections.deque(maxlen=20000); self.last_state = {}; self.started = time.time()
        self.labels = {}
        try:
            from .strategies import RANDOM_BOTS; self.labels = {k: v[2] for k, v in RANDOM_BOTS.items()}
        except Exception: pass
        self._load()

    def _load(self):
        p = os.path.join(self.dir, "accounts.json")
        if os.path.exists(p):
            try:
                snap = json.load(open(p))
                for name, s in snap.get("accounts", {}).items():
                    if name not in self.accounts: continue
                    a = self.accounts[name]; a.cash = s["cash"]; a.fees = s["fees"]; a.maker_fills = s["maker_fills"]; a.taker_fills = s["taker_fills"]; a.start_cash = s.get("start_cash", self.cash)
                    for slug, ps in s.get("positions", {}).items():
                        pos = a.positions[slug]; pos.qty = ps["qty"]; pos.avg = ps["avg"]; pos.realized = ps["realized"]; pos.fees = ps["fees"]; pos.fills = ps["fills"]; pos.closed = ps["closed"]
                    a.settlements = s.get("settlements", [])
                self.started = snap.get("started", self.started)
                for row in self.store.query("select ts, bot, equity from equity order by ts"): self.equity_hist.append(row)
                log.info("[%s] resumed state from %s", self.name, p)
            except Exception as e: log.warning("[%s] could not resume: %s", self.name, e)

    def save(self):
        snap = {"started": self.started, "accounts": {}}
        for name, a in self.accounts.items():
            snap["accounts"][name] = {"cash": a.cash, "fees": a.fees, "maker_fills": a.maker_fills, "taker_fills": a.taker_fills, "start_cash": a.start_cash,
                "positions": {s: {"qty": p.qty, "avg": p.avg, "realized": p.realized, "fees": p.fees, "fills": p.fills, "closed": p.closed} for s, p in a.positions.items()},
                "settlements": a.settlements[-500:]}
        tmp = os.path.join(self.dir, "accounts.json.tmp"); json.dump(snap, open(tmp, "w")); os.replace(tmp, os.path.join(self.dir, "accounts.json"))

    def build_state(self, engine, now):
        mk = {}
        for m in engine.mf.markets.values():
            s = m.snapshot(); p, det = p_up(m, engine.idx, engine.spot, now); s["p_up"] = round(p, 3) if p is not None else None; s["model"] = det
            s["remaining"] = round(m.remaining(now)); s["positions"] = {}; s["orders"] = {}
            for name, a in self.accounts.items():
                pos = a.positions.get(m.slug); s["positions"][name] = round(pos.qty, 2) if pos and not pos.closed else 0.0
                s["orders"][name] = [{"side": o.side, "px": o.price, "qty": round(o.remaining, 2), "tag": o.tag} for o in a.open_orders(m.slug)]
            mk[m.slug] = s
        idx = {a: dict(zip(("ts", "value"), engine.idx.last(a))) for a in feeds.ASSETS}
        spot = {a: dict(zip(("value", "ts"), engine.spot.last(a))) for a in feeds.ASSETS}
        hist = {}
        for ts, name, eq in self.equity_hist: hist.setdefault(name, []).append([round(ts), round(eq, 3)])
        for name in hist:
            if len(hist[name]) > 1500: step = len(hist[name]) // 1500 + 1; hist[name] = hist[name][::step] + hist[name][-1:]
        settles = []; fills = []
        for a in self.accounts.values(): settles += a.settlements[-60:]; fills += list(a.events)[:60]
        settles.sort(key=lambda r: -r["ts"]); fills.sort(key=lambda r: -r["ts"])
        self.last_state = {"ts": now, "started": self.started, "uptime": round(now - engine.started), "loop": engine.loop_count, "errors": engine.errors,
            "accounts": {n: a.snapshot(engine.mf.markets) for n, a in self.accounts.items()}, "markets": mk, "index": idx, "spot": spot,
            "feeds": {"index_age": round(now - engine.idx.last_ok, 1) if engine.idx.last_ok else None, "coinbase_connected": engine.spot.connected, "coinbase_msgs": engine.spot.msgs,
                      "books_polled": len(engine.mf.markets), "brti_crosscheck": engine.crosscheck},
            "equity_history": hist, "settlements": settles[:80], "fills": fills[:80], "ladder": self.ladder.snapshot(), "ladder_model": self.ladder_model.snapshot(), "touch_model": self.touch_model.snapshot(), "xarb": self.xarb.snapshot(), "arbs": {k: v.snapshot() for k, v in self.arbs.items()},
            "config": {"universe": self.name, "size_mode": self.cfg["size_mode"], "size_range": [self.cfg.get("size_min"), self.cfg.get("size_max")], "active": self.active, "labels": self.labels,
                       "start_cash": self.cash, "theta": 0.095, "assets": feeds.ASSETS, "horizons": list(feeds.HORIZONS)}}


class Engine:
    def __init__(self):
        self.mf = feeds.MarketFeed(); self.idx = feeds.IndexFeed(); self.spot = feeds.SpotFeed(); self.lf = LadderFeed()
        self.started = time.time(); self.crosscheck = {}; self.loop_count = 0; self.errors = 0
        self.universes = [Universe(cfg, self) for cfg in UNIVERSES]
        self.index_store = self.universes[0].store

    def all_accounts(self):
        for u in self.universes:
            for a in u.accounts.values(): yield u, a

    # ---- threads -----------------------------------------------------------
    def t_index(self):
        last_log = 0
        while True:
            try:
                self.idx.poll()
                if time.time() - last_log > 10:
                    for a in feeds.ASSETS:
                        ts, v = self.idx.last(a)
                        if ts: self.index_store.insert("index_log", {"ts": ts, "asset": a, "value": v})
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

    def t_http(self, u):
        handler = functools.partial(Handler, universe=u)
        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.ThreadingTCPServer(("0.0.0.0", u.port), handler) as httpd:
            log.info("[%s] dashboard on port %d", u.name, u.port); httpd.serve_forever()

    # ---- settlement ----------------------------------------------------------
    def settle_market(self, m):
        self.mf.refresh_terms(m); up = None; sp = m.settle_px; src = "exchange"
        if sp is not None: up = m.yes_wins(sp)
        elif time.time() - m.end < (45 if m.kind == "updown" else 900): return False
        else:
            avg, n = self.idx.avg(m.asset, m.end - 60, m.end)
            if avg is not None and n >= 55: sp = round(avg, m.decimals); up = m.yes_wins(sp); src = "index-average"
        if up is None:
            if time.time() - m.end < 240: return False
            log.warning("could not settle %s; using last price", m.slug); up = (m.last_px or 0.5) >= 0.5
        for u in self.universes:
            for s in u.strats: s.on_close(m)
            for a in u.accounts.values():
                rec = a.settle(m, up, sp)
                if rec: u.store.insert("settlements", {k: v for k, v in rec.items() if k != "ts"} | {"ts": rec["ts"], "up": int(up)})
        log.info("settled %s up=%s p2b=%s settle=%s (%s)", m.slug, up, m.p2b, sp, src); return True

    def settle_orphans(self):
        slugs = set()
        for u, a in self.all_accounts():
            for slug, pos in a.positions.items():
                if not pos.closed and abs(pos.qty) > 1e-9: slugs.add(slug)
        for slug in slugs:
            if slug in self.mf.markets: continue
            raw = feeds.get_json(f"{feeds.GATEWAY}/v1/markets?slug={slug}")
            ms = (raw or {}).get("markets") or []
            if not ms or not ms[0].get("assetPriceTerms"): continue
            m = feeds.Market(ms[0])
            if m.end < time.time() + 2:
                if self.settle_market(m): log.info("settled orphan %s after restart", slug)
            else:
                with self.mf.lock: self.mf.markets[slug] = m

    # ---- main loop -----------------------------------------------------------
    def loop(self):
        ctx = {"index": self.idx, "spot": self.spot}; last_save = 0; last_eq = 0
        while True:
            t0 = time.time(); self.loop_count += 1
            try:
                self.mf.poll_books(); now = time.time()
                for u in self.universes:
                    try:
                        if "ladder" in u.active: u.ladder.scan(now)
                        if "ladder_model" in u.active: u.ladder_model.scan(now)
                        if "touch_model" in u.active: u.touch_model.scan(now)
                        if "xarb" in u.active: u.xarb.scan(now)
                        for name, sc in u.arbs.items():
                            if name in u.active:
                                try: sc.scan(now)
                                except Exception as e: self.errors += 1; log.exception("[%s] arb %s: %s", u.name, name, e)
                        for a in u.accounts.values():
                            for slug, pos in a.positions.items():
                                if not pos.closed and abs(pos.qty) > 1e-9 and slug not in self.mf.markets:
                                    leg = self.lf.legs.get(slug) or getattr(self.lf, "touch_legs", {}).get(slug)
                                    if leg is not None:
                                        with self.mf.lock: self.mf.markets[slug] = leg
                    except Exception as e: self.errors += 1; log.exception("[%s] ladder: %s", u.name, e)
                for m in list(self.mf.markets.values()):
                    if m.p2b is None and now - m.start > 5 and int(now) % 5 == 0 and m.kind == "updown": self.mf.refresh_terms(m)
                    if m.kind == "ladder" and m.market_type.endswith("ONE_TOUCH") and int(now) % 300 == 0:
                        self.mf.refresh_terms(m)
                        if m.closed and m.settle_px is not None and self.settle_market(m): self.mf.retire(m.slug); continue
                    if now >= m.end + 2:
                        if self.settle_market(m): self.mf.retire(m.slug)
                        continue
                    if m.book_ts < now - 10: continue
                    for u, a in self.all_accounts(): a.on_book(m)
                    if m.kind != "updown": continue
                    for u in self.universes:
                        for s in u.strats:
                            try: s.on_tick(m, ctx, now)
                            except Exception as e: self.errors += 1; log.exception("[%s] strategy %s on %s: %s", u.name, s.name, m.slug, e)
                    for u, a in self.all_accounts(): a.on_book(m)
                for u in self.universes:
                    for a in u.accounts.values():
                        while a.fills:
                            f = a.fills.pop(0); u.store.insert("fills", f); a.events.appendleft(f)
                if now - last_eq >= 5:
                    for u in self.universes:
                        for a in u.accounts.values():
                            eq = a.equity(self.mf.markets); u.equity_hist.append((now, a.name, eq))
                            u.store.insert("equity", {"ts": now, "bot": a.name, "equity": eq, "cash": a.cash, "bp": a.buying_power(), "fees": a.fees})
                    last_eq = now
                for u in self.universes: u.build_state(self, now)
                if now - last_save > 10:
                    for u in self.universes: u.save()
                    last_save = now
            except Exception as e:
                self.errors += 1; log.exception("loop: %s", e)
            time.sleep(max(0.0, 1.0 - (time.time() - t0)))

    def start(self):
        for fn in (self.t_index, self.t_discover, self.t_crosscheck, self.spot.run):
            threading.Thread(target=fn, daemon=True).start()
        for u in self.universes: threading.Thread(target=self.t_http, args=(u,), daemon=True).start()
        time.sleep(3)
        try: self.settle_orphans()
        except Exception as e: log.warning("orphan settlement: %s", e)
        self.loop()


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, universe=None, **kw):
        self.universe = universe; super().__init__(*a, directory=WEB, **kw)
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/state.json"):
            body = json.dumps(self.universe.last_state).encode(); self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        if self.path.startswith("/history.json"):
            rows = self.universe.store.query("select ts, bot, slug, asset, horizon, up, p2b, settle_px, final_qty, pnl_settle, pnl_total, fees, fills from settlements order by ts desc limit 2000")
            body = json.dumps({"settlements": rows}).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        if self.path == "/": self.path = "/index.html"
        super().do_GET()


if __name__ == "__main__":
    Engine().start()
