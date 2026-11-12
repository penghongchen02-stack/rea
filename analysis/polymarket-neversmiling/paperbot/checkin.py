"""One-line scoreboard for the hourly check-in: reads /state.json from every universe's dashboard.

    python3 checkin.py "run 6"            # prints the line; append it to checkins.md
    python3 checkin.py "run 6" 8080 8081  # a subset of ports (default 8080-8089 and 8091)

Per universe: net P/L across all bots, the top bot with its P/L, the engine error count. The order-book stream
status comes from the first port, and Traceback lines in the engine log are counted. Never prints key values.
"""
import os, sys, json, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(os.environ.get("PAPERBOT_DATA", os.path.join(HERE, "paperbot-data")), "bot.log")


def state(port):
    with urllib.request.urlopen(f"http://localhost:{port}/state.json", timeout=10) as r: return json.load(r)


def main():
    label = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].isdigit() else ""
    ports = [int(p) for p in sys.argv[1:] if p.isdigit()] or list(range(8080, 8090)) + [8091]
    parts = []; stream = None
    for port in ports:
        try: s = state(port)
        except Exception as e: parts.append(f"port {port} DOWN ({type(e).__name__})"); continue
        accts = s.get("accounts", {})
        pnl = {n: a.get("pnl", 0.0) for n, a in accts.items()}
        net = sum(pnl.values()); top = max(pnl, key=pnl.get) if pnl else "-"
        parts.append(f"{s['config']['universe']} net {net:+.0f} top {top} {pnl.get(top, 0.0):+.1f} err {s.get('errors', '?')}")
        if stream is None: stream = s.get("feeds", {}).get("stream") or {}
    st = "stream off" if not stream or not stream.get("enabled") else (
        f"stream on msgs {stream.get('msgs')} age {stream.get('age')}s subs {stream.get('subscribed')} err {stream.get('errors')}"
        if stream.get("connected") else f"stream enabled but NOT connected err {stream.get('errors')}")
    tb = 0
    try:
        with open(LOG, errors="replace") as f: tb = sum(1 for line in f if line.startswith("Traceback"))
    except OSError: pass
    stamp = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime())
    print(f"{stamp} {label} | " + " | ".join(parts) + f" | {st}" + (f" | tracebacks {tb}" if tb else ""))


if __name__ == "__main__": main()
