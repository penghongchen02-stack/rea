"""Main loop: feeds -> books -> strategies -> fills -> settlement; persists to SQLite and serves the dashboard."""
import os, sys, time, json, threading, logging, sqlite3, http.server, socketserver, functools, collections
from . import feeds
from .ladder import LadderFeed, LadderArb
from .sim import Account
from .strategies import OldModel, Improved, FinalMinute, WhiskasReplica, LastCall, LastCallTilt, LastCallHold, LastCallRest, LastCallEarly, Param, SWEEP, MarketFollow, RANDOM_BOTS, PickOff, Scalper
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
        self.accounts = {"old": Account("old", START_CASH), "improved": Account("improved", START_CASH), "final": Account("final", START_CASH), "whiskas": Account("whiskas", START_CASH), "lastcall": Account("lastcall", START_CASH), "lastcall_tilt": Account("lastcall_tilt", START_CASH), "lastcall_hold": Account("lastcall_hold", START_CASH), "lastcall_rest": Account("lastcall_rest", START_CASH), "lastcall_early": Account("lastcall_early", START_CASH)}
        for k in SWEEP: self.accounts[k] = Account(k, START_CASH)
        for k in ("follow80", "follow80_model"): self.accounts[k] = Account(k, START_CASH)
        for k in RANDOM_BOTS: self.accounts[k] = Account(k, START_CASH)
        for k in ("ladder", "pickoff", "hourly_last", "hourly_rest", "hourly_follow80", "scalper_with", "scalper_fade"): self.accounts[k] = Account(k, START_CASH)
        self.lf = LadderFeed(); self.ladder = LadderArb(self.accounts["ladder"], self.lf)
        # retired after the first 88 windows (structurally losing here): old, improved, whiskas. Their accounts stay for the record.
        active = os.environ.get("PAPERBOT_ACTIVE", "all").split(",")
        if active == ["all"]: active = list(self.accounts.keys())
        all_strats = {"old": OldModel, "improved": Improved, "final": FinalMinute, "whiskas": WhiskasReplica, "lastcall": LastCall, "lastcall_tilt": LastCallTilt, "lastcall_hold": LastCallHold, "lastcall_rest": LastCallRest, "lastcall_early": LastCallEarly}
        self.strats = [all_strats[k](self.accounts[k]) for k in active if k in all_strats]
        self.strats += [Param(self.accounts[k], k, *SWEEP[k]) for k in active if k in SWEEP]
        if "follow80" in active: self.strats.append(MarketFollow(self.accounts["follow80"], "follow80"))
        if "follow80_model" in active: self.strats.append(MarketFollow(self.accounts["follow80_model"], "follow80_model", z_min=1.0))
        for k, (kind, params, label) in RANDOM_BOTS.items():
            if k not in active: continue
            if kind == "model": self.strats.append(Param(self.accounts[k], k, params["t_lo"], params["t_hi"], params["z_min"], params["max_price"], params["entry"], params["durs"], params["assets"], 0, clip=params["clip"]))
            else:
                st = MarketFollow(self.accounts[k], k, params["t_lo"], params["t_hi"], params["p_lo"], params["p_hi"], params["z_min"]); self.strats.append(st)
        if "pickoff" in active: self.strats.append(PickOff(self.accounts["pickoff"]))
        if "scalper_with" in active: self.strats.append(Scalper(self.accounts["scalper_with"], "scalper_with", fade=False))
        if "scalper_fade" in active: self.strats.append(Scalper(self.accounts["scalper_fade"], "scalper_fade", fade=True))
        if "hourly_last" in active: self.strats.append(Param(self.accounts["hourly_last"], "hourly_last", 30, 120, 2.5, 0.90, "take", (3600,), ("btc", "eth"), 0))
        if "hourly_rest" in active: self.strats.append(Param(self.accounts["hourly_rest"], "hourly_rest", 30, 120, 2.5, 0.90, "rest", (3600,), ("btc", "eth"), 0))
        if "hourly_follow80" in active: self.strats.append(MarketFollow(self.accounts["hourly_follow80"], "hourly_follow80", 60, 180, 0.75, 0.85, None, durs=(3600,)))
        self.labels = {k: v[2] for k, v in RANDOM_BOTS.items()}
        self.active = active
        self.started = time.time(); self.equity_hist = collections.deque(maxlen=20000); self.crosscheck = {}; self.lock = threading.Lock()
        self.last_state = {}; self.loop_count = 0; self.errors = 0
        self._load()

    def _load(self):
        p = os.path.join(DATA, "accounts.json")
        if os.path.exists(p):
            try:
                snap = json.load(open(p))
                for name, s in snap.get("accounts", {}).items():
                    if name not in self.accounts: continue
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
        if sp is not None: up = m.yes_wins(sp)
        elif time.time() - m.end < (45 if m.kind == "updown" else 900): return False   # ladders settle within ~15 minutes
        else:
            avg, n = self.idx.avg(m.asset, m.end - 60, m.end)
            if avg is not None and n >= 55: sp = round(avg, m.decimals); up = m.yes_wins(sp); src = "index-average"
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
                if "ladder" in self.active:
                    try:
                        self.ladder.scan(now)
                        for slug, pos in self.accounts["ladder"].positions.items():     # legs we hold must settle: adopt them into the market map
                            if not pos.closed and abs(pos.qty) > 1e-9 and slug not in self.mf.markets and slug in self.lf.legs:
                                with self.mf.lock: self.mf.markets[slug] = self.lf.legs[slug]
                    except Exception as e: self.errors += 1; log.exception("ladder: %s", e)
                for m in list(self.mf.markets.values()):
                    if m.p2b is None and now - m.start > 5 and int(now) % 5 == 0: self.mf.refresh_terms(m)
                    if now >= m.end + 2:
                        if self.settle_market(m): self.mf.retire(m.slug)
                        continue
                    if m.book_ts < now - 10: continue
                    for a in self.accounts.values(): a.on_book(m)
                    if m.kind != "updown": continue
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
                           "equity_history": hist, "settlements": settles[:80], "fills": fills[:80], "ladder": self.ladder.snapshot() if hasattr(self, "ladder") else {}, "config": {"active": self.active, "labels": self.labels, "start_cash": START_CASH, "theta": 0.095, "assets": feeds.ASSETS, "horizons": list(feeds.HORIZONS)}}

    def settle_orphans(self):
        """After a restart, positions in windows that closed while the engine was down are settled from the exchange."""
        slugs = set()
        for a in self.accounts.values():
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

    def start(self):
        for fn in (self.t_index, self.t_discover, self.t_crosscheck, self.t_http, self.spot.run):
            threading.Thread(target=fn, daemon=True).start()
        time.sleep(3)
        try: self.settle_orphans()
        except Exception as e: log.warning("orphan settlement: %s", e)
        self.loop()


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
