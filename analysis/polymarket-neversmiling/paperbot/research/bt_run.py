"""Minute-price backtest of twenty buy-once-and-hold rules (user, 11-16) on what Polymarket US keeps of its closed
windows (research/bt_fetch.py) and Coinbase one-minute candles (research/bt_candles.py).

This is NOT a replay of the live bots. Polymarket keeps one quote a minute for a closed window (the price to buy Up
and the price to buy Down), so each rule is rewritten here to look once a minute:

  coin price   the close of the last finished Coinbase minute, moved by the gap between Coinbase and the window's
               starting line in the minute before the window (the live model corrects the same gap)
  volatility   the spread of the last 30 one-minute moves, per second
  model        as paperbot/fair.py more than a minute out: the settlement is the average of the last minute, the coin
               a random walk until then; z = (price - line) / sd, chance of Up = phi(z)
  fill         at the stored quote of that minute, any size, plus the fee (0.095 * p * (1 - p) a contract); a second
               figure pays 1c more
  stake        $10 a bet (1% of $1,000, not compounded); "bet by the odds" scales it 0.2-3 times as the live bot does

What that leaves out: quotes between the minutes, how much size was on offer, and every rule that rests an order or
acts within a second (pickoff, arbitrage, v_loose_rest, the committee). The near-certain rules rest a bid live; here
they take the offer, which is a different and worse trade.

    python3 research/bt_run.py            (from the paperbot directory; writes paperbot-data/backtest/results.json)
"""
import os, sys, json, math, pickle, sqlite3, statistics, collections, time

D = os.path.join("paperbot-data", "backtest"); THETA = 0.095; STAKE = 10.0; ALL = ("btc", "eth", "sol", "xrp")
C = pickle.load(open(os.path.join(D, "candles.pkl"), "rb"))
phi = lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2)))
fee = lambda p: THETA * p * (1 - p)


def close(a, t):
    """Close of the last Coinbase minute finished at or before t (looking back up to 5 minutes)."""
    m = int(t) // 60 * 60 - 60
    for k in range(5):
        c = C[a].get(m - 60 * k)
        if c: return c[3]
    return None

_sig = {}
def sigma(a, t, n=30):
    """Per-second volatility from the last n one-minute moves before t."""
    m = int(t) // 60 * 60; key = (a, m, n)
    if key in _sig: return _sig[key]
    px = [close(a, m - 60 * k) for k in range(n + 1)]
    r = [math.log(px[i] / px[i + 1]) for i in range(n) if px[i] and px[i + 1]]
    v = max(statistics.pstdev(r) / math.sqrt(60), 1e-5) if len(r) >= 10 else None
    _sig[key] = v; return v

def heat(a, t):
    s = sigma(a, t, 10); past = [x for x in (sigma(a, t - 60 * k, 10) for k in range(1, 31)) if x]
    return s / statistics.median(past) if s and len(past) >= 20 else None


class W:
    def __init__(self, slug, a, hz, start, dur, p2b, settle, pts):
        self.slug = slug; self.a = a; self.hz = hz; self.start = start; self.dur = dur; self.p2b = p2b; self.settle = settle
        self.q = {t - start: (l, s) for t, l, s in pts if l is not None and s is not None}
        self.up = settle >= p2b
        c = C[a].get(start - 60); self.basis = ((c[0] + c[1] + c[2] + c[3]) / 4 - p2b) if c else None
        a0, b0 = close(a, start - 900), close(a, start); s = sigma(a, start)
        self.mv = (b0 / a0 - 1) / (s * 30) if a0 and b0 and s else None          # the quarter hour before, in usual moves
        self._m = {}

    def model(self, el):
        """(chance of Up, z) at `el` seconds into the window, or None."""
        if el in self._m: return self._m[el]
        t = self.start + el; px = close(self.a, t); s = sigma(self.a, t); out = None
        if px and s and self.basis is not None:
            f = max(0.0, self.dur - 60 - el); sd = s * self.p2b * math.sqrt(f + 20.0)
            z = (px - self.basis - self.p2b) / sd; out = (phi(z), z)
        self._m[el] = out; return out


# ---- the rules: f(w, el, group) -> True (Up) / False (Down) / None; group = the same-time windows of the other coins ----
def by_model(w, el, g): m = w.model(el); return None if m is None else m[0] >= 0.5
def rules(lead=0.3, big=1.0):
    def f(w, el, g):
        m = w.model(el)
        if m is None or abs(m[1]) < lead: return None
        up = m[1] > 0
        if w.mv is not None and abs(w.mv) >= big and (w.mv > 0) == up: return None
        return up
    return f
