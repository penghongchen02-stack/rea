"""Download a bounded window of each source wallet's history into data/."""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import polydata
from .sources import WALLETS

DATA = Path(__file__).resolve().parent.parent / "data"


def fill_key(t: dict) -> tuple:
    """Identity of one fill across the all-fills and taker-only feeds."""
    return (t["transaction_hash"], t["token_id"], t["side"], t["size"], t["price"])


def fetch_wallet(key: str, days: int, max_trades: int, until_ts: int) -> dict:
    w = WALLETS[key]
    out = DATA / key
    latest = list(polydata.iter_trades(w.address, 0, until_ts, 1, taker_only=False))
    # Anchor the window at the wallet's latest fill so dormant wallets still get data.
    last_ts = int(latest[0]["timestamp"]) + 1 if latest else until_ts
    since = last_ts - days * 86400
    rows = list(polydata.iter_trades(w.address, since, last_ts, max_trades, taker_only=False))
    oldest = min((int(r["timestamp"]) for r in rows), default=since)
    # Taker fills over the same covered span; the difference is maker fills.
    taker = {fill_key(t) for t in
             polydata.iter_trades(w.address, oldest, last_ts, 10 * max_trades, taker_only=True)}
    for r in rows:
        r["is_taker"] = fill_key(r) in taker
    polydata.write_jsonl(out / "trades.jsonl", rows)
    cids = sorted({r["condition_id"] for r in rows})
    markets = polydata.fetch_markets(cids)
    (out / "markets.json").write_text(json.dumps(markets))
    n_pos = polydata.write_jsonl(
        out / "closed_positions.jsonl", polydata.iter_closed_positions(w.address, oldest, 200_000)
    )
    meta = {
        "wallet": w.address,
        "window_requested": [since, last_ts],
        "window_covered": [oldest, last_ts],
        "trades": len(rows),
        "taker_fills": sum(r["is_taker"] for r in rows),
        "taker_keys_unmatched": len(taker - {fill_key(r) for r in rows}),
        "trade_cap_hit": len(rows) >= max_trades,
        "markets_requested": len(cids),
        "markets_found": len(markets),
        "closed_positions": n_pos,
        "fetched_at": int(time.time()),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    return {key: meta}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--max-trades", type=int, default=100_000)
    p.add_argument("wallets", nargs="*", default=list(WALLETS))
    a = p.parse_args(argv)
    now = int(time.time())
    (DATA / "sports.json").write_text(json.dumps(polydata.get_json(f"{polydata.GAMMA_API}/sports")))
    with ThreadPoolExecutor(max_workers=4) as ex:
        for res in ex.map(lambda k: fetch_wallet(k, a.days, a.max_trades, now), a.wallets):
            print(json.dumps(res), flush=True)


if __name__ == "__main__":
    main()
