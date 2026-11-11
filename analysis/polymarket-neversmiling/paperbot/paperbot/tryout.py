"""Tryout universe (port 8088): the champion's candidates traded side by side under the champion's exact rules
(BTC, ETH, SOL and XRP 5-minute windows, $1,000 each; since 11-11 22:40Z every bot sizes its own orders, and the
replicas of the report traders (paperbot/reports.py) trade their own coins, window lengths and style),
so every candidate sees the same markets at the same time and is judged on its own live results.

Entry: the best SIZE bots of the pool, every strategy of the other dashboards plus evolve variants (alive or culled
with settings kept), ranked by the luck-adjusted score below on their 5-minute results of these coins (at least
MIN_POOL markets).

Luck-adjusted score: mean P/L per market minus sqrt(2 ln N) standard errors, N = number of bots compared. That is
roughly how far the luckiest of N bots with no real edge would sit above zero, so a bot must beat pure chance.

Edge: per settled window, 1 if the side it bought won else 0, minus the average price it paid for that side.
A bot that buys at 55c and wins 62% of its windows has a 7c edge, whatever its P/L luck.

Pass (the champion may hire only passing bots), all of:
  at least MIN_WINDOWS settled windows here; P/L above zero; profitable on at least 3 of the 4 coins; profitable in
  more than half of the hours it traded; edge above zero; and profitable in both the first and the second half of its
  windows (it kept working on data it had not been chosen on).
Review every hour: a bot with at least DROP_N windows that is losing money with a negative edge is dropped, a bot
that has not settled any window in IDLE seconds is dropped, and the free seats go to the next best pool bots not
dropped in the last 24 hours.

State: <dir>/tryout.json (members, drops, reviews); balances use the universe's normal accounts.json.
"""
import os, json, math, time, logging, statistics

log = logging.getLogger("tryout")
SIZE = 12; REVIEW = 3600.0; MIN_WINDOWS = 50; MIN_POOL = 20; DROP_N = 20; IDLE = 3 * 3600.0; COOLDOWN = 86400.0; WARMUP = 60.0
COINS_NEEDED = 3


def luck_k(n): return math.sqrt(2 * math.log(max(n, 2)))


def builders(cfg):
    from .strategies import build_registry
    from .bonelab import build_lab_registry
    from .reports import build_report_registry
    return {"main": build_registry(cfg.get("pos_mult", 15.0)), "lab": build_lab_registry(), "rep": build_report_registry()}


def pool(engine, skip, b, assets, horizons, min_markets=MIN_POOL, min_per_coin=0):
    """Every hireable bot of the other dashboards scored on its settled markets of these coins and horizons, each
    market's P/L rescaled to a $1,000 account; best luck-adjusted score first. Evolve variants carry their settings."""
    per = {}; coins = {}; info = {}
    q = (f"select bot, asset, pnl_total from settlements where fills > 0 and asset in ({','.join('?' * len(assets))}) "
         f"and horizon in ({','.join('?' * len(horizons))})")
    for u in engine.universes:
        if u.name in skip: continue
        ev = None
        if getattr(u, "evolver", None) is not None:      # live members, and culled ones whose settings were kept
            ev = {g["name"]: g for g in u.evolver.graveyard if "genes" in g}; ev.update(u.evolver.members)
        for bot, asset, pnl in u.store.query(q, tuple(assets) + tuple(horizons)):
            if ev is not None:
                spec = ev.get(bot)
                if spec is None or spec["src"] not in b: continue
                genes = spec.get("genes", {}); base = spec["base"]; src = spec["src"]; key = bot if genes else base
            else:
                if bot not in u.accounts: continue                     # deleted from its dashboard
                src = "lab" if u.name == "bonelab" and bot in b["lab"] else ("main" if bot in b["main"] else None)
                if src is None: continue                                # scanners and children without a registry entry
                genes = {}; base = key = bot
            per.setdefault(key, []).append(pnl / u.cash * 1000.0)
            coins.setdefault(key, {}).setdefault(asset, 0); coins[key][asset] += 1
            info.setdefault(key, {"key": key, "base": base, "src": src, "genes": genes, "where": set()})["where"].add(u.name)
    elig = {k: xs for k, xs in per.items() if len(xs) >= min_markets and all(coins[k].get(a, 0) >= min_per_coin for a in assets)}
    k = luck_k(len(elig)); out = []
    for key, xs in elig.items():
        m = statistics.mean(xs); se = statistics.stdev(xs) / math.sqrt(len(xs))
        out.append(dict(info[key], where=sorted(info[key]["where"]), score=round(m - k * se, 3), mean=round(m, 3), n=len(xs),
                        total=round(sum(xs), 1), coins=coins[key]))
    out.sort(key=lambda c: -c["score"])
    return out


