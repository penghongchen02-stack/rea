"""Fast20 universe (port 8091; user, 11-12 10:05Z): the 20 most profitable strategy bots of the other dashboards, $1,000
each, run by their own loop 50 times a second (cfg fast_hz) instead of the engine's one pass a second.

Picking (once, when the universe first starts; kept in <dir>/fast20.json): every strategy bot of dollar, dollar30,
dollar1000, top20, bonelab and evolve with at least MIN_WINDOWS settled windows, ranked by return on its starting cash
(equity, so after fees; the mean where the same bot runs on several dashboards). Evolved variants and the children
added after the 11-11 prune keep their settings. Scanners are left out: they poll the exchange on their own
one-second budget, and running them faster would only add requests. The other new dashboards (standard, rep1000,
tryout, champion) are not sources.

Speed: the engine's loop serves ten dashboards and takes about 1.2 s a pass. This universe's bots get their own thread
(run.Engine.t_fast): every 1 / FAST_HZ seconds it matches resting orders and runs each bot on every open Up/Down
window, with the model recomputed at that rate (fair.p_up caches per 1 / FAST_HZ here, per second elsewhere). Books
arrive by websocket, so a price change is seen within one pass. The engine still settles windows, records fills and
equity and builds the dashboard, under this universe's lock. Most bots wait 2-3 s between their own orders, so the
gain is in reacting sooner, not in ordering more often.

Sizing: the share-of-bank rule (0.1%-2% of the bank an order, 5% a window). Paper fills at the quote seen are the best
case; a real order still has to reach the exchange.
"""
import os, json, time, logging, statistics

log = logging.getLogger("fast20")
SIZE = 20; MIN_WINDOWS = 10; WARMUP = 90.0
SOURCES = ("dollar", "dollar30", "dollar1000", "top20", "bonelab", "evolve")


class Fast20:
    def __init__(self, universe, engine):
        self.u = universe; self.engine = engine; self.cfg = universe.cfg; self.cash = universe.cash
        self.path = os.path.join(universe.dir, "fast20.json")
        u = universe; u.accounts = {}; u.strats = []; u.arbs = {}; u.active = []
        u.ladder = u.ladder_model = u.touch_model = u.xarb = None
        self.members = {}; self.labels = {}; self.picked_at = None; self.ready = time.time() + WARMUP; self._b = None

    def builders(self):
        if self._b is None:
            from .strategies import build_registry
            from .bonelab import build_lab_registry
            self._b = {"main": build_registry(self.cfg.get("pos_mult", 15.0)), "lab": build_lab_registry()}
        return self._b

    def _attach(self, spec):
        from .sim import Account
        n = spec["key"]
        a = Account(n, self.cash, size_mode=self.cfg["size_mode"]).configure(self.cfg); a.universe = self.u.name
        obj = self.builders()[spec["src"]][spec["base"]](a)
        for k, v in spec.get("genes", {}).items(): setattr(obj, k, v)
        obj.name = n
        self.u.accounts[n] = a; self.u.active.append(n); self.u.strats.append(obj); self.members[n] = spec
        self.labels[n] = f"#{spec['rank']}: {spec['ret'] * 100:+.1f}% on {', '.join(spec['where'])} when picked" + (f" (as {spec['was']})" if spec.get("was") and spec["was"] != n else "")

    def restore(self):
        if not os.path.exists(self.path): return
        try:
            s = json.load(open(self.path)); self.picked_at = s.get("picked_at")
            for spec in s.get("members", []):
                try: self._attach(spec)
                except Exception as e: log.warning("[fast20] could not rebuild %s: %s", spec.get("key"), e)
            log.info("[fast20] restored %d bots", len(self.members))
        except Exception as e: log.warning("[fast20] could not restore: %s", e)

    def save(self):
        tmp = self.path + ".tmp"; json.dump({"picked_at": self.picked_at, "members": list(self.members.values())}, open(tmp, "w")); os.replace(tmp, self.path)

    def candidates(self):
        b = self.builders(); rets = {}; info = {}
        for u in self.engine.universes:
            if u.name not in SOURCES: continue
            ev = getattr(u, "evolver", None); ro = getattr(u, "roster", None)
            for name, a in u.accounts.items():
                if a.n_settled < MIN_WINDOWS: continue
                if ev is not None: sp = ev.members.get(name); key = name
                elif ro is not None and name in ro.added: sp = ro.added[name]; key = f"{name}_{u.name}"      # the prune's children differ per dashboard
                else:
                    src = "lab" if u.name == "bonelab" and name in b["lab"] else ("main" if name in b["main"] else None)
                    sp = src and {"base": name, "src": src, "genes": {}}; key = name
                if not sp or sp["src"] not in b or sp["base"] not in b[sp["src"]]: continue        # scanners and unknown bots
                if not sp.get("genes") and key != sp["base"]: key = sp["base"]                         # an unchanged copy counts as its base
                r = (a.equity(self.engine.mf.markets) - a.start_cash) / a.start_cash
                rets.setdefault(key, []).append(r)
                info.setdefault(key, {"key": key, "base": sp["base"], "src": sp["src"], "genes": dict(sp.get("genes", {})), "where": [], "was": name})["where"].append(u.name)
        out = [dict(info[k], ret=round(statistics.mean(v), 5)) for k, v in rets.items()]
        out.sort(key=lambda c: -c["ret"])
        return out

    def pick(self, now):
        top = [c for c in self.candidates() if c["ret"] > 0][:SIZE]
        for i, c in enumerate(top, 1):
            try: self._attach(dict(c, rank=i, joined=now))
            except Exception as e: log.warning("[fast20] could not add %s: %s", c["key"], e)
        self.picked_at = now; self.save()
        log.info("[fast20] picked %s", ", ".join(self.members))

    def tick(self, now):
        if not self.members and now >= self.ready:
            self.pick(now)
            if not self.members: self.ready = now + 300

    def snapshot(self, now):
        f = getattr(self.u, "fast_stats", None) or {}
        return {"hz": self.cfg.get("fast_hz"), "picked_at": self.picked_at, "ready_in": round(self.ready - now) if not self.members else None,
                "loop": {k: f.get(k) for k in ("passes_per_s", "pass_ms", "pass_ms_max", "passes")}, "ages": f.get("ages"),
                "members": {n: {k: s.get(k) for k in ("rank", "ret", "where", "base", "src", "was")} for n, s in self.members.items()}}
