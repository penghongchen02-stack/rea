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
from .ws import WSBooks
from .ladder import LadderFeed, LadderArb, LadderModel, LadderTouch, XArb
from .fair import p_up

DATA = os.environ.get("PAPERBOT_DATA", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "paperbot-data"))
WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
START_CASH = float(os.environ.get("PAPERBOT_CASH", "100"))
TOP20 = ["almach", "xarb_windows", "xarb_fast", "v_mid_rest", "xarb_tight", "v_mid_take", "lock_sweep", "lock_strikes",
         "pickoff", "lock_fast", "quarter", "v_15m_early", "lock_early", "v_strict", "xarb", "lastcall_rest", "v_early_z2",
         "v_mid_z2", "consensus_z2", "final"]
# Share-of-bank sizing for the dashboards that were losing on 11-12 09:35Z (standard, rep1000, tryout; user's choice): every
# order a random 0.1%-2% of the bot's settled bank, at most 5% of it in one market. The profitable dashboards keep theirs.
PCT = (0.001, 0.02)
UNIVERSES = [
    # Standard universe: since 11-12 01:45Z only the report replicas (rep_*), at the user's request; the standard bots
    # still run in dollar, dollar30 and dollar1000, and the old accounts are backed up beside accounts.json.
    {"name": "std", "port": int(os.environ.get("PAPERBOT_PORT", "8080")), "dir": DATA, "pos_mult": 1.0, "only_prefix": "rep_",
     "size_mode": "pct", "size_pct": PCT, "window_pct": 0.05},
    {"name": "dollar", "port": int(os.environ.get("PAPERBOT_PORT2", "8081")), "size_mode": "dollar", "dir": os.path.join(DATA, "dollar"), "pos_mult": 5.0,
     "size_min": float(os.environ.get("PAPERBOT_SIZE_MIN", "0.1")), "size_max": float(os.environ.get("PAPERBOT_SIZE_MAX", "10"))},
    {"name": "dollar30", "port": int(os.environ.get("PAPERBOT_PORT3", "8082")), "size_mode": "dollar", "dir": os.path.join(DATA, "dollar30"), "pos_mult": 15.0,
     "size_min": 0.1, "size_max": 30.0, "cash": float(os.environ.get("PAPERBOT_CASH3", "1000"))},
    # every order a random $0.10-$1,000; $30,000 per bot so one maximum order is about 3% of the account,
    # the same proportion as the $30 / $1,000 universe
    {"name": "dollar1000", "port": int(os.environ.get("PAPERBOT_PORT4", "8083")), "size_mode": "dollar", "dir": os.path.join(DATA, "dollar1000"), "pos_mult": 500.0,
     "size_min": 0.1, "size_max": 1000.0, "cash": float(os.environ.get("PAPERBOT_CASH4", "30000"))},
    # Bonereaper lab: only the 50 Bonereaper variants (paperbot/bonelab.py), $1,000 each; each variant sizes its own orders
    {"name": "bonelab", "port": int(os.environ.get("PAPERBOT_PORT5", "8084")), "size_mode": "contracts", "dir": os.path.join(DATA, "bonelab"),
     "cash": 1000.0, "registry": "bonelab"},
    # Top 20: the 20 best non-Bonereaper bots of run 4 (mean return across the four universes at 11:20Z on 11-10),
    # $5,000 each; every order a random $1-$150 (3% of the account at most, the dollar30/dollar1000 proportion)
    {"name": "top20", "port": int(os.environ.get("PAPERBOT_PORT6", "8085")), "size_mode": "dollar", "dir": os.path.join(DATA, "top20"),
     "pos_mult": 75.0, "size_min": 1.0, "size_max": 150.0, "cash": 5000.0, "only": TOP20},
    # Evolution: the best 20 bots across every other dashboard, $1,000 each, every order a random $0.10-$50 (user's
    # choice, 11-11 09:10Z; was $200; position caps scaled like the top20 universe; Bonereaper-lab variants keep their own
    # sizing; every bot that has a coin list trades all four coins, user's request 11-11 10:30Z). Every hour the bottom 5 are replaced by 3 children of the top 5 and 2 immigrants (paperbot/evolve.py).
    {"name": "evolve", "port": int(os.environ.get("PAPERBOT_PORT7", "8086")), "size_mode": "dollar", "dir": os.path.join(DATA, "evolve"),
     "pos_mult": 100.0, "size_min": 0.1, "size_max": 50.0, "cash": 1000.0, "registry": "evolve", "coins": ["btc", "eth", "sol", "xrp"]},
    # Champion: one $1,000 bank, BTC/ETH/SOL/XRP 5-minute windows only (user, 11-11 17:20Z), trading as the best-scoring bot of every other
    # dashboard; reviewed hourly and replaced while it is losing (paperbot/champion.py). Orders $1-$30 (3% of the
    # bank at most, the dollar30 proportion).
    {"name": "champion", "port": int(os.environ.get("PAPERBOT_PORT8", "8087")), "size_mode": "contracts", "dir": os.path.join(DATA, "champion"),
     "pos_mult": 15.0, "cash": 1000.0, "registry": "champion", "assets": ["btc", "eth", "sol", "xrp"], "horizons": ["5m"]},
    # Tryout: the champion's candidates side by side under the champion's exact rules, $1,000 each; the champion hires
    # only bots that pass here (paperbot/tryout.py; user, 11-11 18:10Z).
    # Every bot sizes its own orders (no $1-$30 range) and the report replicas trade their own coins and windows, so the
    # universe passes 5- and 15-minute markets; pool bots are held to 5-minute windows (user, 11-11 22:40Z).
    {"name": "tryout", "port": int(os.environ.get("PAPERBOT_PORT9", "8088")), "size_mode": "pct", "size_pct": PCT, "window_pct": 0.05, "dir": os.path.join(DATA, "tryout"),
     "pos_mult": 15.0, "cash": 1000.0, "registry": "tryout", "assets": ["btc", "eth", "sol", "xrp"], "horizons": ["5m", "15m"], "pool_horizons": ["5m"]},
    # Rep1000: the nine report replicas alone with $1,000 each (user, 11-12 01:55Z), beside the $100 ones in std.
    # br133 on its own (user, 11-12 18:50Z): the first bot to pass the tryout, run as it ran there (4 coins, 5-minute
    # windows, share-of-bank sizing) on a fresh $1,000 account. It was in profit, so it runs without the direction guards.
    {"name": "br133", "port": int(os.environ.get("PAPERBOT_PORT12", "8092")), "size_mode": "pct", "size_pct": PCT, "window_pct": 0.05,
     "dir": os.path.join(DATA, "br133"), "cash": 1000.0, "registry": "single", "bot": "br133", "assets": ["btc", "eth", "sol", "xrp"], "horizons": ["5m"],
     "note": "passed the tryout 11-12 (53 windows, +$59); here since 11-12 18:50Z on a fresh $1,000"},
    # br133, fast and doubled (user, 11-12 18:52Z): the same bot on Bitcoin 5-minute windows only, on the 50-passes-a-second
    # loop (Engine.t_fast), with twice the tryout's stakes: 0.2%-4% of the bank an order, 10% a window.
    {"name": "br133fast", "port": int(os.environ.get("PAPERBOT_PORT13", "8093")), "size_mode": "pct", "size_pct": (PCT[0] * 2, PCT[1] * 2), "window_pct": 0.10,
     "dir": os.path.join(DATA, "br133fast"), "cash": 1000.0, "registry": "single", "bot": "br133", "assets": ["btc"], "horizons": ["5m"], "fast_hz": 50,
     "note": "Bitcoin only, fast loop, double the tryout's stakes; here since 11-12 18:53Z on a fresh $1,000"},
    # Fast20: the 20 most profitable strategy bots, $1,000 each, on their own 20-passes-a-second loop (paperbot/fast20.py).
    {"name": "fast20", "port": int(os.environ.get("PAPERBOT_PORT11", "8091")), "size_mode": "pct", "size_pct": PCT, "window_pct": 0.05,
     "dir": os.path.join(DATA, "fast20"), "pos_mult": 15.0, "cash": 1000.0, "registry": "fast20", "fast_hz": 50},
    {"name": "rep1000", "port": int(os.environ.get("PAPERBOT_PORT10", "8089")), "size_mode": "pct", "size_pct": PCT, "window_pct": 0.05, "dir": os.path.join(DATA, "rep1000"),
     "cash": 1000.0, "registry": "reports"},
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
        from . import bonelab as _bl; _bl.GUARD_DIRS[self.name] = self.dir
        self.cash = cfg.get("cash", START_CASH)
        mk = lambda k: Account(k, self.cash, size_mode=cfg["size_mode"], size_min=cfg.get("size_min", 0.1), size_max=cfg.get("size_max", 10.0)).configure(cfg)
        self.lab = cfg.get("registry") == "bonelab"; self.evolver = None; self.champion = None; self.roster = None; self.tryout = None
        self.fast20 = None; self.fast = bool(cfg.get("fast_hz")); self.lock = threading.RLock(); self.fast_stats = {}
        if cfg.get("registry") == "fast20":     # the picked bots, run by Engine.t_fast (paperbot/fast20.py)
            from .fast20 import Fast20
            self.fast20 = Fast20(self, engine); self.fast20.restore()
        elif cfg.get("registry") == "tryout":     # candidates added and dropped at run time (paperbot/tryout.py)
            from .tryout import Tryout
            self.tryout = Tryout(self, engine); self.tryout.restore()
        elif cfg.get("registry") == "champion":   # one account; its strategy is hired and replaced by the Champion
            from .champion import Champion
            self.champion = Champion(self, engine)
        elif cfg.get("registry") == "evolve":     # members are created and replaced at run time by the Evolver
            from .evolve import Evolver
            self.accounts = {}; self.strats = []; self.arbs = {}; self.active = []
            self.ladder = self.ladder_model = self.touch_model = self.xarb = None
            self.evolver = Evolver(self, engine); self.evolver.restore()
        elif cfg.get("registry") == "single":       # one lab bot on its own dashboard (cfg "bot")
            from .bonelab import build_lab_registry
            reg = {cfg["bot"]: build_lab_registry()[cfg["bot"]]}; self.accounts = {k: mk(k) for k in reg}
        elif self.lab:
            from .bonelab import build_lab_registry
            reg = build_lab_registry(); self.accounts = {k: mk(k) for k in reg}
        elif cfg.get("registry") == "reports":      # only the report replicas (paperbot/reports.py)
            from .reports import build_report_registry
            reg = build_report_registry(); self.accounts = {k: mk(k) for k in reg}
        else:
            reg = build_registry(cfg.get("pos_mult", 1.0))
            self.accounts = {k: mk(k) for k in list(reg) + list(LADDER_BOTS) + list(ARB_BOTS)}
        for a in self.accounts.values(): a.universe = self.name
        managed = self.evolver is not None or self.champion is not None or self.tryout is not None or self.fast20 is not None   # build their own bots
        if not managed:
            active = os.environ.get("PAPERBOT_ACTIVE", "all").split(",")
            self.active = list(self.accounts) if active == ["all"] else active
            self.strats = [reg[k](self.accounts[k]) for k in self.active if k in reg]
        if managed: pass
        elif self.lab or cfg.get("registry") in ("reports", "single"):
            if cfg.get("registry") == "single":       # the coins and windows it passed the tryout on
                for s in self.strats:
                    s.assets = set(cfg["assets"]); s.durs = {300}
                    if cfg.get("fast_hz"): s.period = 1.0 / cfg["fast_hz"]      # look for an entry on every fast pass, not every 3 s
            self.ladder = self.ladder_model = self.touch_model = self.xarb = None; self.arbs = {}
        else:
            self.ladder = LadderArb(self.accounts["ladder"], engine.lf); self.ladder_model = LadderModel(self.accounts["ladder_model"], engine.lf, engine.idx, engine.spot)
            self.touch_model = LadderTouch(self.accounts["touch_model"], engine.lf, engine.idx, engine.spot)
            self.xarb = XArb(self.accounts["xarb"], engine.mf, engine.lf)
            self.arbs = make_arbs(self.accounts, engine)
        only = cfg.get("only")
        if only:     # a hand-picked universe: keep only the listed bots (scanners are built above, then dropped)
            self.accounts = {k: a for k, a in self.accounts.items() if k in only}
            self.arbs = {k: v for k, v in self.arbs.items() if k in only}
            for attr in ("ladder", "ladder_model", "touch_model", "xarb"):
                if attr not in only: setattr(self, attr, None)
            self.active = [k for k in self.active if k in only]
            self.strats = [st for st in self.strats if st.acct.name in only]
        self.equity_hist = collections.deque(maxlen=20000); self.last_state = {}; self.started = time.time()
        self.labels = {}
        try:
            from .strategies import RANDOM_BOTS; self.labels = {k: v[2] for k, v in RANDOM_BOTS.items()}
        except Exception: pass
        if self.lab:
            from .bonelab import LAB_LABELS; self.labels = dict(LAB_LABELS)
        if cfg.get("registry") == "reports":
            self.labels = {k: "PR&R report replica, filtered: leading side only, 5% of bank per window, stops for the day at -10%" for k in self.accounts}
        if cfg.get("registry") == "single":
            from .bonelab import LAB_LABELS; self.labels = {k: LAB_LABELS.get(k, k) + "; " + cfg.get("note", "") for k in self.accounts}
        if self.evolver is not None: self.labels = self.evolver.labels
        if self.champion is not None: self.labels = self.champion.labels; self.champion.restore()
        if self.tryout is not None: self.labels = self.tryout.labels
        if self.fast20 is not None: self.labels = self.fast20.labels
        if not managed:     # deleted and added bots (paperbot/roster.py)
            from .roster import Roster
            self.roster = Roster(self, engine); self.roster.apply()
        pre = cfg.get("only_prefix")
        if pre:      # keep only bots whose name starts with this (scanners are built above, then dropped)
            self.accounts = {k: a for k, a in self.accounts.items() if k.startswith(pre)}
            self.arbs = {k: v for k, v in self.arbs.items() if k in self.accounts}
            for attr in ("ladder", "ladder_model", "touch_model", "xarb"): setattr(self, attr, None)
            self.active = [k for k in self.active if k in self.accounts]
            self.strats = [st for st in self.strats if st.acct.name in self.accounts]
        self._load()

    def _load(self):
        p = os.path.join(self.dir, "accounts.json")
        if os.path.exists(p):
            try:
                snap = json.load(open(p))
                for name, s in snap.get("accounts", {}).items():
                    if name not in self.accounts: continue
                    a = self.accounts[name]; a.cash = s["cash"]; a.fees = s["fees"]; a.maker_fills = s["maker_fills"]; a.taker_fills = s["taker_fills"]; a.start_cash = s.get("start_cash", self.cash); a.est_rewards = s.get("est_rewards", 0.0)
                    for slug, ps in s.get("positions", {}).items():
                        pos = a.positions[slug]; pos.qty = ps["qty"]; pos.avg = ps["avg"]; pos.realized = ps["realized"]; pos.fees = ps["fees"]; pos.fills = ps["fills"]; pos.closed = ps["closed"]
                    a.settlements = s.get("settlements", [])
                    if "n_settled" in s: a.n_settled = s["n_settled"]; a.n_won = s["n_won"]; a.realized_closed = s["realized_closed"]
                    else:   # older snapshot: rebuild the totals from the closed positions, then drop them
                        cl = [p for p in a.positions.values() if p.closed]
                        a.n_settled = sum(1 for p in cl if p.fills); a.n_won = sum(1 for p in cl if p.realized > 0); a.realized_closed = sum(p.realized for p in cl)
                    for slug in [k for k, p in a.positions.items() if p.closed]: del a.positions[slug]
                self.started = snap.get("started", self.started)
                for row in self.store.query("select ts, bot, equity from equity order by ts"): self.equity_hist.append(row)
                log.info("[%s] resumed state from %s", self.name, p)
            except Exception as e: log.warning("[%s] could not resume: %s", self.name, e)

    def save(self):
        snap = {"started": self.started, "accounts": {}}
        for name, a in self.accounts.items():
            snap["accounts"][name] = {"cash": a.cash, "fees": a.fees, "maker_fills": a.maker_fills, "taker_fills": a.taker_fills, "start_cash": a.start_cash, "est_rewards": a.est_rewards,
                "positions": {s: {"qty": p.qty, "avg": p.avg, "realized": p.realized, "fees": p.fees, "fills": p.fills, "closed": p.closed} for s, p in a.positions.items() if not p.closed},
                "n_settled": a.n_settled, "n_won": a.n_won, "realized_closed": a.realized_closed,
                "settlements": a.settlements[-500:]}
        tmp = os.path.join(self.dir, "accounts.json.tmp"); json.dump(snap, open(tmp, "w")); os.replace(tmp, os.path.join(self.dir, "accounts.json"))

    def build_state(self, engine, now):
        mk = {}
        for m in list(engine.mf.markets.values()):      # the discovery thread may add a market mid-pass
            s = m.snapshot(); p, det = p_up(m, engine.idx, engine.spot, now); s["p_up"] = round(p, 3) if p is not None else None; s["model"] = det
            s["remaining"] = round(m.remaining(now)); s["positions"] = {}; s["orders"] = {}
            for name, a in self.accounts.items():
                pos = a.positions.get(m.slug); s["positions"][name] = round(pos.qty, 2) if pos and not pos.closed else 0.0
                s["orders"][name] = [{"side": o.side, "px": o.price, "qty": round(o.remaining, 2), "tag": o.tag} for o in a.open_orders(m.slug)]
            mk[m.slug] = s
        idx = {a: dict(zip(("ts", "value"), engine.idx.last(a))) for a in feeds.ASSETS}
        spot = {a: dict(zip(("value", "ts"), engine.spot.last(a))) for a in feeds.ASSETS}
        hist = {}
        for ts, name, eq in self.equity_hist:
            if name in self.accounts: hist.setdefault(name, []).append([round(ts), round(eq, 3)])
        for name in hist:
            if len(hist[name]) > 1500: step = len(hist[name]) // 1500 + 1; hist[name] = hist[name][::step] + hist[name][-1:]
        settles = []; fills = []
        for a in self.accounts.values(): settles += a.settlements[-60:]; fills += list(a.events)[:60]
        settles.sort(key=lambda r: -r["ts"]); fills.sort(key=lambda r: -r["ts"])
        self.last_state = {"ts": now, "started": self.started, "uptime": round(now - engine.started), "loop": engine.loop_count, "errors": engine.errors,
            "accounts": {n: a.snapshot(engine.mf.markets) for n, a in self.accounts.items()}, "markets": mk, "index": idx, "spot": spot,
            "feeds": {"index_age": round(now - engine.idx.last_ok, 1) if engine.idx.last_ok else None, "coinbase_connected": engine.spot.connected, "coinbase_msgs": engine.spot.msgs,
                      "books_polled": len(engine.mf.markets), "brti_crosscheck": engine.crosscheck, "stream": engine.ws.status()},
            "equity_history": hist, "settlements": settles[:80], "fills": fills[:80], "ladder": self.ladder.snapshot() if self.ladder else {}, "ladder_model": self.ladder_model.snapshot() if self.ladder_model else {}, "touch_model": self.touch_model.snapshot() if self.touch_model else {}, "xarb": self.xarb.snapshot() if self.xarb else {}, "arbs": {k: v.snapshot() for k, v in self.arbs.items()},
            "config": {"universe": self.name, "size_mode": self.cfg["size_mode"], "size_range": [self.cfg.get("size_min"), self.cfg.get("size_max")], "size_pct": self.cfg.get("size_pct"), "window_pct": self.cfg.get("window_pct"), "active": self.active, "labels": self.labels,
                       "start_cash": self.cash, "theta": 0.095, "assets": feeds.ASSETS, "horizons": list(feeds.HORIZONS)}}
        if self.evolver is not None: self.last_state["evolution"] = self.evolver.snapshot(now)
        if self.champion is not None: self.last_state["champion"] = self.champion.snapshot(now)
        if self.roster is not None: self.last_state["roster"] = self.roster.snapshot()
        if self.tryout is not None: self.last_state["tryout"] = self.tryout.snapshot(now)
        if self.fast20 is not None: self.last_state["fast20"] = self.fast20.snapshot(now)
        elif self.fast:      # a fast dashboard without the Fast 20 picker: the loop's speed and input ages only
            f = self.fast_stats; self.last_state["fastloop"] = {"hz": self.cfg.get("fast_hz"), "ages": f.get("ages"),
                                                                "loop": {k: f.get(k) for k in ("passes_per_s", "pass_ms", "pass_ms_max", "passes")}}


class Engine:
    def __init__(self):
        self.mf = feeds.MarketFeed(); self.idx = feeds.IndexFeed(); self.spot = feeds.SpotFeed(); self.lf = LadderFeed(); self.ws = WSBooks(self.mf)
        self.started = time.time(); self.crosscheck = {}; self.loop_count = 0; self.errors = 0
        self.universes = [Universe(cfg, self) for cfg in UNIVERSES]
        self.index_store = self.universes[0].store

    def all_accounts(self):
        for u in self.universes:
            for a in u.accounts.values(): yield u, a

    # ---- threads -----------------------------------------------------------
    def t_index(self):
        last_log = 0; last_full = 0.0
        while True:
            try:
                # the benchmark prints once a second; ask twice a second for the last few seconds (one request for all
                # coins), and for the full 3 minutes every 30 s to fill any gap
                if time.time() - last_full > 30: self.idx.poll(); last_full = time.time()
                else: self.idx.poll(lookback=8)
                if time.time() - last_log > 10:
                    for a in feeds.ASSETS:
                        ts, v = self.idx.last(a)
                        if ts: self.index_store.insert("index_log", {"ts": ts, "asset": a, "value": v})
                    last_log = time.time()
            except Exception as e: log.warning("index poll: %s", e)
            time.sleep(0.5)

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
            v = self.idx.value_at(m.asset, m.end - 1)      # the series is the rolling 60-second average; its value at T-1 is the settlement
            if v is not None: sp = round(v, m.decimals); up = m.yes_wins(sp); src = "index-series"
        if up is None:
            if time.time() - m.end < 240: return False
            log.warning("could not settle %s; using last price", m.slug); up = (m.last_px or 0.5) >= 0.5
        for u in self.universes:
            with u.lock:
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
            if m.end < time.time() + 2 and self.settle_market(m): log.info("settled orphan %s after restart", slug); continue
            # not settleable yet (the exchange publishes within about a minute): track it so the loop keeps retrying
            with self.mf.lock: self.mf.markets[slug] = m

    # ---- main loop -----------------------------------------------------------
    def loop(self):
        ctx = {"index": self.idx, "spot": self.spot}; last_save = 0; last_eq = 0
        while True:
            t0 = time.time(); self.loop_count += 1
            try:
                self.mf.poll_books(); now = time.time()
                for u in self.universes:
                    if u.fast: continue          # no scanners there; its positions are all Up/Down windows
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
                    for u, a in self.all_accounts(): 
                        if not u.fast: a.on_book(m)
                    if m.kind != "updown": continue
                    for u in self.universes:
                        if u.fast: continue        # run by t_fast
                        if "assets" in u.cfg and (m.asset not in u.cfg["assets"] or m.horizon not in u.cfg["horizons"]): continue
                        for s in u.strats:
                            try: s.on_tick(m, ctx, now)
                            except Exception as e: self.errors += 1; log.exception("[%s] strategy %s on %s: %s", u.name, s.name, m.slug, e)
                    for u, a in self.all_accounts():
                        if not u.fast: a.on_book(m)
                for u in self.universes:
                    with u.lock:
                        for a in u.accounts.values():
                            while a.fills:
                                f = a.fills.pop(0); u.store.insert("fills", f); a.events.appendleft(f)
                for u in self.universes:
                    if u.evolver is not None:
                        try: u.evolver.tick(now)
                        except Exception as e: self.errors += 1; log.exception("[%s] evolve: %s", u.name, e)
                    if u.fast20 is not None:
                        try:
                            with u.lock: u.fast20.tick(now)
                        except Exception as e: self.errors += 1; log.exception("[%s] fast20: %s", u.name, e)
                    if u.tryout is not None:
                        try: u.tryout.tick(now)
                        except Exception as e: self.errors += 1; log.exception("[%s] tryout: %s", u.name, e)
                    if u.roster is not None:
                        try: u.roster.tick(now)
                        except Exception as e: self.errors += 1; log.exception("[%s] roster: %s", u.name, e)
                    if u.champion is not None:
                        try: u.champion.tick(now)
                        except Exception as e: self.errors += 1; log.exception("[%s] champion: %s", u.name, e)
                if now - last_eq >= 5:
                    for u in self.universes:
                        with u.lock:
                            for a in u.accounts.values():
                                eq = a.equity(self.mf.markets); u.equity_hist.append((now, a.name, eq))
                                u.store.insert("equity", {"ts": now, "bot": a.name, "equity": eq, "cash": a.cash, "bp": a.buying_power(), "fees": a.fees})
                    last_eq = now
                for u in self.universes:
                    with u.lock: u.build_state(self, now)
                if now - last_save > 10:
                    for u in self.universes:
                        with u.lock: u.save()
                    last_save = now
            except Exception as e:
                self.errors += 1; log.exception("loop: %s", e)
            time.sleep(max(0.0, 1.0 - (time.time() - t0)))

    def t_fast(self, u):
        """The fast loop of one universe (cfg fast_hz passes a second): match resting orders and run its bots on every
        open Up/Down window. Holds the universe's lock for the pass; the engine loop takes the same lock to settle,
        record and display. Books come from the websocket; nothing is polled here."""
        from . import fair
        hz = float(u.cfg["fast_hz"]); period = 1.0 / hz; fair.set_resolution(hz)
        ctx = {"index": self.idx, "spot": self.spot}; st = u.fast_stats; st.update(passes=0, pass_ms=0.0, pass_ms_max=0.0, passes_per_s=0.0)
        win_t = time.time(); win_n = 0; last_err = 0.0; last_age = 0.0
        while True:
            t0 = time.perf_counter(); now = time.time()
            try:
                if now - last_age >= 1:      # how old each input is when the bots read it (shown on the dashboard)
                    last_age = now
                    bk = sorted(now - m.book_ts for m in list(self.mf.markets.values()) if m.kind == "updown" and m.start <= now < m.end)
                    sp = [now - t for a in feeds.ASSETS for t in [self.spot.last(a)[1]] if t]
                    ix = [now - t for a in feeds.ASSETS for t in [self.idx.last(a)[0]] if t]
                    st["ages"] = {"book_ms": round(bk[len(bk) // 2] * 1000) if bk else None, "book_ws": sum(1 for m in self.mf.markets.values() if m.kind == "updown" and m.start <= now < m.end and m.book_src == "ws"), "books": len(bk),
                                  "spot_ms": round(min(sp) * 1000) if sp else None, "spot_ms_max": round(max(sp) * 1000) if sp else None,
                                  "index_ms": round(sum(ix) / len(ix) * 1000) if ix else None}
                with u.lock:
                    for m in list(self.mf.markets.values()):
                        if now >= m.end or m.book_ts < now - 10: continue
                        for a in u.accounts.values(): a.on_book(m)
                        if m.kind != "updown": continue
                        for s in u.strats:
                            try: s.on_tick(m, ctx, now)
                            except Exception as e:
                                self.errors += 1
                                if now - last_err > 5: last_err = now; log.exception("[%s] strategy %s on %s: %s", u.name, s.name, m.slug, e)
                        for a in u.accounts.values(): a.on_book(m)
            except Exception as e:
                self.errors += 1
                if now - last_err > 5: last_err = now; log.exception("[%s] fast loop: %s", u.name, e)
            ms = (time.perf_counter() - t0) * 1000.0
            st["passes"] += 1; win_n += 1; st["pass_ms"] = round(0.95 * st["pass_ms"] + 0.05 * ms, 2) if st["passes"] > 1 else round(ms, 2)
            st["pass_ms_max"] = round(max(st["pass_ms_max"] * 0.999, ms), 2)
            if now - win_t >= 5: st["passes_per_s"] = round(win_n / (now - win_t), 1); win_t = now; win_n = 0
            time.sleep(max(0.001, period - (time.perf_counter() - t0)))

    def summary(self):
        """One compact JSON for the phone dashboard (web/mobile.html on PAPERBOT_PORT_MOBILE), for one universe
        (PAPERBOT_MOBILE_UNIVERSE, default the standard one; user, 11-12 00:05Z): key and feed health, the live 5-minute
        window of each coin, the universe's totals, every bot, its roster changes and its latest settlements and fills.
        Built from the state the loop already holds; no key values, only whether the signed stream is connected."""
        now = time.time(); name = os.environ.get("PAPERBOT_MOBILE_UNIVERSE", "std")
        u = next((x for x in self.universes if x.name == name), self.universes[0]); st = u.last_state or {}
        feeds = st.get("feeds", {}); stream = feeds.get("stream") or {}
        live = {}
        for slug, m in (st.get("markets") or {}).items():
            if m.get("horizon") != "5m" or m.get("remaining", 0) <= 0: continue
            cur = live.get(m["asset"])
            if cur is None or m["remaining"] < cur["remaining"]:
                live[m["asset"]] = {k: m.get(k) for k in ("slug", "asset", "start", "end", "p2b", "bid", "ask", "last", "p_up", "remaining")}
        for a_, m in live.items(): m["index"] = (st.get("index") or {}).get(a_, {}).get("value"); m["spot"] = (st.get("spot") or {}).get(a_, {}).get("value")
        labels = (st.get("config") or {}).get("labels") or {}
        bots = sorted(({"name": a["name"], "pnl": round(a.get("pnl", 0), 4), "equity": a.get("equity"), "settled": a.get("markets_settled"), "won": a.get("markets_won"),
                        "fees": a.get("fees"), "fills": (a.get("maker_fills") or 0) + (a.get("taker_fills") or 0), "est_rewards": a.get("est_rewards") or 0, "open": len(a.get("open_positions") or {}),
                        "label": labels.get(a["name"])} for a in (st.get("accounts") or {}).values()), key=lambda b: -b["pnl"])
        ro = st.get("roster") or {}
        return {"ts": now, "uptime": round(now - self.started), "errors": self.errors, "loop": self.loop_count,
                "universe": {"name": u.name, "port": u.port, "start_cash": u.cash, "size_mode": u.cfg.get("size_mode"), "started": st.get("started")},
                "key": {"set": bool(os.environ.get("POLYMARKET_KEY_ID")) and bool(os.environ.get("POLYMARKET_SECRET_KEY")),
                        "accepted": bool(stream.get("connected")) and stream.get("age") is not None and stream["age"] < 30},
                "stream": stream, "feeds": {k: feeds.get(k) for k in ("index_age", "coinbase_connected", "books_polled")}, "crosscheck": feeds.get("brti_crosscheck"),
                "live": live, "bots": bots, "net": round(sum(b["pnl"] for b in bots), 2),
                "roster": {"removed": ro.get("removed", 0), "added": ro.get("added") or {}},
                "settlements": [{k: r.get(k) for k in ("ts", "bot", "asset", "horizon", "up", "pnl_total", "fills")} for r in (st.get("settlements") or [])[:25]],
                "fills": [{k: f.get(k) for k in ("ts", "bot", "slug", "side", "price", "qty", "liq", "tag")} for f in (st.get("fills") or [])[:25]]}

    def t_mobile(self):
        eng = self
        class H(http.server.SimpleHTTPRequestHandler):
            def __init__(s, *a, **kw): super().__init__(*a, directory=WEB, **kw)
            def log_message(s, *a): pass
            def do_GET(s):
                if s.path.startswith("/summary.json"):
                    body = json.dumps(eng.summary()).encode(); s.send_response(200); s.send_header("Content-Type", "application/json")
                    s.send_header("Cache-Control", "no-store"); s.send_header("Content-Length", str(len(body))); s.end_headers(); s.wfile.write(body); return
                if s.path == "/" or s.path.startswith("/?"): s.path = "/mobile.html"
                super().do_GET()
        port = int(os.environ.get("PAPERBOT_PORT_MOBILE", "8090"))
        srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), H); log.info("phone dashboard on port %d", port); srv.serve_forever()

    def start(self):
        for fn in (self.t_index, self.t_discover, self.t_crosscheck, self.spot.run, self.ws.run, self.t_mobile):
            threading.Thread(target=fn, daemon=True).start()
        for u in self.universes: threading.Thread(target=self.t_http, args=(u,), daemon=True).start()
        for u in self.universes:
            if u.fast: threading.Thread(target=self.t_fast, args=(u,), daemon=True).start()
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