class Tryout:
    def __init__(self, universe, engine):
        self.u = universe; self.engine = engine; self.cfg = universe.cfg; self.cash = universe.cash
        self.assets = tuple(self.cfg["assets"]); self.horizons = tuple(self.cfg.get("pool_horizons", self.cfg["horizons"]))
        self.path = os.path.join(universe.dir, "tryout.json")
        u = universe; u.accounts = {}; u.strats = []; u.arbs = {}; u.active = []
        u.ladder = u.ladder_model = u.touch_model = u.xarb = None
        self.members = {}; self.dropped = []; self.reviews = []; self.labels = {}; self.board = []
        self.next_review = None; self.ready = time.time() + WARMUP; self._b = None; self._stats = {}; self._stats_at = 0

    def builders(self):
        if self._b is None: self._b = builders(self.cfg)
        return self._b

    # ---- members ---------------------------------------------------------------
    def _attach(self, spec):
        from .sim import Account
        n = spec["key"]
        a = Account(n, self.cash, size_mode="contracts")          # every bot sizes its own orders (user, 11-11 22:40Z)
        a.universe = self.u.name
        obj = self.builders()[spec["src"]][spec["base"]](a)
        for k, v in spec.get("genes", {}).items(): setattr(obj, k, v)
        if spec["src"] != "rep":       # pool bots: the champion's coins and 5-minute windows; report replicas keep their own
            if isinstance(getattr(obj, "assets", None), (set, tuple, list)): obj.assets = set(self.assets)
            if isinstance(getattr(obj, "durs", None), (set, tuple, list)): obj.durs = {300}
        obj.name = n
        self.u.accounts[n] = a; self.u.active.append(n); self.u.strats.append(obj); self.members[n] = spec
        self.labels[n] = f"tryout since {time.strftime('%H:%MZ', time.gmtime(spec['joined']))}" + (f" ({', '.join(spec['where'])})" if spec.get("where") else "")

    def _detach(self, n):
        a = self.u.accounts.pop(n, None)
        if a: a.cancel_all()
        self.u.strats = [s for s in self.u.strats if s.acct.name != n]
        if n in self.u.active: self.u.active.remove(n)
        self.labels.pop(n, None); return self.members.pop(n, None)

    def restore(self):
        if not os.path.exists(self.path): return
        try:
            s = json.load(open(self.path))
            self.dropped = s.get("dropped", []); self.reviews = s.get("reviews", []); self.next_review = s.get("next_review"); self.board = s.get("board", [])
            for spec in s.get("members", []):
                try: self._attach(spec)
                except Exception as e: log.warning("[tryout] could not rebuild %s: %s", spec.get("key"), e)
            log.info("[tryout] restored %d members", len(self.members))
        except Exception as e: log.warning("[tryout] could not restore: %s", e)

    def save(self):
        s = {"members": list(self.members.values()), "dropped": self.dropped[-300:], "reviews": self.reviews[-100:],
             "next_review": self.next_review, "board": self.board[:20]}
        tmp = self.path + ".tmp"; json.dump(s, open(tmp, "w")); os.replace(tmp, self.path)

    # ---- judging -----------------------------------------------------------------
    def stats(self, n):
        """This member's live tryout record (since it joined)."""
        spec = self.members[n]; a = self.u.accounts[n]; t0 = spec["joined"]
        rows = self.u.store.query("select slug, asset, ts, up, pnl_total from settlements where bot = ? and fills > 0 and ts >= ? order by ts", (n, t0))
        fills = {}
        for slug, side, px, qty in self.u.store.query("select slug, side, price, qty from fills where bot = ? and ts >= ? order by ts", (n, t0)):
            fills.setdefault(slug, []).append((side, px, qty))
        coin = {}; hour = {}; edges = []; xs = []
        for slug, asset, ts, up, pnl in rows:
            xs.append(pnl); coin[asset] = coin.get(asset, 0.0) + pnl; hour[int(ts // 3600)] = hour.get(int(ts // 3600), 0.0) + pnl
            f = fills.get(slug)
            if f:
                side = f[0][0]; ent = [(px, q) for s_, px, q in f if s_ == side]; qty = sum(q for _, q in ent)
                if qty > 0:
                    avg = sum(px * q for px, q in ent) / qty; paid = avg if side == "buy" else 1 - avg
                    won = (up and side == "buy") or (not up and side == "sell")
                    edges.append((1.0 if won else 0.0) - paid)
        nw = len(xs); half = nw // 2
        pnl = a.equity(self.engine.mf.markets) - a.start_cash
        k = luck_k(len(self.members))
        mean = statistics.mean(xs) if xs else 0.0; se = statistics.stdev(xs) / math.sqrt(nw) if nw > 1 else 0.0
        edge = statistics.mean(edges) if edges else None
        st = {"n": nw, "pnl": round(pnl, 2), "score": round(mean - k * se, 3) if nw > 1 else None,
              "coins": {c: round(v, 2) for c, v in sorted(coin.items())}, "coins_up": sum(1 for v in coin.values() if v > 0),
              "hours": len(hour), "hours_up": sum(1 for v in hour.values() if v > 0),
              "edge": round(edge, 4) if edge is not None else None, "edge_se": round(statistics.stdev(edges) / math.sqrt(len(edges)), 4) if len(edges) > 1 else None,
              "halves": [round(sum(xs[:half]), 2), round(sum(xs[half:]), 2)] if nw >= 2 else None,
              "last": rows[-1][2] if rows else None}
        why = []
        if nw < MIN_WINDOWS: why.append(f"{nw}/{MIN_WINDOWS} windows")
        if pnl <= 0: why.append("not in profit")
        if st["coins_up"] < COINS_NEEDED: why.append(f"profitable on {st['coins_up']}/4 coins")
        if st["hours"] and st["hours_up"] * 2 <= st["hours"]: why.append(f"profitable in {st['hours_up']}/{st['hours']} hours")
        if edge is None or edge <= 0: why.append("no positive edge")
        if not st["halves"] or min(st["halves"]) <= 0: why.append("not profitable in both halves")
        st["passed"] = not why; st["why"] = why
        return st

    def all_stats(self, now):
        if now - self._stats_at > 15 or set(self._stats) != set(self.members):
            self._stats = {n: self.stats(n) for n in self.members}; self._stats_at = now
        return self._stats

    def passers(self, now):
        """Members that pass, best luck-adjusted score first, as hireable specs."""
        st = self.all_stats(now)
        ok = sorted((n for n in self.members if st[n]["passed"]), key=lambda n: -(st[n]["score"] or 0))
        return [dict(self.members[n], tryout=st[n]) for n in ok]

    # ---- running -----------------------------------------------------------------
    def fill(self, now):
        b = self.builders()
        recent = {d["key"] for d in self.dropped if now - d["ts"] < COOLDOWN}
        cands = pool(self.engine, {self.u.name, "champion"}, b, self.assets, self.horizons); self.board = cands[:20]
        added = []
        for c in cands:
            if len(self.members) >= SIZE: break
            if c["key"] in self.members or c["key"] in recent: continue
            spec = {k: c[k] for k in ("key", "base", "src", "genes", "where")}
            spec.update(joined=now, entry_score=c["score"], entry_n=c["n"])
            try: self._attach(spec); added.append(c["key"])
            except Exception as e: log.warning("[tryout] could not add %s: %s", c["key"], e)
        return added

    def review(self, now):
        st = self.all_stats(now); out = []
        for n in list(self.members):
            s = st[n]; spec = self.members[n]
            losing = s["n"] >= DROP_N and s["pnl"] < 0 and (s["edge"] is None or s["edge"] < 0)
            idle = now - (s["last"] or spec["joined"]) >= IDLE
            if losing or idle:
                self._detach(n); out.append(n)
                self.dropped.append({"key": n, "ts": now, "joined": spec["joined"], "why": "losing with a negative edge" if losing else "idle 3 h",
                                     **{k: s[k] for k in ("n", "pnl", "edge", "coins")}})
        added = self.fill(now); self._stats_at = 0
        self.reviews.append({"ts": now, "dropped": out, "added": added, "passed": [p["key"] for p in self.passers(now)]})
        self.next_review = now + REVIEW; self.save()
        log.info("[tryout] review: dropped %s; added %s", out, added)

    def requested(self, now):
        """A hand-added candidate: <dir>/add.json {"key", "base", "src", "genes"?} (consumed once; may exceed SIZE), or
        {"remove": key} to take one out."""
        p = os.path.join(self.u.dir, "add.json")
        if not os.path.exists(p): return
        try: req = json.load(open(p))
        except Exception as e: log.warning("[tryout] bad add.json: %s", e); req = None
        os.remove(p)
        if req and req.get("remove"):        # take a bot out (to rebuild it); its account and record go with it
            n = req["remove"]
            if n in self.members: self._detach(n); self._stats_at = 0; self.save(); log.info("[tryout] removed %s by request", n)
            return
        if not req or req.get("key") in self.members or req.get("base") not in self.builders().get(req.get("src"), {}):
            log.warning("[tryout] cannot add %s", req); return
        spec = {"key": req["key"], "base": req["base"], "src": req["src"], "genes": req.get("genes", {}), "where": ["added by request"],
                "joined": now, "entry_score": None, "entry_n": 0}
        self._attach(spec); self._stats_at = 0; self.save(); log.info("[tryout] added %s by request", spec["key"])

    def tick(self, now):
        self.requested(now)
        if not self.members:
            if now < self.ready: return
            added = self.fill(now); self.next_review = now + REVIEW; self.save()
            log.info("[tryout] started with %s", added)
            if not added: self.ready = now + 300
            return
        if self.next_review and now >= self.next_review: self.review(now)

    def snapshot(self, now):
        st = self.all_stats(now)
        return {"size": SIZE, "min_windows": MIN_WINDOWS, "assets": list(self.assets), "horizons": list(self.horizons),
                "next_review_in": round(self.next_review - now) if self.next_review else None,
                "members": {n: dict(st[n], joined=s["joined"], entry_score=s.get("entry_score"), where=s.get("where")) for n, s in self.members.items()},
                "dropped": self.dropped[-20:][::-1], "reviews": self.reviews[-6:][::-1],
                "board": [{k: c.get(k) for k in ("key", "score", "mean", "n", "where")} for c in self.board[:12]]}