def against(lead=0.3, big=1.5):
    def f(w, el, g):
        m = w.model(el)
        if m is None or w.mv is None or abs(w.mv) < big: return None
        up = w.mv < 0
        return up if (m[1] if up else -m[1]) >= -lead else None
    return f
def fade(big):
    return lambda w, el, g: None if w.mv is None or abs(w.mv) < big else w.mv < 0
def favourite(w, el, g):
    q = w.q.get(el)
    return None if not q else q[0] >= q[1]
def odd_one(w, el, g):
    m = w.model(el); o = [x.model(el) for x in g if x is not w]
    if m is None or len(o) < 3 or any(x is None for x in o) or abs(m[1]) > 0.3: return None
    if all(x[1] >= 0.8 for x in o): return True
    if all(x[1] <= -0.8 for x in o): return False
    return None
def hot_gate(w, el, g):
    h = {a: heat(a, w.start + el) for a in ALL}
    return all(h.values()) and max(h, key=h.get) == w.a and h[w.a] >= 1.1
def kelly(w, el, up, price):
    p = w.model(el)[0]; e = (p if up else 1 - p) - price - fee(price)
    return max(0.2, min(3.0, 0.25 * e / max(1 - price, 0.02) / 0.01)) if e > 0 else 0.0

# name: (what it does, window seconds, coins, seconds left it may bet between (hi, lo), price band, edge, rule, gate, scale)
B = collections.OrderedDict([
 ("one_buy_mid_window", ("the model's side with a 3c edge at 45-75c, minutes 2-3 of a 5-minute window", 300, ALL, (225, 75), (0.45, 0.75), 0.03, by_model, None, None)),
 ("one_buy_mid_window_5c_edge", ("the same, needing 5c", 300, ALL, (225, 75), (0.45, 0.75), 0.05, by_model, None, None)),
 ("one_buy_mid_window_bitcoin_ether", ("the same on Bitcoin and Ether", 300, ("btc", "eth"), (225, 75), (0.45, 0.75), 0.03, by_model, None, None)),
 ("one_buy_mid_window_15_minute", ("the model's side with a 3c edge at 45-75c, middle half of a 15-minute window", 900, ALL, (675, 225), (0.45, 0.75), 0.03, by_model, None, None)),
 ("bet_by_the_odds_4c_edge", ("the model's side at a 4c edge, 10-90c, stake sized by the odds, 5-minute", 300, ALL, (240, 60), (0.10, 0.90), 0.04, by_model, None, kelly)),
 ("bet_by_the_odds_15_minute", ("the same in 15-minute windows", 900, ALL, (780, 120), (0.10, 0.90), 0.04, by_model, None, kelly)),
 ("all_the_rules", ("the side ahead at 50-70c in the second half, never with a big move of the last quarter hour", 300, ALL, (150, 30), (0.50, 0.70), None, rules(), None, None)),
 ("all_the_rules_bitcoin", ("the same on Bitcoin", 300, ("btc",), (150, 30), (0.50, 0.70), None, rules(), None, None)),
 ("all_the_rules_15_minute", ("the same in the second half of a 15-minute window", 900, ALL, (450, 90), (0.50, 0.70), None, rules(), None, None)),
 ("side_ahead_at_50_to_70c", ("the side ahead at 50-70c, any time after the first quarter", 300, ALL, (225, 30), (0.50, 0.70), None, rules(big=1e9), None, None)),
 ("side_ahead_late", ("the side ahead at 60-90c with a minute left", 300, ALL, (75, 30), (0.60, 0.90), None, rules(big=1e9), None, None)),
 ("against_the_quarter_hour_not_behind", ("against a big move of the last quarter hour while that side is not behind, 40-65c", 300, ALL, (225, 30), (0.40, 0.65), None, against(), None, None)),
 ("against_the_quarter_hour_15_minute", ("the same in 15-minute windows", 900, ALL, (675, 90), (0.40, 0.65), None, against(), None, None)),
 ("fade_quarter_hour_15_minute", ("15-minute: against the last quarter hour when it moved its usual size, first 5 minutes, 35-60c", 900, ALL, (870, 600), (0.35, 0.60), None, fade(1.0), None, None)),
 ("fade_big_quarter_hour_15_minute", ("15-minute: against a quarter hour of 1.5 times its usual size, 35-62c", 900, ALL, (870, 600), (0.35, 0.62), None, fade(1.5), None, None)),
 ("near_certain_last_minute_taken", ("takes the side priced 90-98c with a minute left (live it rests a bid instead)", 300, ALL, (75, 30), (0.90, 0.98), None, favourite, None, None)),
 ("near_certain_last_two_minutes_taken", ("takes the side priced 90-98c in the last two minutes", 300, ALL, (135, 30), (0.90, 0.98), None, favourite, None, None)),
 ("near_certain_15_minute_taken", ("takes the side priced 90-98c in the last two minutes of a 15-minute window", 900, ALL, (135, 30), (0.90, 0.98), None, favourite, None, None)),
 ("liveliest_coin", ("only the coin liveliest against its own normal, the model's side at a 3c edge, 25-75c", 300, ALL, (240, 60), (0.25, 0.75), 0.03, by_model, hot_gate, None)),
 ("odd_one_out", ("three coins are past their lines and the fourth is on its line: bets it follows, 30-65c", 300, ALL, (220, 60), (0.30, 0.65), None, odd_one, None, None)),
])
assert len(B) == 20


