"""Move bets of bots no longer running out of the paper ledger.

A bot is removed when it is not in psb.registry.BOTS (retired or deleted from
code). Its fills, positions, strategy state, resting orders and start time move
to a gzipped archive beside the ledger; shared market data (settled prices,
marks) stays. Running it again merges new removals into the same archive.

    python3 -m scripts.archive_removed [--ledger paper-results/ledger.json]

Do not run while a paper run is active: the run rewrites the ledger from memory.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from psb.registry import BOTS

PER_BOT = ("positions", "states", "bot_started")


def split(d: dict) -> tuple[dict, dict]:
    keep = {**d}
    gone: dict = {"fills": [], "orders": []}
    for k in PER_BOT:
        keep[k] = {b: v for b, v in d[k].items() if b in BOTS}
        gone[k] = {b: v for b, v in d[k].items() if b not in BOTS}
    for k in ("fills", "orders"):
        keep[k] = [x for x in d[k] if x["bot"] in BOTS]
        gone[k] = [x for x in d[k] if x["bot"] not in BOTS]
    return keep, gone


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default="paper-results/ledger.json")
    ap.add_argument("--archive", default="paper-results/archive/removed-bots.json.gz")
    a = ap.parse_args()
    ledger, archive = Path(a.ledger), Path(a.archive)
    d = json.loads(ledger.read_text())
    keep, gone = split(d)
    if not gone["fills"] and not any(gone[k] for k in PER_BOT) and not gone["orders"]:
        print("nothing to archive")
        return
    if archive.exists():
        old = json.loads(gzip.decompress(archive.read_bytes()))
        for k in ("fills", "orders"):
            gone[k] = old[k] + gone[k]
        for k in PER_BOT:
            gone[k] = {**old[k], **gone[k]}
    archive.parent.mkdir(parents=True, exist_ok=True)
    tmp = archive.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(gone).encode(), 9))
    tmp.replace(archive)
    tmp = ledger.with_suffix(".tmp")
    tmp.write_text(json.dumps(keep))
    tmp.replace(ledger)
    bots = set(gone["positions"]) | set(gone["states"]) | {f["bot"] for f in gone["fills"]}
    print(f"archived {len(bots)} removed bots, {len(gone['fills'])} bets -> {archive}; "
          f"ledger now {ledger.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
