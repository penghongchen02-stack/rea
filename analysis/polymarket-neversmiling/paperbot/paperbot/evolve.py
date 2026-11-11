"""Evolution universe: the best 20 bots of every other dashboard, $1,000 each, and every 30 minutes the bottom 5 by
P/L are removed and replaced by 5 children of the current top 5.

A child is a copy of its parent (same strategy class, same settings it inherited) with 1-3 of its tuning numbers
changed: price caps, z thresholds, timing windows, edges and sizes. Request-rate settings (fetch budgets, polling
periods) are never changed, so no child can poll the exchange harder than its parent.

What it learns: each child is scored once, at the first cull after it has lived a full period, by its return minus
the median return of all bots at that cull (children that never traded are not scored). That excess is credited to
every (strategy, setting, direction) it mutated. New mutations of the same setting lean towards the direction whose children did better, so the population
keeps more of the changes that worked. Parents are picked from the top 5, weighted 5:4:3:2:1 by rank.

State lives in <dir>/evolve.json (members, their settings, lineage, the graveyard, culls and lessons); balances use
the universe's normal accounts.json, so the run resumes after a restart.
"""
import os, re, json, math, time, random, inspect, numbers, logging, statistics

log = logging.getLogger("evolve")
PERIOD = float(os.environ.get("PAPERBOT_EVOLVE_PERIOD", "1800"))
SIZE = 20; CULL = 5; WARMUP = 90.0
PRICE = {"MAX_PRICE", "max_price", "p_hi", "p_lo", "PAIR_CAP", "pair_cap", "REST_CAP", "hi", "lo"}
UNIT = {"frac", "ORDER_FRAC", "WINDOW_FRAC", "shrink"}
FROZEN = {"BUDGET", "budget", "PERIOD", "SCAN_EVERY", "WATCH", "last_scan", "seed"}
PAIRS = [("t_lo", "t_hi"), ("p_lo", "p_hi"), ("lo", "hi"), ("T_MIN", "T_MAX"), ("t_min", "t_max")]


def _used_constants():
    """Upper-case class constants that bot code actually reads through self (only those can be overridden per bot)."""
    from . import strategies, arbs, locks, ladder, bonelab
    src = "".join(inspect.getsource(m) for m in (strategies, arbs, locks, ladder, bonelab))
    bots = os.path.join(os.path.dirname(__file__), "bots")
    for f in sorted(os.listdir(bots)) if os.path.isdir(bots) else []:
        if f.endswith(".py"): src += open(os.path.join(bots, f)).read()
    return set(re.findall(r"self\.([A-Z][A-Z0-9_]+)", src))


def _num(v): return isinstance(v, numbers.Real) and not isinstance(v, bool)


def genes(obj, used):
    """The tunable numbers of one bot instance: numeric instance settings plus the class constants it reads."""
    out = {k: v for k, v in vars(obj).items() if _num(v) and not k.startswith("_") and k not in FROZEN}
    for k in used:
        if k not in out and k not in FROZEN and _num(getattr(type(obj), k, None)): out[k] = getattr(obj, k)
    return out


def mutate_value(rng, gene, v, d):
    """One step of gene in direction d (+1 / -1)."""
    if gene in PRICE: return round(min(0.99, max(0.02, v + d * rng.uniform(0.01, 0.04))), 3)
    f = math.exp(d * rng.uniform(0.10, 0.35))
    if isinstance(v, int):
        nv = int(round(v * f))
        if nv == v: nv = v + d
        return max(0, nv)
    if v == 0: return 0.01 if d > 0 else 0.0
    nv = v * f
    if gene in UNIT: nv = min(1.0, max(0.001, nv))
    return float(f"{nv:.4g}")


