"""Real-money fast_pickoff (user, 11-17 10:50Z). OFF until the user says so.

This is the only module that can send a real order to Polymarket US. It makes the same decision as the paper
fast_pickoff (paperbot/fastpick.py, through the switches of paperbot/fastpicks.py): a Coinbase jump the quote has not
followed, with an edge left after the taker fee. What differs is what happens after the decision.

Two modes:
  SHADOW (the default): no order is sent. Each signal is sent to the exchange's preview endpoint, which checks the
      order and places nothing, so the request, the signature and the round trip are the real ones. When the answer
      is back, the bot looks at the book again: a signal counts as a shadow fill only if the quote it wanted is still
      there. Shadow fills are settled like real ones, which gives a paper result at the real delay.
  ARMED: the same signal is sent as a real limit order, immediate-or-cancel, at the quote that was seen: it fills what
      is still there at that price or better and cancels the rest; it can never pay more and never rests on the book.

Armed needs all of: the file <dir>/ARMED holding exactly ARM_PHRASE, no <dir>/STOP file, the API key, and no halt.
`place()` checks this itself, so no other code path can send an order. Remove ARMED or create STOP to switch off;
both are read before every order.

Limits (live.json, read at start; see DEFAULTS): bet size as a share of the balance with a floor and a cap, a daily
loss stop, a total loss stop, a bets-a-day cap, a signal-age cap and a halt after repeated errors. A halt needs a
person: delete <dir>/HALT after reading why.

What is verified and what is not: the request format, both directions and the signature were accepted by the preview
endpoint. The shape of a real fill's answer is taken from the documentation and has not been seen, because no real
order has been sent. So the bot never trusts that answer alone: after every order it reads the position back from the
exchange and uses that as the truth; a difference halts it.

Files in <dir> (paperbot-data/live): live.json (limits), state.json (positions and totals), status.json (a summary,
rewritten every few seconds), events.jsonl (every signal, request and answer; never the key).
"""
import os, json, time, math, uuid, base64, queue, logging, threading, collections

from .fastpicks import Pick
from .sim import Account
from . import ws, feeds

log = logging.getLogger("live")
API = "https://api.polymarket.us"
ARM_PHRASE = "REAL MONEY ON"
DEFAULTS = {
    "assets": ["btc"],          # Bitcoin only: the one coin whose paper result held up when fills were made worse
    "exit": True,               # fast_pickoff's protective exit (false: hold every bet to the close)
    "theta": 0.095,             # taker fee coefficient assumed in the edge (Tier 1; a new account pays 0.0695 for a week)
    "bet_pct": 0.01,            # one bet = this share of the balance ...
    "bet_min_usd": 1.0,         # ... but at least this (no bet if the balance cannot cover it)
    "bet_max_usd": 5.0,         # ... and at most this
    "day_loss_usd": 10.0,       # no new bets for the rest of the UTC day after losing this much in it
    "total_loss_usd": 30.0,     # halt after losing this much since the bot was armed
    "max_bets_day": 150,
    "max_signal_age_ms": 400,   # a signal older than this when the sender picks it up is dropped
    "max_errors": 3,            # consecutive failed requests before a halt
    "shadow_bank_usd": 100.0,   # balance assumed for bet sizes while in shadow mode
}


def now_ms(): return int(time.time() * 1000)
def utc_day(ts=None): return time.strftime("%Y-%m-%d", time.gmtime(ts or time.time()))
def fmt_px(p): return ("%.4f" % p).rstrip("0").rstrip(".")


def order_body(slug, side, yes_price, qty, close=False):
    """The order for the paper engine's side: "buy" = Up (YES), "sell" = Down (NO). price.value is always the YES
    price. Opening Down is BUY_SHORT at the YES bid; closing Up is SELL_LONG at the YES bid; closing Down is
    SELL_SHORT at the YES ask."""
    if not (0.01 <= yes_price <= 0.99): raise ValueError(f"price out of bounds: {yes_price}")
    if qty <= 0: raise ValueError(f"bad quantity: {qty}")
    if close: intent = "ORDER_INTENT_SELL_LONG" if side == "sell" else "ORDER_INTENT_SELL_SHORT"
    else: intent = "ORDER_INTENT_BUY_LONG" if side == "buy" else "ORDER_INTENT_BUY_SHORT"
    return {"marketSlug": slug, "type": "ORDER_TYPE_LIMIT", "price": {"value": fmt_px(yes_price), "currency": "USD"}, "quantity": qty,
            "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL", "intent": intent, "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC"}


