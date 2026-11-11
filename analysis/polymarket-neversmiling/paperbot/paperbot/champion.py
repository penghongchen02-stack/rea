"""Champion universe: one $1,000 account that trades only the configured coins (BTC, ETH, SOL, XRP) 5- and 15-minute Up/Down windows with the
single best bot of every other dashboard, and replaces it every hour while it is losing.

Choosing: every bot of every other dashboard (and every evolved variant alive in the evolution universe) is scored
on its settled markets of those coins and horizons only, with each market's P/L rescaled to a $1,000 account (P/L / that
universe's starting cash * 1,000). Score = mean per market minus half its standard error; at least MIN_MARKETS
markets. Only strategies can be hired (whole-book scanners cannot be limited to these markets).

Review: every hour. If the champion has not made money since it was hired (losing, or not trading at all; after at
least one full hour), it is fired and the best-scoring bot that has not been fired in the last 24 hours is hired; the $1,000 bank carries over,
so open positions settle into the same balance. A champion that is up keeps its job. The goal is a profit every
UTC day, which the panel tracks; paper results do not guarantee live results.

State: <dir>/champion.json (current hire, hire history, daily P/L); the account uses the normal accounts.json.
"""
import os, json, math, time, logging, statistics

log = logging.getLogger("champion")
REVIEW = float(os.environ.get("PAPERBOT_CHAMPION_REVIEW", "3600"))
MIN_MARKETS = 20; COOLDOWN = 86400.0; WARMUP = 60.0
NAME = "champion"


