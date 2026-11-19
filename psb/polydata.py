"""Read-only access to public Polymarket (international) data used for replay.

Sources:
  data-api.polymarket.com/v2/trades     wallet fills (no fee field)
  data-api.polymarket.com/v2/positions  per-position cost, fees and realized P&L
  gamma-api.polymarket.com/markets      market metadata and resolution
"""
from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterator

DATA_API = "https://data-api.polymarket.com/v2"
GAMMA_API = "https://gamma-api.polymarket.com"
USER_AGENT = "psb-research/0.1"


class FetchError(RuntimeError):
    pass


# A dropped or garbled connection: worth retrying. OSError covers URLError,
# timeouts and resets raised while reading the reply (ConnectionResetError).
TRANSIENT = (OSError, http.client.HTTPException, json.JSONDecodeError)


def get_json(url: str, params: list[tuple[str, str]] | None = None, retries: int = 4):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    delay = 1.0
    last: Exception | None = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:  # 4xx other than 429 will not improve
            last = e
            if e.code != 429 and e.code < 500:
                raise FetchError(f"{e.code} for {url}") from e
        except TRANSIENT as e:
            last = e
        time.sleep(delay)
        delay *= 2
    raise FetchError(f"giving up on {url}: {last}")


def _paginate(path: str, params: list[tuple[str, str]], max_rows: int) -> Iterator[dict]:
    cursor: str | None = None
    seen = 0
    while seen < max_rows:
        page_params = list(params)
        if cursor:
            page_params.append(("cursor", cursor))
        body = get_json(f"{DATA_API}/{path}", page_params)
        rows = body.get("data", [])
        for row in rows:
            yield row
            seen += 1
            if seen >= max_rows:
                return
        pag = body.get("pagination") or {}
        cursor = pag.get("next_cursor")
        if not rows or not pag.get("has_more") or not cursor:
            return


def iter_trades(wallet: str, since_ts: int, until_ts: int, max_rows: int,
                taker_only: bool) -> Iterator[dict]:
    """Newest-first wallet fills with since_ts <= timestamp < until_ts.

    The endpoint defaults to taker fills only; taker_only=False adds maker fills.
    """
    params = [("user", wallet), ("limit", "1000"), ("taker_only", str(taker_only).lower())]
    for row in _paginate("trades", params, 10**9):
        ts = int(row["timestamp"])
        if ts >= until_ts:
            continue
        if ts < since_ts:
            return
        yield row
        max_rows -= 1
        if max_rows <= 0:
            return


def iter_closed_positions(wallet: str, since_ts: int, max_rows: int) -> Iterator[dict]:
    """Closed positions, newest first, whose last activity is at or after since_ts."""
    params = [("user", wallet), ("status", "CLOSED"), ("sort_by", "TIMESTAMP"), ("limit", "500")]
    for row in _paginate("positions", params, max_rows):
        if int(row.get("last_event_at") or 0) < since_ts:
            return
        yield row


def fetch_markets(condition_ids: list[str], batch: int = 50) -> dict[str, dict]:
    """Market metadata keyed by condition_id. Missing ids are simply absent."""
    out: dict[str, dict] = {}
    for i in range(0, len(condition_ids), batch):
        chunk = condition_ids[i : i + batch]
        for closed in ("true", "false"):
            params = [("condition_ids", c) for c in chunk] + [("closed", closed), ("limit", str(batch))]
            for m in get_json(f"{GAMMA_API}/markets", params):
                out[m["conditionId"]] = m
    return out


def write_jsonl(path: Path, rows) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
            n += 1
    return n


def read_jsonl(path: Path) -> list[dict]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]