def _num(x, default=0.0):
    if isinstance(x, dict): x = x.get("value")
    try: return float(x)
    except (TypeError, ValueError): return default


def parse_fill(js):
    """(filled contracts, average YES price, fee, final state) from a create-order or get-order answer, or None when
    the answer does not say (no executions and no order): the caller then reads the position back."""
    if not isinstance(js, dict): return None
    order = js.get("order"); ex = js.get("executions") or []
    if order is None and ex: order = (ex[-1] or {}).get("order")
    if not isinstance(order, dict): return None
    state = order.get("state"); cum = _num(order.get("cumQuantity")); avg = _num(order.get("avgPx")); fee = _num(order.get("commissionNotionalTotalCollected"))
    fills = [e for e in ex if e.get("type") in ("EXECUTION_TYPE_FILL", "EXECUTION_TYPE_PARTIAL_FILL")]
    if fills:
        q = sum(_num(e.get("lastShares")) for e in fills)
        if q > 0:
            px = sum(_num(e.get("lastShares")) * _num(e.get("lastPx")) for e in fills) / q
            if cum <= 0: cum = q
            if avg <= 0: avg = px
            if fee <= 0: fee = sum(_num(e.get("commissionNotionalCollected")) for e in fills)
    if state in (None, "ORDER_STATE_PENDING_NEW", "ORDER_STATE_PENDING_RISK", "ORDER_STATE_NEW", "ORDER_STATE_PARTIALLY_FILLED") and not fills: return None   # not final yet
    return cum, avg, fee, state


class Exchange:
    """Signed requests to the retail API. One session per thread that uses it (keep-alive)."""
    def __init__(self, dir_):
        import requests
        from cryptography.hazmat.primitives.asymmetric import ed25519
        self.dir = dir_; self.s = requests.Session()
        self.pk = ed25519.Ed25519PrivateKey.from_private_bytes(base64.b64decode(ws.SECRET)[:32])

    def _headers(self, method, path):
        ts = str(now_ms()); sig = base64.b64encode(self.pk.sign(f"{ts}{method}{path}".encode())).decode()
        return {"X-PM-Access-Key": ws.KEY_ID, "X-PM-Timestamp": ts, "X-PM-Signature": sig, "Content-Type": "application/json", "User-Agent": feeds.USER_AGENT}

    def call(self, method, path, body=None, extra=None, timeout=6.0):
        """(status, parsed body or text, milliseconds). status 0 = no answer (the outcome is unknown)."""
        t0 = time.perf_counter(); h = self._headers(method, path)
        if extra: h.update(extra)
        try:
            r = self.s.request(method, API + path, headers=h, data=None if body is None else json.dumps(body), timeout=timeout)
            try: js = r.json()
            except ValueError: js = r.text[:500]
            return r.status_code, js, (time.perf_counter() - t0) * 1000.0
        except Exception as e:
            return 0, f"{type(e).__name__}: {e}"[:300], (time.perf_counter() - t0) * 1000.0

    def armed(self):
        if os.path.exists(os.path.join(self.dir, "STOP")) or os.path.exists(os.path.join(self.dir, "HALT")): return False
        try: return open(os.path.join(self.dir, "ARMED")).read().strip() == ARM_PHRASE
        except OSError: return False

    def balance(self):
        c, js, ms = self.call("GET", "/v1/account/balances")
        if c != 200 or not isinstance(js, dict): return None
        for b in js.get("balances") or []:
            if b.get("currency") == "USD": return {"cash": _num(b.get("currentBalance")), "buying_power": _num(b.get("buyingPower")), "open_orders": _num(b.get("openOrders"))}
        return None

    def positions(self):
        """{slug: signed YES contracts}, or None when the exchange did not answer."""
        c, js, ms = self.call("GET", "/v1/portfolio/positions")
        if c != 200 or not isinstance(js, dict): return None
        out = {}
        for slug, p in (js.get("positions") or {}).items():
            q = _num(p.get("netPositionDecimal"), None) if isinstance(p, dict) else None
            if q is None: q = _num(p.get("netPosition")) if isinstance(p, dict) else 0.0
            if abs(q) > 1e-9 and not p.get("expired"): out[slug] = q
        return out

    def preview(self, body): return self.call("POST", "/v1/order/preview", {"request": body}, timeout=4.0)
    def order(self, oid): return self.call("GET", f"/v1/order/{oid}")
    def cancel_all(self, slugs=()): return self.call("POST", "/v1/orders/open/cancel", {"slugs": list(slugs)})

    def place(self, body, key):
        """The one place a real order is sent. Refuses unless armed at this moment."""
        if not self.armed(): raise RuntimeError("not armed: no order sent")
        b = dict(body); b["synchronousExecution"] = True; b["maxBlockTime"] = "2"
        return self.call("POST", "/v1/orders", b, extra={"Idempotency-Key": key}, timeout=5.0)