class Champion:
    def __init__(self, universe, engine):
        from .sim import Account
        self.u = universe; self.engine = engine; self.cfg = universe.cfg; self.cash = universe.cash
        self.assets = tuple(self.cfg.get("assets", ("btc", "eth"))); self.horizons = tuple(self.cfg.get("horizons", ("5m", "15m")))
        self.path = os.path.join(universe.dir, "champion.json")
        self.acct = Account(NAME, self.cash, size_mode="dollar", size_min=self.cfg.get("size_min", 1.0), size_max=self.cfg.get("size_max", 30.0))
        self.acct.universe = universe.name
        u = universe; u.accounts = {NAME: self.acct}; u.active = [NAME]; u.strats = []; u.arbs = {}
        u.ladder = u.ladder_model = u.touch_model = u.xarb = None
        self.current = None; self.history = []; self.days = {}; self.next_review = None; self.ready = time.time() + WARMUP
        self.labels = {}; self.board = []; self._builders = None

    # ---- construction --------------------------------------------------------
    def builders(self):
        if self._builders is None:
            from .strategies import build_registry
            from .bonelab import build_lab_registry
            self._builders = {"main": build_registry(self.cfg.get("pos_mult", 15.0)), "lab": build_lab_registry()}
        return self._builders

    def hire(self, spec, now, reason):
        a = self.acct; a.cancel_all()
        a.size_mode = "contracts" if spec["src"] == "lab" else "dollar"
        obj = self.builders()[spec["src"]][spec["base"]](a)
        for k, v in spec.get("genes", {}).items(): setattr(obj, k, v)
        if isinstance(getattr(obj, "assets", None), (set, tuple, list)): obj.assets = set(self.assets)   # trade every coin of this universe
        obj.name = spec["key"]
        self.u.strats = [obj]
        eq = a.equity(self.engine.mf.markets)
        self.current = dict(spec, hired=now, eq_at_hire=round(eq, 4), reason=reason)
        self.labels[NAME] = f"now trading as {spec['key']} (hired {time.strftime('%H:%MZ', time.gmtime(now))}; {reason})"
        self.next_review = now + REVIEW; self.save()
        log.info("[champion] hired %s (%s)", spec["key"], reason)

    # ---- persistence ---------------------------------------------------------
    def restore(self):
        if not os.path.exists(self.path): return
        try:
            s = json.load(open(self.path))
            self.history = s.get("history", []); self.days = s.get("days", {}); self.board = s.get("board", [])
            cur = s.get("current")
            if cur:
                hired, eq0, nr = cur["hired"], cur["eq_at_hire"], s.get("next_review")
                self.hire(cur, hired, cur.get("reason", "restored"))
                self.current["eq_at_hire"] = eq0; self.next_review = nr or hired + REVIEW
            log.info("[champion] restored: %s", cur and cur["key"])
        except Exception as e: log.warning("[champion] could not restore: %s", e)

    def save(self):
        s = {"current": self.current, "history": self.history[-500:], "days": self.days, "next_review": self.next_review, "board": self.board[:20]}
        tmp = self.path + ".tmp"; json.dump(s, open(tmp, "w")); os.replace(tmp, self.path)

    # ---- choosing --------------------------------------------------------------
    def candidates(self):
        """Every hireable bot, best score first: [{key, base, src, genes, score, mean, n, where}]."""
        b = self.builders(); per = {}; info = {}
        q = (f"select bot, pnl_total from settlements where fills > 0 and asset in ({','.join('?' * len(self.assets))}) "
             f"and horizon in ({','.join('?' * len(self.horizons))})")
        for u in self.engine.universes:
            if u is self.u: continue
            ev = u.evolver.members if getattr(u, "evolver", None) is not None else None
            for bot, pnl in u.store.query(q, self.assets + self.horizons):
                if ev is not None:
                    spec = ev.get(bot)
                    if spec is None or spec["src"] not in b: continue      # dead evolve members keep no settings
                    genes = spec.get("genes", {}); base = spec["base"]; src = spec["src"]
                    key = bot if genes else base
                else:
                    if bot not in u.accounts: continue                     # deleted from its dashboard
                    src = "lab" if u.name == "bonelab" and bot in b["lab"] else ("main" if bot in b["main"] else None)
                    if src is None: continue                                # scanners and ladder bots
                    genes = {}; base = key = bot
                per.setdefault(key, []).append(pnl / u.cash * 1000.0)
                info.setdefault(key, {"key": key, "base": base, "src": src, "genes": genes, "where": set()})["where"].add(u.name)
        out = []
        for k, xs in per.items():
            if len(xs) < MIN_MARKETS: continue
            m = statistics.mean(xs); se = statistics.stdev(xs) / math.sqrt(len(xs))
            out.append(dict(info[k], where=sorted(info[k]["where"]), score=round(m - 0.5 * se, 3), mean=round(m, 3), n=len(xs), total=round(sum(xs), 1)))
        out.sort(key=lambda c: -c["score"])
        return out

    def pick(self, now, exclude=()):
        cands = self.candidates(); self.board = cands[:20]
        fired = {h["key"] for h in self.history if h.get("fired") and now - h["fired"] < COOLDOWN}
        return next((c for c in cands if c["key"] not in fired and c["key"] not in exclude and c["score"] > 0), None)

    # ---- running -----------------------------------------------------------------
    def pnl_since_hire(self):
        return self.acct.equity(self.engine.mf.markets) - self.current["eq_at_hire"] if self.current else 0.0

    def requested(self, now):
        """A hand-picked hire: <dir>/hire.json {"key": "<bot>"} (consumed once). The key may be any scored candidate,
        a strategy name, or a member of the evolution universe (its evolved settings are copied)."""
        p = os.path.join(self.u.dir, "hire.json")
        if not os.path.exists(p): return
        try: key = json.load(open(p))["key"]
        except Exception as e: log.warning("[champion] bad hire.json: %s", e); os.remove(p); return
        os.remove(p)
        spec = next((c for c in self.candidates() if c["key"] == key), None)
        if spec is None:
            for u in self.engine.universes:
                ev = getattr(u, "evolver", None)
                if ev is not None and key in ev.members and ev.members[key]["src"] in self.builders():
                    m = ev.members[key]; spec = {"key": key, "base": m["base"], "src": m["src"], "genes": m.get("genes", {}), "where": [u.name]}
            b = self.builders()
            if spec is None and key in b["main"]: spec = {"key": key, "base": key, "src": "main", "genes": {}, "where": []}
            if spec is None and key in b["lab"]: spec = {"key": key, "base": key, "src": "lab", "genes": {}, "where": ["bonelab"]}
        if spec is None: log.warning("[champion] requested hire %s: no such bot", key); return
        cur = self.current
        if cur and cur["key"] != key:
            self.history.append({"key": cur["key"], "hired": cur["hired"], "fired": now, "pnl": round(self.pnl_since_hire(), 2), "why": "replaced by request"})
        self.hire(spec, now, "picked by the user")

    def tick(self, now):
        eq = self.acct.equity(self.engine.mf.markets); day = time.strftime("%Y-%m-%d", time.gmtime(now))
        d = self.days.setdefault(day, {"open": round(eq, 4)}); d["last"] = round(eq, 4); d["pnl"] = round(eq - d["open"], 2)
        self.requested(now)
        if self.current is None:
            if now < self.ready: return
            c = self.pick(now)
            if c: self.hire(c, now, f"best score {c['score']:+.2f}/market over {c['n']} markets")
            else: self.ready = now + 300
            return
        if now < self.next_review: return
        p = self.pnl_since_hire(); cur = self.current
        if p <= 0 and now - cur["hired"] >= REVIEW - 5:        # not making money (losing or idle): replace
            self.history.append({"key": cur["key"], "hired": cur["hired"], "fired": now, "pnl": round(p, 2)})
            c = self.pick(now, exclude=(cur["key"],))
            if c: self.hire(c, now, f"replaced {cur['key']} ({p:+.2f}); score {c['score']:+.2f}/market over {c['n']} markets"); return
            self.history[-1].pop("fired")                                  # nobody better: keep it
            log.info("[champion] %s is at %.2f but no other candidate scores above 0", cur["key"], p)
        else:
            self.board = self.candidates()[:20]
        self.next_review = now + REVIEW; self.save()

    def snapshot(self, now):
        cur = self.current
        return {"assets": list(self.assets), "horizons": list(self.horizons), "review": REVIEW, "min_markets": MIN_MARKETS,
                "ready_in": round(self.ready - now) if cur is None else None,
                "current": cur and {k: cur.get(k) for k in ("key", "base", "src", "genes", "score", "mean", "n", "where", "hired", "reason")},
                "pnl_since_hire": round(self.pnl_since_hire(), 2) if cur else None,
                "next_review_in": round(self.next_review - now) if self.next_review else None,
                "history": self.history[-30:][::-1], "days": dict(sorted(self.days.items())[-14:]),
                "board": [{k: c[k] for k in ("key", "score", "mean", "n", "total", "where")} for c in self.board[:10]]}
