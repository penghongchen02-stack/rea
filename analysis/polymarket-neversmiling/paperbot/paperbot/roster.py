"""Roster changes for the fixed dashboards (std, dollar, dollar30, dollar1000, bonelab, top20): bots deleted at the
user's request and the new bots added in their place. The evolution and champion universes manage their own bots.

Prune (requested by writing <dir>/prune.json {"per": 5}; consumed once): every bot whose P/L is below zero (equity,
open positions at mark included, minus its starting cash) is deleted: open orders cancelled, positions dropped, and it
is no longer built after a restart. Then ceil(deleted / per) new bots are added, each a child of one of the
dashboard's 5 most profitable bots (picked 5:4:3:2:1 by rank) with 1-3 settings changed, exactly as the evolution
universe makes children (evolve.mutate_genes), on a fresh account with the dashboard's starting cash. Bots that never
traded (P/L exactly zero) are kept.

State: <dir>/roster.json {"removed": [names], "added": [specs], "counter", "prunes": [summaries]}. Settled history of
deleted bots stays in paper.db.
"""
import os, json, math, time, random, logging

log = logging.getLogger("roster")
SCANNER_ATTRS = ("ladder", "ladder_model", "touch_model", "xarb")


class Roster:
    def __init__(self, universe, engine):
        self.u = universe; self.engine = engine; self.cfg = universe.cfg
        self.path = os.path.join(universe.dir, "roster.json"); self.rng = random.Random()
        self.removed = []; self.added = {}; self.counter = 0; self.prunes = []; self._builders = None; self._used = None
        if os.path.exists(self.path):
            try:
                s = json.load(open(self.path))
                self.removed = s.get("removed", []); self.counter = s.get("counter", 0); self.prunes = s.get("prunes", [])
                self.added = {sp["name"]: sp for sp in s.get("added", [])}
            except Exception as e: log.warning("[%s] could not read roster: %s", universe.name, e)

    def save(self):
        s = {"removed": self.removed, "added": list(self.added.values()), "counter": self.counter, "prunes": self.prunes[-50:]}
        tmp = self.path + ".tmp"; json.dump(s, open(tmp, "w")); os.replace(tmp, self.path)

    # ---- construction --------------------------------------------------------
    def builders(self):
        if self._builders is None:
            from .strategies import build_registry
            from .bonelab import build_lab_registry
            from .arbs import scanner_builders
            from .reports import build_report_registry
            self._builders = {"main": build_registry(self.cfg.get("pos_mult", 1.0)), "lab": build_lab_registry(),
                              "scan": scanner_builders(self.engine), "rep": build_report_registry()}
        return self._builders

    def spec_of(self, name):
        """How to rebuild a bot of this dashboard: {name, base, src, genes}; None if it cannot be rebuilt."""
        if name in self.added: return self.added[name]
        b = self.builders()
        src = ("lab" if self.u.lab and name in b["lab"] else "scan" if name in b["scan"] else "main" if name in b["main"] else "rep" if name in b["rep"] else None)
        return src and {"name": name, "base": name, "src": src, "genes": {}}

    def _build(self, spec, account):
        obj = self.builders()[spec["src"]][spec["base"]](account)
        for k, v in spec.get("genes", {}).items(): setattr(obj, k, v)
        return obj

    def apply(self):
        """At start-up, after the universe has built its standard bots: drop deleted ones, rebuild added ones."""
        for n in self.removed: self._remove(n)
        for spec in list(self.added.values()):
            try: self._attach(spec)
            except Exception as e: log.warning("[%s] could not rebuild %s: %s", self.u.name, spec["name"], e)

    def _remove(self, name):
        u = self.u; a = u.accounts.pop(name, None)
        if a: a.cancel_all()
        u.arbs.pop(name, None); u.strats = [s for s in u.strats if s.acct.name != name]
        if name in u.active: u.active.remove(name)
        if name in SCANNER_ATTRS: setattr(u, name, None)
        u.labels.pop(name, None); self.added.pop(name, None)
        return a

    def _attach(self, spec, record=True):
        from .sim import Account
        u = self.u; c = self.cfg
        a = Account(spec["name"], u.cash, size_mode=c["size_mode"], size_min=c.get("size_min", 0.1), size_max=c.get("size_max", 10.0))
        a.universe = u.name; obj = self._build(spec, a)
        if spec["src"] == "scan": u.arbs[spec["name"]] = obj
        else: u.strats.append(obj)
        u.accounts[spec["name"]] = a; u.active.append(spec["name"])
        if record: self.added[spec["name"]] = spec
        ch = "; ".join(f"{g} {o:g}->{n:g}" for g, o, n in spec.get("muts", []))
        u.labels[spec["name"]] = spec.get("note") or f"new, child of {spec['parent']} ({ch})"

    def child(self, parent, now):
        from .evolve import genes, mutate_genes, _used_constants
        from .sim import Account
        p = self.spec_of(parent)
        if p is None: return None
        if self._used is None: self._used = _used_constants()
        gs = genes(self._build(p, Account("_probe", self.u.cash, size_mode=self.cfg["size_mode"])), self._used)
        if not gs: return None
        g2, muts = mutate_genes(self.rng, gs, p.get("genes", {}))
        if not muts: return None
        self.counter += 1
        return {"name": f"{p['base']}_n{self.counter}", "base": p["base"], "src": p["src"], "genes": g2, "parent": parent, "born": now, "muts": muts}

    # ---- pruning -----------------------------------------------------------------
    def pnl(self, name):
        a = self.u.accounts[name]; return a.equity(self.engine.mf.markets) - a.start_cash

    def prune(self, now, per=5):
        pnl = {n: self.pnl(n) for n in self.u.accounts}
        doomed = sorted((n for n, p in pnl.items() if p < 0), key=pnl.get)
        top = [n for n in sorted(pnl, key=pnl.get, reverse=True) if pnl[n] > 0 and self.spec_of(n)][:5]
        for n in doomed:
            self._remove(n)
            if n not in self.removed: self.removed.append(n)
        want = math.ceil(len(doomed) / per) if doomed else 0; born = []; tries = 0
        while top and len(born) < want and tries < 50:
            tries += 1
            try: spec = self.child(self.rng.choices(top, list(range(len(top), 0, -1)))[0], now)
            except Exception as e: log.warning("[%s] child failed: %s", self.u.name, e); spec = None
            if spec: self._attach(spec); born.append(spec)
        rec = {"ts": now, "removed": [[n, round(pnl[n], 2)] for n in doomed], "added": [[s["name"], s["parent"], s["muts"]] for s in born],
               "parents": [[n, round(pnl[n], 2)] for n in top]}
        self.prunes.append(rec); self.save()
        log.info("[%s] pruned %d losing bots; added %s", self.u.name, len(doomed), [s["name"] for s in born])
        return rec

    def request(self, now):
        """<dir>/roster_req.json (consumed once): {"add": [names], "restore": [names]}. add: a bot of any registry not
        in this dashboard (the report replicas), on a fresh account, kept across restarts. restore: a deleted standard
        bot comes back on a fresh account (its old losses stay in the prune record)."""
        p = os.path.join(self.u.dir, "roster_req.json")
        if not os.path.exists(p): return
        try: req = json.load(open(p))
        except Exception as e: log.warning("[%s] bad roster_req.json: %s", self.u.name, e); req = {}
        os.remove(p); done = []
        for n in req.get("restore", []):
            if n in self.u.accounts or n not in self.removed: continue
            spec = self.spec_of(n)
            if spec is None: continue
            self.removed.remove(n); self._attach(dict(spec, note=f"restored {time.strftime('%m-%d %H:%MZ', time.gmtime(now))} on a fresh account"), record=False); done.append(n)
        for n in req.get("add", []):
            spec = self.spec_of(n)
            if n in self.u.accounts or spec is None: continue
            self._attach(dict(spec, parent=None, born=now, muts=[], note=req.get("note") or "added by request")); done.append(n)
        self.save(); log.info("[%s] roster request: %s", self.u.name, done)

    def tick(self, now):
        self.request(now)
        p = os.path.join(self.u.dir, "prune.json")
        if not os.path.exists(p): return
        try: per = int(json.load(open(p)).get("per", 5))
        except Exception as e: log.warning("[%s] bad prune.json: %s", self.u.name, e); per = None
        os.remove(p)
        if per and per > 0: self.prune(now, per)

    def snapshot(self):
        return {"removed": len(self.removed), "added": {n: {k: s.get(k) for k in ("parent", "born", "muts")} for n, s in self.added.items()},
                "prunes": self.prunes[-5:][::-1]}