class _Hook:
    """Stands in for FastPick's fill queue: a planned fill goes to the live sender instead of a paper account."""
    def __init__(self, fn): self.fn = fn
    def append(self, sig): self.fn(sig)
    def __bool__(self): return False
    def __len__(self): return 0


class LiveBot(Pick):
    EXIT_GAP = 2.0          # seconds between exit attempts in one market

    def __init__(self, engine, dir_, cfg, exchange=None, keeper=True):
        self.dir = dir_; self.cfg = cfg; self.ex = exchange or Exchange(dir_)
        acct = Account("live_fast_pickoff", cash=cfg["shadow_bank_usd"], theta=cfg["theta"]); acct.universe = "live"
        self.ASSETS = tuple(cfg["assets"]); self.EXIT = bool(cfg["exit"])
        super().__init__(acct, engine, "live_fast_pickoff")
        ls = engine.spot.listeners; ls.remove(self.on_spot); ls.insert(0, self.on_spot)       # decide before the paper bots
        self.queue = _Hook(self._signal); self.out = queue.Queue(maxsize=50)
        self.lock = threading.RLock(); self.events = collections.deque(maxlen=60); self.lat_ms = collections.deque(maxlen=500)
        self.bal = None; self.bal_ts = 0.0; self.errors = 0; self.last_exit = {}
        self.st = {"pos": {}, "shadow": {}, "day": utc_day(), "day_pnl": 0.0, "day_bets": 0, "total_pnl": 0.0, "bets": 0, "fills": 0, "fees": 0.0,
                   "shadow_stats": {"signals": 0, "previews_ok": 0, "still_there": 0, "settled": 0, "won": 0, "pnl": 0.0}, "armed_at": None}
        self._load()
        threading.Thread(target=self._sender, daemon=True, name="live-sender").start()
        if keeper: threading.Thread(target=self._keeper, daemon=True, name="live-keeper").start()

    # ---- state ---------------------------------------------------------------------
    def _path(self, n): return os.path.join(self.dir, n)

    def _load(self):
        try: saved = json.load(open(self._path("state.json")))
        except (OSError, ValueError): return
        for k, v in saved.items():
            if k == "shadow_stats": self.st[k].update(v)
            else: self.st[k] = v

    def _save(self):
        with self.lock:              # the sender, the keeper and the engine all save
            tmp = self._path("state.json.tmp")
            with open(tmp, "w") as f: json.dump(self.st, f)
            os.replace(tmp, self._path("state.json"))

    def _event(self, kind, **kw):
        rec = {"ts": round(time.time(), 3), "kind": kind, **kw}
        self.events.appendleft(rec)
        try:
            with open(self._path("events.jsonl"), "a") as f: f.write(json.dumps(rec, default=str) + "\n")
        except OSError as e: log.warning("[live] cannot write events: %s", e)
        return rec

    def _halt(self, why):
        log.error("[live] HALT: %s", why); self._event("halt", why=why)
        with open(self._path("HALT"), "w") as f: f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {why}\n")

    def mode(self):
        if os.path.exists(self._path("STOP")): return "STOPPED"
        if os.path.exists(self._path("HALT")): return "HALTED"
        return "ARMED" if self.ex.armed() else "SHADOW"

    def _roll_day(self):
        d = utc_day()
        if d != self.st["day"]: self.st["day"] = d; self.st["day_pnl"] = 0.0; self.st["day_bets"] = 0

    # ---- sizing and limits ------------------------------------------------------------
    def bank(self, armed):
        if not armed: return self.cfg["shadow_bank_usd"]
        return self.bal["buying_power"] if self.bal and time.time() - self.bal_ts < 120 else None

    def bet_usd(self, armed):
        b = self.bank(armed); c = self.cfg
        if b is None: return 0.0
        usd = min(max(b * c["bet_pct"], c["bet_min_usd"]), c["bet_max_usd"])
        return usd if usd <= b else 0.0

    def dollars(self, acct): return self.bet_usd(self.ex.armed())      # FastPick sizes the planned fill with this

    def blocked(self, armed):
        """Why no new bet may be opened now, or None."""
        c = self.cfg; s = self.st; self._roll_day()
        if s["day_bets"] >= c["max_bets_day"]: return "bets-a-day cap"
        if armed:
            if s["day_pnl"] <= -c["day_loss_usd"]: return "daily loss stop"
            if s["total_pnl"] <= -c["total_loss_usd"]: self._halt(f"total loss stop: {s['total_pnl']:.2f}"); return "total loss stop"
            if self.bank(True) is None: return "balance unknown"
        return None

    # ---- Coinbase thread: a decision arrives ----------------------------------------------
    def _signal(self, sig):
        try: self.out.put_nowait(("entry", sig, time.perf_counter()))
        except queue.Full: self.stats["dropped"] += 1

    # ---- engine thread --------------------------------------------------------------------
    def on_tick(self, market, ctx, now):
        """The protective exit of fast_pickoff (LastCallHold.manage), for the real or the shadow position."""
        if not self.EXIT: return
        slug = market.slug; armed = self.ex.armed(); p = self.st["pos" if armed else "shadow"].get(slug)
        if not p or market.remaining(now) < 3 or market.state not in (None, "MARKET_STATE_OPEN"): return
        if now - self.last_exit.get(slug, 0) < self.EXIT_GAP: return
        from .strategies import p_up
        pr, det = p_up(market, ctx["index"], ctx["spot"], now)
        if pr is None or det.get("n_known", 0) < 20: return
        z = det["z"]; q = p["qty"]
        if q > 0 and z < self.EXIT_Z and market.best_bid is not None: side, px = "sell", market.best_bid
        elif q < 0 and z > -self.EXIT_Z and market.best_ask is not None: side, px = "buy", market.best_ask
        else: return
        self.last_exit[slug] = now
        try: self.out.put_nowait(("exit", (slug, side, px, abs(q), now, f"exit z{z:.1f}"), time.perf_counter()))
        except queue.Full: pass

    def on_settle(self, m, up):
        """A window closed: book the result of the real and the shadow position in it."""
        with self.lock:
            for book in ("pos", "shadow"):
                p = self.st[book].pop(m.slug, None)
                if not p: continue
                pnl = p["qty"] * ((1.0 if up else 0.0) - p["avg"]) - p.get("fee", 0.0) + p.get("realized", 0.0)
                if book == "pos":
                    self._roll_day(); self.st["day_pnl"] += pnl; self.st["total_pnl"] += pnl
                    self._event("settled", slug=m.slug, up=bool(up), qty=p["qty"], avg=p["avg"], pnl=round(pnl, 4))
                    log.info("[live] settled %s up=%s qty=%+.2f avg=%.3f pnl=%+.2f", m.slug, up, p["qty"], p["avg"], pnl)
                else:
                    ss = self.st["shadow_stats"]; ss["settled"] += 1; ss["won"] += pnl > 0; ss["pnl"] += pnl
                    self._event("shadow_settled", slug=m.slug, up=bool(up), qty=p["qty"], avg=p["avg"], pnl=round(pnl, 4))
            self._save()

    # ---- sender thread --------------------------------------------------------------------
    def _sender(self):
        while True:
            try: kind, sig, t_sig = self.out.get(timeout=20)
            except queue.Empty:
                self.ex.call("GET", "/v1/account/balances", timeout=4.0)       # keeps the connection open, so an order does not pay for a new one
                continue
            try:
                if kind == "entry": self._entry(sig, t_sig)
                else: self._exit(sig, t_sig)
            except Exception as e:
                log.exception("[live] sender: %s", e); self._event("error", where=kind, error=f"{type(e).__name__}: {e}")
                if self.ex.armed(): self._halt(f"error while handling an {kind}: {type(e).__name__}: {e}")

    def _qty(self, m, q):
        step = m.min_qty or 0.01
        return round(math.floor(q / step + 1e-9) * step, 6)

    def _entry(self, sig, t_sig):
        slug, side, lim, q, ts, tag = sig; m = self.engine.mf.markets.get(slug); armed = self.ex.armed()
        age = (time.perf_counter() - t_sig) * 1000.0; ss = self.st["shadow_stats"]
        if m is None: return
        q = self._qty(m, q)
        base = {"slug": slug, "side": side, "px": lim, "qty": q, "tag": tag, "age_ms": round(age, 1), "mode": self.mode()}
        if q < (m.min_qty or 0.01): self._event("skip", why="size below the minimum", **base); return
        if age > self.cfg["max_signal_age_ms"]: self._event("skip", why="signal too old", **base); return
        with self.lock: why = self.blocked(armed)
        if why: self._event("skip", why=why, **base); return
        if slug in self.st["pos"] or slug in self.st["shadow"]: return
        body = order_body(slug, side, lim, q)
        if not armed:
            ss["signals"] += 1
            code, js, ms = self.ex.preview(body); self.lat_ms.append(ms)
            still = (m.best_ask is not None and m.best_ask <= lim + 1e-9) if side == "buy" else (m.best_bid is not None and m.best_bid >= lim - 1e-9)
            ok = code == 200
            if ok: ss["previews_ok"] += 1
            if ok and still:
                ss["still_there"] += 1; fee = round(self.cfg["theta"] * q * lim * (1 - lim), 2)
                with self.lock: self.st["shadow"][slug] = {"qty": q if side == "buy" else -q, "avg": lim, "fee": fee, "ts": time.time()}; self.st["day_bets"] += 1; self._save()
            self._event("shadow", code=code, ms=round(ms, 1), still_there=still, answer=None if ok else str(js)[:300], **base)
            return
        # ---- a real order ----
        key = uuid.uuid4().hex
        with self.lock: self.st["day_bets"] += 1; self.st["bets"] += 1; self._save()
        code, js, ms = self.ex.place(body, key); self.lat_ms.append(ms)
        self._event("order", code=code, ms=round(ms, 1), key=key, answer=js if code != 200 else {"id": js.get("id") if isinstance(js, dict) else None, "executions": len((js or {}).get("executions") or []) if isinstance(js, dict) else None}, **base)
        self._after_order(m, slug, side, code, js, opening=True)

    def _exit(self, sig, t_sig):
        slug, side, px, q, ts, tag = sig; m = self.engine.mf.markets.get(slug); armed = self.ex.armed()
        if m is None: return
        book = "pos" if armed else "shadow"; p = self.st[book].get(slug)
        if not p: return
        q = self._qty(m, min(q, abs(p["qty"])))
        if q <= 0: return
        base = {"slug": slug, "side": side, "px": px, "qty": q, "tag": tag, "mode": self.mode()}
        if not armed:          # shadow: the paper exit at the quote seen
            fee = round(self.cfg["theta"] * q * px * (1 - px), 2); sgn = 1 if p["qty"] > 0 else -1
            with self.lock:
                p["realized"] = p.get("realized", 0.0) + sgn * q * (px - p["avg"]); p["fee"] = p.get("fee", 0.0) + fee; p["qty"] -= sgn * q
                if abs(p["qty"]) < 1e-9: p["qty"] = 0.0
                self._save()
            self._event("shadow_exit", **base); return
        key = uuid.uuid4().hex
        code, js, ms = self.ex.place(order_body(slug, "sell" if p["qty"] > 0 else "buy", px, q, close=True), key); self.lat_ms.append(ms)
        self._event("exit_order", code=code, ms=round(ms, 1), key=key, answer=js if code != 200 else None, **base)
        self._after_order(m, slug, side, code, js, opening=False)

    def _after_order(self, m, slug, side, code, js, opening):
        """Work out what the order did, then check it against the position the exchange reports. The exchange is the truth."""
        fill = parse_fill(js) if code == 200 else None
        if code == 200 and fill is None and isinstance(js, dict) and js.get("id"):
            time.sleep(0.3); c2, j2, _ = self.ex.order(js["id"]); fill = parse_fill(j2) if c2 == 200 else None
        if code in (400, 401, 403, 404, 422, 429) and fill is None:
            self.errors += 1; fill = (0.0, 0.0, 0.0, "refused")       # refused before the book: nothing was placed
            if self.errors >= self.cfg["max_errors"]: self._halt(f"{self.errors} refused orders in a row; last: {str(js)[:200]}")
        elif code == 200: self.errors = 0
        time.sleep(0.25); real = self.ex.positions()
        if real is None: time.sleep(1.0); real = self.ex.positions()
        with self.lock:
            p = self.st["pos"].get(slug); had = p["qty"] if p else 0.0
            sgn_order = 1 if side == "buy" else -1                  # the direction this order moves the position
            told = had + sgn_order * fill[0] if fill is not None else None
            if real is None:
                self._halt(f"could not read the position back after an order in {slug}")
                if told is None: return
                now_q = told
            else:
                # the exchange's figure is used as a size only: the direction is the bot's own (the sign the exchange
                # gives a Down position has not been seen yet)
                size = abs(real.get(slug, 0.0)); now_q = size * (1 if (had + sgn_order * size if opening else had) >= 0 else -1) if size else 0.0
                if told is not None and abs(abs(told) - size) > 1e-6:
                    self._halt(f"{slug}: the answer says the position is {told:+.4f}, the exchange reports a size of {size}")
                    self._event("mismatch", slug=slug, fill=fill, told=told, reported=size); now_q = told       # keep the answer's figure so the result is still booked
                elif told is not None: now_q = told
            d = now_q - had                                   # what the order did, in signed YES contracts
            if abs(d) > 1e-9:
                px = fill[1] if fill and fill[1] > 0 else (m.best_ask if d > 0 else m.best_bid) or 0.5
                fee = fill[2] if fill else round(self.cfg["theta"] * abs(d) * px * (1 - px), 2)
                self.st["fills"] += 1; self.st["fees"] += fee
                if opening or not p: self.st["pos"][slug] = {"qty": now_q, "avg": px, "fee": fee, "ts": time.time()}
                else:
                    sgn = 1 if had > 0 else -1
                    p["realized"] = p.get("realized", 0.0) + sgn * abs(d) * (px - p["avg"]); p["fee"] = p.get("fee", 0.0) + fee; p["qty"] = now_q
                self._event("fill", slug=slug, moved=round(d, 4), px=px, fee=fee, position=now_q)
                log.info("[live] FILL %s %+0.2f at %.3f fee %.2f (position %+0.2f)", slug, d, px, fee, now_q)
            self._save()
        if fill is None and code != 200: self.ex.cancel_all([slug])      # unknown outcome: make sure nothing is left resting

    # ---- keeper thread: balance, stray positions, status --------------------------------------
    def _keeper(self):
        ex = type(self.ex)(self.dir); last_bal = 0.0; last_pos = 0.0      # its own connection
        while True:
            try:
                armed = self.ex.armed(); now = time.time()
                if now - last_bal > (20 if armed else 300):
                    b = ex.balance(); last_bal = now
                    if b: self.bal = b; self.bal_ts = now
                with self.lock:
                    if armed and self.st.get("armed_at") is None:
                        self.st["armed_at"] = now; self.st["total_pnl"] = 0.0; self._event("armed", balance=self.bal); log.warning("[live] ARMED: real orders are on"); self._save()
                    if not armed and self.st.get("armed_at") is not None and not self.st["pos"] and self.mode() == "SHADOW": self.st["armed_at"] = None; self._save()
                if armed and now - last_pos > 60:
                    real = ex.positions(); last_pos = now
                    if real is not None:
                        with self.lock:
                            for slug, q in real.items():
                                mine = self.st["pos"].get(slug)
                                if "updown-5m" in slug and (mine is None or abs(abs(mine["qty"]) - abs(q)) > 1e-6) and slug in self.engine.mf.markets:
                                    self._halt(f"{slug}: the exchange holds {q}, the bot's record says {mine['qty'] if mine else 0}")
                with self.lock:
                    for book in ("pos", "shadow"):          # a window that closed while the engine was down
                        for slug in [s for s, p in self.st[book].items() if now - p.get("ts", now) > 1200]:
                            self._event("dropped", book=book, slug=slug, why="closed while the engine was not running; its result is in the balance, not in the bot's totals"); self.st[book].pop(slug)
                self._status()
            except Exception as e: log.exception("[live] keeper: %s", e)
            time.sleep(5)

    def _status(self):
        lat = sorted(self.lat_ms); s = self.st; ss = s["shadow_stats"]; armed = self.ex.armed()
        out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "mode": self.mode(), "assets": list(self.ASSETS), "exit": self.EXIT, "limits": self.cfg,
               "balance": self.bal, "balance_age_s": round(time.time() - self.bal_ts) if self.bal_ts else None, "next_bet_usd": round(self.bet_usd(armed), 2),
               "real": {"open": s["pos"], "bets": s["bets"], "fills": s["fills"], "fees": round(s["fees"], 2), "day": s["day"], "day_bets": s["day_bets"], "day_pnl": round(s["day_pnl"], 2), "total_pnl": round(s["total_pnl"], 2)},
               "shadow": {**ss, "pnl": round(ss["pnl"], 2), "open": s["shadow"]},
               "round_trip_ms": {"n": len(lat), "median": round(lat[len(lat) // 2], 1), "p90": round(lat[int(len(lat) * 0.9)], 1), "max": round(lat[-1], 1)} if lat else None,
               "decisions": dict(self.stats), "events": list(self.events)[:25]}
        tmp = self._path("status.json.tmp")
        with open(tmp, "w") as f: json.dump(out, f, default=str)
        os.replace(tmp, self._path("status.json"))


def load_config(dir_):
    os.makedirs(dir_, exist_ok=True); p = os.path.join(dir_, "live.json"); cfg = dict(DEFAULTS)
    if os.path.exists(p):
        saved = json.load(open(p)); unknown = set(saved) - set(DEFAULTS)
        if unknown: raise ValueError(f"unknown settings in live.json: {sorted(unknown)}")
        cfg.update(saved)
    else:
        with open(p, "w") as f: json.dump(cfg, f, indent=1)
    return cfg


def start(engine, data_dir):
    """Called once by the engine. Returns the bot, or None when it cannot run (no key, or switched off)."""
    if os.environ.get("PAPERBOT_LIVE", "1") == "0" or not ws.available(): return None
    dir_ = os.path.join(data_dir, "live"); cfg = load_config(dir_)
    bot = LiveBot(engine, dir_, cfg); log.info("[live] started in %s mode, coins %s", bot.mode(), bot.ASSETS)
    return bot


if __name__ == "__main__":      # python3 -m paperbot.live : read-only check of the account and of the order format
    import sys
    data = os.environ.get("PAPERBOT_DATA", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "paperbot-data"))
    d = os.path.join(data, "live"); cfg = load_config(d); ex = Exchange(d)
    print("key set:", ws.available(), "| armed:", ex.armed(), "| limits:", cfg)
    print("balance:", ex.balance()); print("positions:", ex.positions())
    try: print("status:", json.dumps({k: v for k, v in json.load(open(os.path.join(d, "status.json"))).items() if k not in ("events", "limits")}, indent=1))
    except (OSError, ValueError): print("status: none yet")
    if len(sys.argv) > 2 and sys.argv[1] == "preview":
        slug = sys.argv[2]
        for side, close, px in (("buy", False, 0.50), ("sell", False, 0.50), ("sell", True, 0.50), ("buy", True, 0.50)):
            c, js, ms = ex.preview(order_body(slug, side, px, 1, close)); o = js.get("order", {}) if isinstance(js, dict) else js
            print(side, "close" if close else "open", c, round(ms), "ms", {k: o.get(k) for k in ("intent", "side", "outcomeSide", "action", "price", "tif")} if isinstance(o, dict) else o)
