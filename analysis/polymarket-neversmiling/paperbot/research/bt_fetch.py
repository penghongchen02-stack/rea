"""Download what Polymarket US keeps about closed Up/Down windows, for the minute-price backtest (bt_run.py).

For every 5-minute and 15-minute window of the four coins, newest first, back to DAYS days: the starting line and the
settlement price (GET /v1/markets?slug=) and the stored price observations from 10 minutes before the window to a
minute after it (GET /v1/price-history, about one a minute). Rows go to paperbot-data/backtest/hist.db; a window
already stored is skipped, so the script can be stopped and started again. Run from the paperbot directory:

    python3 research/bt_fetch.py [days]
"""
import sys, os, time, json, sqlite3, threading, queue
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paperbot import feeds

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 26
OUT = os.path.join("paperbot-data", "backtest"); os.makedirs(OUT, exist_ok=True)
feeds._rl = feeds.RateLimiter(6)          # this process's own budget, beside the engine's, under the gateway's 20 a second
db = sqlite3.connect(os.path.join(OUT, "hist.db"), check_same_thread=False)
db.execute("create table if not exists win(slug text primary key, asset text, hz text, start integer, dur integer, p2b real, settle real, pts text, found integer)")
have = {r[0] for r in db.execute("select slug from win")}
lock = threading.Lock(); jobs = queue.Queue(); done = [0, 0]


def work():
    while True:
        try: a, h, d, s = jobs.get_nowait()
        except queue.Empty: return
        slug = feeds.window_slug(a, h, s)
        raw = feeds.get_json(f"{feeds.GATEWAY}/v1/markets?slug={slug}")
        ms = (raw or {}).get("markets") or []
        row = (slug, a, h, s, d, None, None, None, 0)
        if ms and ms[0].get("assetPriceTerms"):
            t = ms[0]["assetPriceTerms"]
            p2b = float(t["priceToBeat"]["value"]) if t.get("priceToBeat") else None
            st = float(t["settlementPrice"]["value"]) if t.get("settlementPrice") else None
            hd = feeds.get_json(f"{feeds.GATEWAY}/v1/price-history?symbol={slug}&timestamp.startTimestamp={s - 600}&timestamp.endTimestamp={s + d + 60}&fidelity=1")
            if hd is None: continue                       # request failed: leave the window for the next run
            pts = [(int(x["timestamp"]), x.get("longPrice"), x.get("shortPrice")) for x in hd.get("history") or []]
            row = (slug, a, h, s, d, p2b, st, json.dumps(pts), 1)
        elif raw is None: continue
        with lock:
            db.execute("insert or replace into win values(?,?,?,?,?,?,?,?,?)", row); done[0] += 1; done[1] += row[8]
            if done[0] % 200 == 0: db.commit(); print(time.strftime("%H:%M:%S"), "stored", done[0], "found", done[1], "left", jobs.qsize(), "429s", feeds.RATE_LIMITED[0], flush=True)


end = int(time.time()) // 900 * 900 - 900
for day in range(DAYS):
    for h, d in (("15m", 900), ("5m", 300)):
        s = end - day * 86400
        while s > end - (day + 1) * 86400:
            s -= d
            for a in feeds.ASSETS:
                if feeds.window_slug(a, h, s) not in have: jobs.put((a, h, d, s))
print("to fetch", jobs.qsize(), flush=True)
ts = [threading.Thread(target=work) for _ in range(6)]
for t in ts: t.start()
for t in ts: t.join()
db.commit(); print("finished", done, flush=True)
