"""Real-money execution on Polymarket US. OFF unless deliberately armed.

By default this runs in *shadow* mode: one bot trades exactly as in paper
trading, and every order it would have sent is written to a log, but nothing is
sent. Real orders go out only when ALL of these hold:

1. the config file says ``"armed": true`` and names exactly one bot;
2. the environment variable ``PSB_LIVE_CONFIRM`` is ``real-money``;
3. ``POLYMARKET_KEY_ID`` and ``POLYMARKET_SECRET_KEY`` are set (from
   polymarket.us/developer; keep them in your own shell, never in a file here);
4. no ``STOP`` file exists next to the live ledger;
5. the account has no open orders when the run starts.

Hard limits apply to every real order: dollars per order, total dollars at risk
for the bot, and a daily loss stop that cancels everything and halts. The run
cancels its own open orders when it exits, including on errors and Ctrl-C.

One account holds one position per market, so only one bot can trade live per
account. API reference: https://docs.polymarket.us/api-reference/introduction.md
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import fees, usmarket
from .bots import BOTS, Rest, Take
from .paper import DEFAULT_LEAGUES, Engine, Ledger, Tracked, _iso, _now, report

API = "https://api.polymarket.us"
CONFIRM_ENV, CONFIRM_VALUE = "PSB_LIVE_CONFIRM", "real-money"
KEY_ENV, SECRET_ENV = "POLYMARKET_KEY_ID", "POLYMARKET_SECRET_KEY"
STATE = Path(__file__).resolve().parent.parent / "live-results"
MIN_PRICE, MAX_PRICE = 0.01, 0.99          # exchange price bounds (long side)
TERMINAL = {"ORDER_STATE_FILLED", "ORDER_STATE_CANCELED", "ORDER_STATE_REJECTED",
            "ORDER_STATE_EXPIRED", "ORDER_STATE_REPLACED"}


class LiveError(RuntimeError):
    pass


# --- configuration and arming -------------------------------------------------

@dataclass(frozen=True)
class Limits:
    max_order_usd: float = 5.0        # dollars per order
    max_exposure_usd: float = 50.0    # worst-case loss of positions + open orders
    max_daily_loss_usd: float = 25.0  # halt when today's P&L falls below -this


@dataclass(frozen=True)
class LiveConfig:
    armed: bool = False
    bot: str | None = None
    bankroll_usd: float = 1000.0      # what the bot sizes from, as in paper trading
    limits: Limits = field(default_factory=Limits)

    @classmethod
    def load(cls, path: Path) -> "LiveConfig":
        d = json.loads(path.read_text())
        known = {"armed", "bot", "bankroll_usd", "limits"}
        if set(d) - known:
            raise LiveError(f"unknown config keys: {sorted(set(d) - known)}")
        lim = d.get("limits") or {}
        bad = set(lim) - set(Limits.__dataclass_fields__)
        if bad:
            raise LiveError(f"unknown limit keys: {sorted(bad)}")
        limits = Limits(**{k: float(v) for k, v in lim.items()})
        if min(limits.max_order_usd, limits.max_exposure_usd, limits.max_daily_loss_usd) <= 0:
            raise LiveError("every limit must be positive")
        if not isinstance(d.get("armed", False), bool):
            raise LiveError('"armed" must be true or false')
        bot = d.get("bot")
        if bot is not None and bot not in BOTS:
            raise LiveError(f"unknown bot {bot!r}; choose one of {sorted(BOTS)}")
        return cls(d.get("armed", False), bot, float(d.get("bankroll_usd", 1000.0)), limits)


def arm_check(cfg: LiveConfig, env: dict, stop_file: Path) -> list[str]:
    """Reasons real orders must not be sent; empty means armed."""
    why = []
    if not cfg.armed:
        why.append('config has "armed": false')
    if not cfg.bot:
        why.append("config names no bot")
    if env.get(CONFIRM_ENV) != CONFIRM_VALUE:
        why.append(f"{CONFIRM_ENV} is not set to {CONFIRM_VALUE!r}")
    if not env.get(KEY_ENV) or not env.get(SECRET_ENV):
        why.append(f"{KEY_ENV} / {SECRET_ENV} are not set")
    if stop_file.exists():
        why.append(f"stop file {stop_file} exists")
    return why


# --- authenticated client -------------------------------------------------------

class Signer:
    """Ed25519 request signing: base64(sign(timestamp_ms + METHOD + path))."""

    def __init__(self, key_id: str, secret_b64: str):
        try:
            from cryptography.hazmat.primitives.asymmetric import ed25519
        except ImportError as e:  # only needed for real trading
            raise LiveError("real trading needs the 'cryptography' package: "
                            "pip install cryptography") from e
        raw = base64.b64decode(secret_b64)
        if len(raw) not in (32, 64):
            raise LiveError("secret key must be a base64 Ed25519 key (32 or 64 bytes)")
        self.key_id = key_id
        self._key = ed25519.Ed25519PrivateKey.from_private_bytes(raw[:32])

    def headers(self, method: str, path: str, ts_ms: int | None = None) -> dict[str, str]:
        ts = str(ts_ms if ts_ms is not None else int(time.time() * 1000))
        sig = self._key.sign(f"{ts}{method}{path}".encode())
        return {"X-PM-Access-Key": self.key_id, "X-PM-Timestamp": ts,
                "X-PM-Signature": base64.b64encode(sig).decode()}


def _urllib_transport(method: str, url: str, headers: dict, body: bytes | None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode()
            return resp.status, (json.loads(text) if text else {})
    except urllib.error.HTTPError as e:
        text = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(text) if text else {}
        except json.JSONDecodeError:
            return e.code, {"message": text[:500]}


class UsClient:
    """The few authenticated Polymarket US endpoints the runner needs."""

    def __init__(self, signer: Signer, transport=_urllib_transport, base: str = API):
        self.signer, self.transport, self.base = signer, transport, base
        self._last = 0.0

    def _call(self, method: str, path: str, body: dict | None = None,
              idem: str | None = None) -> dict:
        wait = self._last + 1 / 10 - time.monotonic()   # stay far under 25 req/s
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        headers = {"Content-Type": "application/json", "User-Agent": "psb/0.1",
                   **self.signer.headers(method, path)}
        if idem:
            headers["Idempotency-Key"] = idem
        data = json.dumps(body).encode() if body is not None else None
        status, out = self.transport(method, self.base + path, headers, data)
        if status == 429:
            time.sleep(1.0)
            status, out = self.transport(method, self.base + path,
                                         {**headers, **self.signer.headers(method, path)}, data)
        if not 200 <= status < 300:
            raise LiveError(f"{method} {path} -> {status}: {str(out)[:300]}")
        return out

    def create_order(self, body: dict) -> dict:
        return self._call("POST", "/v1/orders", body, idem=str(uuid.uuid4()))

    def get_order(self, order_id: str) -> dict:
        out = self._call("GET", f"/v1/order/{order_id}")
        return out.get("order", out)

    def cancel_order(self, order_id: str, slug: str) -> None:
        self._call("POST", f"/v1/order/{order_id}/cancel", {"marketSlug": slug})

    def cancel_open(self, slugs: list[str]) -> None:
        if slugs:
            self._call("POST", "/v1/orders/open/cancel", {"slugs": sorted(set(slugs))})

    def open_orders(self) -> list[dict]:
        return self._call("GET", "/v1/orders/open").get("orders") or []

    def buying_power(self) -> float | None:
        for b in self._call("GET", "/v1/account/balances").get("balances") or []:
            if b.get("currency", "USD") == "USD":
                return _num(b.get("buyingPower"))
        return None


def _num(x) -> float | None:
    if isinstance(x, dict):
        x = x.get("value")
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# --- order construction ---------------------------------------------------------

def long_price(side: str, side_price: float, tick: float) -> float:
    """The order's price.value: always the long (YES) side, aligned to the tick so
    the order never pays more than `side_price` for `side`."""
    if side == "long":
        p = math.floor(side_price / tick + 1e-9) * tick
    else:   # buying the short side at q is a long-side price of 1 - q, rounded up
        p = math.ceil((1 - side_price) / tick - 1e-9) * tick
    p = round(p, 6)
    if not MIN_PRICE <= p <= MAX_PRICE:
        raise LiveError(f"price {p} outside {MIN_PRICE}-{MAX_PRICE}")
    return p


def side_price_of(side: str, long_px: float) -> float:
    return long_px if side == "long" else round(1 - long_px, 6)


def order_body(slug: str, side: str, side_price: float, contracts: int, tick: float,
               taker: bool) -> dict:
    if contracts < 1:
        raise LiveError("order below one contract")
    body = {
        "marketSlug": slug,
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": f"{long_price(side, side_price, tick):.4f}".rstrip("0").rstrip("."),
                  "currency": "USD"},
        "quantity": contracts,
        "intent": "ORDER_INTENT_BUY_LONG" if side == "long" else "ORDER_INTENT_BUY_SHORT",
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
    }
    if taker:   # take what is there now, cancel the rest
        body.update(tif="TIME_IN_FORCE_IMMEDIATE_OR_CANCEL", synchronousExecution=True,
                    maxBlockTime="2")
    else:       # rest only; rejected rather than crossing the book
        body.update(tif="TIME_IN_FORCE_GOOD_TILL_CANCEL", participateDontInitiate=True)
    return body


@dataclass
class LiveOrder:
    order_id: str
    slug: str
    side: str
    side_price: float
    contracts: float
    tag: str
    filled: float = 0.0


# --- runner ---------------------------------------------------------------------

class LiveEngine(Engine):
    """Runs one bot. Shadow mode (client None) fills on paper and logs the orders
    it would send; armed mode sends them under hard limits."""

    def __init__(self, ledger: Ledger, leagues: list[str], taker_theta: float, log,
                 horizon_h: float, bot_key: str, limits: Limits, client: UsClient | None,
                 intents_path: Path, day_path: Path | None = None):
        super().__init__(ledger, leagues, taker_theta, log, horizon_h)
        self.bots = {bot_key: BOTS[bot_key]}
        self.key, self.limits, self.client = bot_key, limits, client
        self.intents_path = intents_path
        self.live: dict[str, LiveOrder] = {}
        self.halted: str | None = None
        self.buying_power: float | None = None
        # The daily loss stop counts from the first run of the UTC day, not from
        # each restart, so stopping and starting cannot reset it.
        self.day_path = day_path
        self.day, self.day_start_pnl = _now().date().isoformat(), self._pnl()
        if day_path and day_path.exists():
            d = json.loads(day_path.read_text())
            if d.get("date") == self.day:
                self.day_start_pnl = float(d["start_pnl"])
        self._save_day()

    def _save_day(self) -> None:
        if self.day_path:
            self.day_path.parent.mkdir(parents=True, exist_ok=True)
            self.day_path.write_text(json.dumps({"date": self.day, "start_pnl": self.day_start_pnl}))

    @property
    def armed(self) -> bool:
        return self.client is not None

    # -- accounting
    def _pnl(self) -> float:
        """Settled P&L plus open positions at the US mid (at entry when unmarked)."""
        total = 0.0
        for slug, p in self.ledger.positions.get(self.key, {}).items():
            if slug in self.ledger.settled:
                total += p.cash + p.contracts * self.ledger.settled[slug]
            elif slug in self.ledger.marks:
                total += p.cash + p.contracts * self.ledger.marks[slug]
            else:
                total -= p.fees
        return total

    def _exposure(self) -> float:
        """Worst-case loss of open positions plus the cost of our open orders."""
        lost = self.ledger.bankroll - self.ledger.available_cash(self.key)
        lost += sum((o.contracts - o.filled) * o.side_price for o in self.live.values())
        return max(0.0, lost)

    def cash_for(self, key: str) -> float:
        cash = min(self.ledger.available_cash(key),
                   self.limits.max_exposure_usd - self._exposure())
        if self.buying_power is not None:
            cash = min(cash, self.buying_power)
        return max(0.0, cash)

    def check_daily_loss(self) -> None:
        today = _now().date().isoformat()
        if today != self.day:
            self.day, self.day_start_pnl = today, self._pnl()
            self._save_day()
        loss = self.day_start_pnl - self._pnl()
        if loss >= self.limits.max_daily_loss_usd and not self.halted:
            self.halt(f"daily loss ${loss:.2f} reached the ${self.limits.max_daily_loss_usd:.2f} limit")

    def halt(self, why: str) -> None:
        self.halted = why
        self.log(f"HALT: {why}")
        self.cancel_all()

    def cancel_all(self) -> None:
        if not self.armed or not self.live:
            self.live.clear()
            return
        slugs = [o.slug for o in self.live.values()]
        try:
            self.client.cancel_open(slugs)
        except LiveError as e:   # keep the ids so a retry or the next run can see them
            self.log(f"cancel failed, orders may still be open on {sorted(set(slugs))}: {e}")
            return
        for oid, o in list(self.live.items()):   # record fills that raced the cancel
            self._sync(oid, o, _now(), final=True)

    def _sync(self, oid: str, o: LiveOrder, now: datetime, final: bool = False) -> None:
        """Record new fills of one of our resting orders; forget it once done."""
        try:
            d = self.client.get_order(oid)
        except LiveError as e:
            self.log(f"order {oid}: {e}")
            if final:
                del self.live[oid]
            return
        cum = _num(d.get("cumQuantity")) or 0.0
        if cum > o.filled + 1e-9:
            f = self.ledger.record(self.key, o.slug, o.side, o.side_price, cum - o.filled, 0.0,
                                   "maker", o.tag, None, now)
            self.bots[self.key].on_fill(self.ledger.state(self.key, o.slug), f, now)
            self.stats["maker_fills"] += 1
            o.filled = cum
        if final or d.get("state") in TERMINAL or o.filled >= o.contracts - 1e-9:
            del self.live[oid]

    def _cancel(self, slug: str, bot: str | None = None) -> None:
        super()._cancel(slug, bot)
        if not self.armed:
            return
        for oid, o in list(self.live.items()):
            if o.slug == slug:
                self._cancel_one(oid, o)

    def _cancel_one(self, oid: str, o: LiveOrder) -> bool:
        try:
            self.client.cancel_order(oid, o.slug)
        except LiveError as e:
            self.log(f"cancel {oid}: {e}")
            return False
        self._sync(oid, o, _now(), final=True)
        return True

    def _intent(self, kind: str, slug: str, side: str, side_price: float, contracts: float,
                tag: str, now: datetime, sent: bool, order_id: str | None = None) -> None:
        self.intents_path.parent.mkdir(parents=True, exist_ok=True)
        with self.intents_path.open("a") as f:
            f.write(json.dumps({"t": _iso(now), "bot": self.key, "slug": slug, "kind": kind,
                                "side": side, "price": round(side_price, 4),
                                "contracts": round(contracts, 4), "tag": tag, "sent": sent,
                                "order_id": order_id}) + "\n")

    # -- engine hooks
    def _settle_orders(self, t: Tracked, bk: usmarket.Book, now: datetime) -> None:
        if not self.armed:
            return super()._settle_orders(t, bk, now)
        for oid, o in list(self.live.items()):
            if o.slug == t.m.slug:
                self._sync(oid, o, now)

    def _apply(self, key, bot, t: Tracked, bk, fair_long, acts, now) -> None:
        if self.halted:
            return
        if not self.armed:
            for a in acts:
                px = a.limit if isinstance(a, Take) else a.price
                self._intent("take" if isinstance(a, Take) else "rest", t.m.slug, a.side, px,
                             a.dollars / px if px > 0 else 0, a.tag, now, sent=False)
            return super()._apply(key, bot, t, bk, fair_long, acts, now)
        self._apply_live(bot, t, fair_long, acts, now)

    def _size(self, dollars: float, price: float) -> int:
        dollars = min(dollars, self.limits.max_order_usd, self.cash_for(self.key))
        return int(math.floor(dollars / price + 1e-9)) if price > 0 else 0

    def _apply_live(self, bot, t: Tracked, fair_long, acts, now) -> None:
        slug, tick = t.m.slug, t.m.tick or 0.01
        st = self.ledger.state(self.key, slug)
        wanted = {a.side: a for a in acts if isinstance(a, Rest)}
        # Keep resting orders the bot re-issued at the same price; cancel the rest.
        for oid, o in list(self.live.items()):
            if o.slug != slug:
                continue
            w = wanted.get(o.side)
            if w and abs(w.price - o.side_price) < tick / 2:
                wanted.pop(o.side)
                continue
            if not self._cancel_one(oid, o):
                return   # do not stack new orders on one we could not cancel
        for a in acts:
            if isinstance(a, Take):
                self._take(bot, slug, tick, st, a, fair_long, now)
        for a in wanted.values():
            self._rest(slug, tick, a, now)

    def _take(self, bot, slug, tick, st, a: Take, fair_long, now) -> None:
        n = self._size(a.dollars, a.limit)
        if n < 1:
            self.stats["no_cash"] += 1
            return
        body = order_body(slug, a.side, a.limit, n, tick, taker=True)
        try:
            out = self.client.create_order(body)
        except LiveError as e:
            self.log(f"take {slug} {a.side}: {e}")
            return
        self._intent("take", slug, a.side, a.limit, n, a.tag, now, sent=True, order_id=out.get("id"))
        fair = fair_long if a.side == "long" else 1 - fair_long
        for ex in out.get("executions") or []:
            qty = _num(ex.get("lastShares")) or 0.0
            px = _num(ex.get("lastPx"))
            if qty <= 0 or px is None:
                continue
            side_px = side_price_of(a.side, px)
            fee = _num(ex.get("commissionNotionalCollected"))
            if fee is None:
                fee = fees.taker_fee(qty, side_px, self.theta)
            f = self.ledger.record(self.key, slug, a.side, side_px, qty, fee, "taker", a.tag, fair, now)
            bot.on_fill(st, f, now)
            self.stats["takes"] += 1

    def _rest(self, slug, tick, a: Rest, now) -> None:
        n = self._size(a.dollars, a.price)
        if n < 1:
            self.stats["no_cash"] += 1
            return
        body = order_body(slug, a.side, a.price, n, tick, taker=False)
        try:
            out = self.client.create_order(body)
        except LiveError as e:
            self.log(f"rest {slug} {a.side}: {e}")
            return
        oid = out.get("id")
        if not oid:
            self.log(f"rest {slug} {a.side}: no order id in {out}")
            return
        price = side_price_of(a.side, float(body["price"]["value"]))
        self.live[oid] = LiveOrder(oid, slug, a.side, price, n, a.tag)
        self._intent("rest", slug, a.side, price, n, a.tag, now, sent=True, order_id=oid)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run one bot live on Polymarket US. Shadow mode "
                                            "(no orders sent) unless fully armed.")
    p.add_argument("--config", default="live.json")
    p.add_argument("--minutes", type=float, default=60.0)
    p.add_argument("--fast", type=float, default=5.0)
    p.add_argument("--slow", type=float, default=60.0)
    p.add_argument("--discover", type=float, default=900.0)
    p.add_argument("--horizon", type=float, default=24.0)
    p.add_argument("--leagues", nargs="*", default=DEFAULT_LEAGUES)
    p.add_argument("--bot", help="shadow mode only: bot to run if the config names none")
    p.add_argument("--state", default=str(STATE))
    a = p.parse_args(argv)
    cfg = LiveConfig.load(Path(a.config))
    state = Path(a.state)
    stop = state / "STOP"
    bot = cfg.bot or a.bot
    if not bot:
        raise SystemExit("name a bot in the config (or --bot for shadow mode)")
    why = arm_check(cfg, dict(os.environ), stop)
    if cfg.armed and why:
        raise SystemExit("config is armed but real trading is blocked: " + "; ".join(why))
    armed = not why
    log_path = state / "live.log"

    def log(msg: str) -> None:
        line = f"{_iso(_now())} {msg}"
        print(line, flush=True)
        state.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as f:
            f.write(line + "\n")

    client = None
    if armed:
        client = UsClient(Signer(os.environ[KEY_ENV], os.environ[SECRET_ENV]))
        open_now = client.open_orders()
        if open_now:
            raise SystemExit(f"account has {len(open_now)} open orders; cancel them first so "
                             "the bot only manages its own")
    ledger_path = state / ("ledger.json" if armed else "shadow-ledger.json")
    led = Ledger.load(ledger_path, cfg.bankroll_usd)
    eng = LiveEngine(led, a.leagues, fees.WORST_TAKER_THETA, log, a.horizon, bot, cfg.limits,
                     client, state / ("orders.jsonl" if armed else "shadow-orders.jsonl"),
                     state / ("day.json" if armed else "shadow-day.json"))
    log(f"{'ARMED: REAL MONEY' if armed else 'SHADOW: no orders sent'}; bot {bot}; "
        f"limits {cfg.limits}" + ("" if armed else f"; not armed because: {'; '.join(why)}"))
    end = time.monotonic() + a.minutes * 60
    next_discover = next_settle = next_save = 0.0
    try:
        while time.monotonic() < end and not eng.halted:
            if stop.exists():
                eng.halt("stop file found")
                break
            mono = time.monotonic()
            if mono >= next_discover:
                eng.discover_next()
                next_discover = mono + a.discover / max(1, len(eng.leagues))
            if armed:
                eng.buying_power = client.buying_power()
            eng.poll(a.fast, a.slow)
            eng.check_daily_loss()
            if mono >= next_settle:
                eng.settle()
                next_settle = mono + 600
            if mono >= next_save:
                led.save(ledger_path)
                log(f"stats {json.dumps(eng.stats)} open_orders={len(eng.live)} "
                    f"exposure=${eng._exposure():.2f}")
                next_save = mono + 120
            time.sleep(1.0)
    finally:
        eng.cancel_all()
        led.save(ledger_path)
    print(report(led))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