def main():
    db = sqlite3.connect(f"file:{os.path.join(D, 'hist.db')}?mode=ro", uri=True)
    groups = collections.defaultdict(list); n = 0
    for r in db.execute("select slug,asset,hz,start,dur,p2b,settle,pts from win where found=1 and p2b is not null and settle is not null"):
        w = W(*r[:7], json.loads(r[7]))
        if w.q: groups[(w.dur, w.start)].append(w); n += 1
    days = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0.0, 0, 0]))     # bot -> day -> [pnl, pnl at +1c, bets, won]
    for (dur, start), g in sorted(groups.items()):
        for name, (_, bdur, coins, (hi, lo), band, edge, rule, gate, scale) in B.items():
            if bdur != dur: continue
            for w in g:
                if w.a not in coins: continue
                for el in range(60, dur, 60):
                    left = dur - el
                    if not (lo <= left <= hi) or el not in w.q: continue
                    if gate and not gate(w, el, g): continue
                    up = rule(w, el, g)
                    if up is None: continue
                    price = w.q[el][0] if up else w.q[el][1]
                    if not (band[0] <= price <= band[1]): continue
                    if edge is not None:
                        p = w.model(el)[0]
                        if (p if up else 1 - p) - price - fee(price) < edge: continue
                    m = scale(w, el, up, price) if scale else 1.0
                    if m <= 0: continue
                    d = days[name][time.strftime("%m-%d", time.gmtime(start))]; won = up == w.up
                    for i, px in ((0, price), (1, min(0.99, price + 0.01))):
                        qty = STAKE * m / px; d[i] += qty * ((1.0 if won else 0.0) - px - fee(px))
                    d[2] += 1; d[3] += won
                    break
    alld = sorted({time.strftime("%m-%d", time.gmtime(s)) for _, s in groups})
    out = {"windows": n, "days": alld, "bots": {}}
    print(f"{n} windows, {alld[0]} to {alld[-1]} ({len(alld)} days)")
    print(f"{'bot':38} {'bets':>6} {'won':>6} {'P/L':>9} {'+1c':>9} {'days up':>8} {'worst day':>10} {'1st half':>9} {'2nd half':>9}")
    for name in B:
        dd = days[name]; ds = sorted(dd); tot = sum(v[0] for v in dd.values()); slip = sum(v[1] for v in dd.values())
        bets = sum(v[2] for v in dd.values()); won = sum(v[3] for v in dd.values()); h = len(alld) // 2
        first = sum(dd[d][0] for d in alld[:h] if d in dd); second = sum(dd[d][0] for d in alld[h:] if d in dd)
        upd = sum(1 for d in ds if dd[d][0] > 0); worst = min((dd[d][0] for d in ds), default=0.0)
        out["bots"][name] = {"what": B[name][0], "bets": bets, "won": won, "pnl": round(tot, 2), "pnl_1c": round(slip, 2), "days_up": upd, "days": len(ds),
                             "worst": round(worst, 2), "first_half": round(first, 2), "second_half": round(second, 2), "by_day": {d: round(dd[d][0], 2) for d in ds}}
        print(f"{name:38} {bets:6d} {(won / bets * 100 if bets else 0):5.1f}% {tot:9.1f} {slip:9.1f} {upd:3d}/{len(ds):<3d} {worst:10.1f} {first:9.1f} {second:9.1f}")
    json.dump(out, open(os.path.join(D, "results.json"), "w"), indent=1)


if __name__ == "__main__": main()