class Evolver:
    def __init__(self, universe, engine):
        self.u = universe; self.engine = engine; self.cfg = universe.cfg; self.cash = universe.cash
        self.path = os.path.join(universe.dir, "evolve.json"); self.rng = random.Random()
        self.members = {}; self.graveyard = []; self.culls = []; self.lessons = {}; self.labels = {}
        self.counter = 0; self.seeded_at = None; self.next_cull = None; self.ready = time.time() + WARMUP
        self.used = _used_constants(); self._builders = None

    # ---- construction ------------------------------------------------------
    def builders(self):
        if self._builders is None:
            from .strategies import build_registry
            from .bonelab import build_lab_registry
            from .arbs import scanner_builders
            self._builders = {"main": build_registry(self.cfg.get("pos_mult", 1.0)), "lab": build_lab_registry(),
                              "scan": scanner_builders(self.engine)}
        return self._builders

    def source_of(self, base, universe_name):
        b = self.builders()
        if universe_name == "bonelab" and base in b["lab"]: return "lab"
        if base in b["scan"]: return "scan"
        if base in b["main"]: return "main"
        return None

    def _bot(self, spec):
        """Account + strategy/scanner for one member, with its inherited settings applied."""
        from .sim import Account
        lab = spec["src"] == "lab"
        a = Account(spec["name"], self.cash, size_mode="contracts" if lab else self.cfg["size_mode"],
                    size_min=self.cfg.get("size_min", 0.1), size_max=self.cfg.get("size_max", 30.0))
        a.universe = self.u.name
        obj = self.builders()[spec["src"]][spec["base"]](a)
        for k, v in spec.get("genes", {}).items(): setattr(obj, k, v)
        return a, obj

    def _attach(self, spec):
        a, obj = self._bot(spec); n = spec["name"]
        self.u.accounts[n] = a; self.u.active.append(n); self.members[n] = spec
        if spec["src"] == "scan": self.u.arbs[n] = obj
        else: self.u.strats.append(obj)
        self.labels[n] = self._label(spec)

    def _detach(self, name):
        a = self.u.accounts.pop(name, None)
        if a: a.cancel_all()
        self.u.arbs.pop(name, None)
        self.u.strats = [s for s in self.u.strats if s.acct.name != name]
        if name in self.u.active: self.u.active.remove(name)
        self.labels.pop(name, None); return self.members.pop(name, None)

    def _label(self, spec):
        if spec["gen"] == 0: return f"seed: {spec.get('note', '')}".strip()
        ch = "; ".join(f"{g} {o:g}->{n:g}" for g, o, n in spec.get("muts", []))
        return f"gen {spec['gen']}, child of {spec['parent']} ({ch})"

    # ---- persistence ---------------------------------------------------------
    def restore(self):
        if not os.path.exists(self.path): return
        try:
            s = json.load(open(self.path))
            self.counter = s.get("counter", 0); self.seeded_at = s.get("seeded_at"); self.next_cull = s.get("next_cull")
            self.graveyard = s.get("graveyard", []); self.culls = s.get("culls", []); self.lessons = s.get("lessons", {})
            for spec in s.get("members", []):
                try: self._attach(spec)
                except Exception as e: log.warning("could not rebuild %s: %s", spec.get("name"), e)
            log.info("[evolve] restored %d members, %d culls", len(self.members), len(self.culls))
        except Exception as e: log.warning("[evolve] could not restore: %s", e)

    def save(self):
        s = {"counter": self.counter, "seeded_at": self.seeded_at, "next_cull": self.next_cull, "members": list(self.members.values()),
             "graveyard": self.graveyard[-500:], "culls": self.culls[-200:], "lessons": self.lessons}
        tmp = self.path + ".tmp"; json.dump(s, open(tmp, "w")); os.replace(tmp, self.path)

    # ---- ranking -------------------------------------------------------------
    def pnl(self, name): a = self.u.accounts[name]; return a.equity(self.engine.mf.markets) - a.start_cash

    def seed(self, now):
        """Top 20 keys by mean return across every other universe (bots with at least one fill)."""
        rets = {}; where = {}
        for u in self.engine.universes:
            if u is self.u: continue
            for name, a in u.accounts.items():
                if a.maker_fills + a.taker_fills == 0: continue
                src = self.source_of(name, u.name)
                if src is None: continue
                r = (a.equity(self.engine.mf.markets) - a.start_cash) / a.start_cash
                rets.setdefault((name, src), []).append(r); where.setdefault((name, src), []).append(u.name)
        ranked = sorted(rets, key=lambda k: -statistics.mean(rets[k]))[:SIZE]
        for i, (name, src) in enumerate(ranked, 1):
            m = statistics.mean(rets[(name, src)])
            self._attach({"name": name, "base": name, "src": src, "genes": {}, "gen": 0, "parent": None, "born": now, "muts": [],
                          "note": f"#{i}, mean {m * 100:+.1f}% over {', '.join(where[(name, src)])}"})
        self.seeded_at = now; self.next_cull = now + PERIOD; self.save()
        log.info("[evolve] seeded with %s", ", ".join(n for n, _ in ranked))

    # ---- learning --------------------------------------------------------------
    def lean(self, base, gene):
        """Probability of stepping a setting up, from the excess return of earlier children that moved it."""
        l = self.lessons.get(f"{base}|{gene}")
        if not l: return 0.5
        up = l["up"][0] / l["up"][1] if l["up"][1] else 0.0; dn = l["down"][0] / l["down"][1] if l["down"][1] else 0.0
        return 0.5 + 0.35 * math.tanh((up - dn) * 100)

    def score(self, ranked, now):
        rets = {n: self.pnl(n) / self.cash for n in ranked}; med = statistics.median(rets.values())
        for n in ranked:
            spec = self.members[n]
            if spec["gen"] == 0 or "scored" in spec or now - spec["born"] < PERIOD - 60: continue
            a = self.u.accounts[n]
            if a.maker_fills + a.taker_fills == 0: continue      # never traded: says nothing about its settings
            ex = rets[n] - med; spec["scored"] = round(ex, 5)
            for g, o, nv in spec.get("muts", []):
                l = self.lessons.setdefault(f"{spec['base']}|{g}", {"up": [0.0, 0], "down": [0.0, 0]})
                d = l["up" if nv > o else "down"]; d[0] += ex; d[1] += 1

    def child(self, parent, now):
        p = self.members[parent]; _, obj = self._bot(p); gs = genes(obj, self.used)
        if not gs: return None
        g2 = dict(p.get("genes", {})); muts = []
        for gene in self.rng.sample(sorted(gs), min(len(gs), self.rng.choice((1, 1, 2, 3)))):
            old = g2.get(gene, gs[gene]); d = 1 if self.rng.random() < self.lean(p["base"], gene) else -1
            new = mutate_value(self.rng, gene, old, d)
            if new == old: continue
            g2[gene] = new; muts.append([gene, old, new])
        for lo, hi in PAIRS:        # keep windows and bands ordered
            vlo = g2.get(lo, gs.get(lo)); vhi = g2.get(hi, gs.get(hi))
            if _num(vlo) and _num(vhi) and vlo >= vhi:
                muts = [m for m in muts if m[0] not in (lo, hi)]
                for k in (lo, hi):
                    if k in p.get("genes", {}): g2[k] = p["genes"][k]
                    else: g2.pop(k, None)
        if not muts: return None
        self.counter += 1
        return {"name": f"{p['base']}_e{self.counter}", "base": p["base"], "src": p["src"], "genes": g2, "gen": p["gen"] + 1,
                "parent": parent, "born": now, "muts": muts}

    def cull(self, now):
        # by P/L; on a tie (often $0 for bots that have not traded yet) the one that has had longer goes first,
        # so a new child gets at least one full period before it can be removed
        ranked = sorted(self.members, key=lambda n: (self.pnl(n), self.members[n]["born"]), reverse=True)
        self.score(ranked, now)
        removed = []
        for n in ranked[-CULL:]:
            a = self.u.accounts[n]; spec = self.members[n]
            rec = {"name": n, "base": spec["base"], "gen": spec["gen"], "parent": spec["parent"], "born": spec["born"], "died": now,
                   "pnl": round(self.pnl(n), 2), "fills": a.maker_fills + a.taker_fills, "muts": spec.get("muts", [])}
            self.graveyard.append(rec); removed.append(rec); self._detach(n)
        top = [n for n in ranked if n in self.members][:5]; weights = list(range(len(top), 0, -1)); added = []
        tries = 0
        while len(added) < CULL and top and tries < 50:
            tries += 1
            try: spec = self.child(self.rng.choices(top, weights)[0], now)
            except Exception as e: log.warning("[evolve] child failed: %s", e); spec = None
            if spec: self._attach(spec); added.append(spec)
        self.culls.append({"ts": now, "removed": [[r["name"], r["pnl"]] for r in removed],
                           "added": [[s["name"], s["parent"], s["muts"]] for s in added],
                           "top": [[n, round(self.pnl(n), 2)] for n in top]})
        self.next_cull = now + PERIOD; self.save()
        log.info("[evolve] cull %d: removed %s; added %s", len(self.culls), [r["name"] for r in removed], [s["name"] for s in added])

    def tick(self, now):
        if not self.members:
            if now >= self.ready: self.seed(now)
            return
        if self.next_cull and now >= self.next_cull: self.cull(now)

    def snapshot(self, now):
        return {"period": PERIOD, "size": SIZE, "cull": CULL, "seeded_at": self.seeded_at,
                "next_cull_in": round(self.next_cull - now) if self.next_cull else None, "ready_in": round(self.ready - now) if not self.members else None,
                "generation": max((s["gen"] for s in self.members.values()), default=0), "culls_done": len(self.culls),
                "members": {n: {k: s.get(k) for k in ("base", "gen", "parent", "born", "muts", "scored")} for n, s in self.members.items()},
                "culls": self.culls[-12:][::-1], "graveyard": self.graveyard[-40:][::-1],
                "lessons": {k: {"up": [round(v["up"][0], 4), v["up"][1]], "down": [round(v["down"][0], 4), v["down"][1]], "lean_up": round(self.lean(*k.split("|", 1)), 2)} for k, v in self.lessons.items()}}
