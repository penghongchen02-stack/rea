"""A local dashboard for the paper bots and the live runner, at http://localhost:8110.

    python3 -m psb.serve [--port 8110] [--no-fetch]

Reads the files on this Mac and updates itself: the page asks for fresh data
every minute, and the server reloads a file only when it changed.

Paper results: the scheduled paper runs push them to GitHub, so by default the
server runs `git fetch` every few minutes and reads paper-results/ledger.json
from the remote branch (origin/<current branch>) without touching your working
tree; when that fails, or with --no-fetch, it reads the local file.
Live runner: live-results/ (log, ledgers, orders) and live.json, as written.
Account: live-results/dashboard/account.json from scripts/check_account.command.
The page never sees the Polymarket keys. Nothing here trades. Listens on
127.0.0.1 only, so it is not reachable from other machines.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import dashboard
from .paper import Ledger

ROOT = Path(__file__).resolve().parent.parent
PAGE = Path(__file__).resolve().parent / "serve.html"
FETCH_EVERY = 300.0      # seconds between `git fetch` runs
RECENT_GAMES = 20


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                          timeout=120, check=True).stdout


class PaperSource:
    """The paper ledger: from the remote branch after a fetch, else the local file."""

    def __init__(self, fetch: bool, local: Path):
        self.fetch, self.local = fetch, local
        self.last_fetch = 0.0
        self.note = ""
        self._key = None
        self._ledger: Ledger | None = None

    def _remote(self) -> tuple[str, str] | None:
        """(commit, ref) of the remote branch's ledger, fetching when due."""
        try:
            branch = _git("rev-parse", "--abbrev-ref", "HEAD").strip()
            ref = f"origin/{branch}"
            if time.monotonic() - self.last_fetch > FETCH_EVERY:
                self.last_fetch = time.monotonic()
                _git("fetch", "-q", "origin", branch)
            commit = _git("log", "-1", "--format=%H", ref, "--", "paper-results/ledger.json").strip()
            return (commit, ref) if commit else None
        except (subprocess.SubprocessError, OSError):
            return None

    def ledger(self) -> Ledger:
        remote = self._remote() if self.fetch else None
        if remote:
            key = ("remote", remote[0])
            if key != self._key:
                text = _git("show", f"{remote[0]}:paper-results/ledger.json")
                self._ledger = _ledger_from_text(text)
                self._key = key
                self.note = f"GitHub, {remote[1]} at {remote[0][:9]}"
            return self._ledger
        mtime = self.local.stat().st_mtime if self.local.exists() else None
        key = ("local", mtime)
        if key != self._key:
            self._ledger = Ledger.load(self.local, 1000.0)
            self._key = key
            self.note = f"local file {self.local.parent.name}/{self.local.name}"
        return self._ledger


def _ledger_from_text(text: str) -> Ledger:
    """Ledger.load reads a path; give it the remote copy through a temp file."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ledger.json"
        p.write_text(text)
        return Ledger.load(p, 1000.0)


class State:
    """Everything the page shows, rebuilt only when an input changed."""

    def __init__(self, fetch: bool = True, root: Path = ROOT):
        self.root = root
        self.paper = PaperSource(fetch, root / "paper-results" / "ledger.json")
        self.lock = threading.Lock()
        self._paper_for = None
        self._info = dashboard.bot_info_rows()
        self.bots: list[dict] = []
        self.by_bot: dict[str, dict] = {}
        self.through: str | None = None

    def _paper(self) -> None:
        led = self.paper.ledger()
        if led is self._paper_for:
            return
        self._paper_for = led
        self.bots = dashboard.all_bot_rows(led)
        holdings, fills, games = defaultdict(list), defaultdict(list), defaultdict(list)
        for r in dashboard.holding_rows(led):
            holdings[r["bot"]].append(r)
        for r in dashboard.bot_fill_rows(led):
            fills[r["bot"]].append(r)
        for r in dashboard.game_rows(led):
            games[r["bot"]].append(r)
        self.by_bot = {"holdings": holdings, "fills": fills, "games": games}
        self.through = max((f["t"] for f in led.fills[-2000:]), default=None)

    def summary(self) -> dict:
        with self.lock:
            self._paper()
            running = [r for r in self.bots if r["running"]]
            groups: dict[str, dict] = {}
            for r in running:
                g = groups.setdefault(r["group"], {"group": r["group"], "bots": 0, "up": 0, "down": 0, "pnl": 0.0})
                g["bots"] += 1
                g["up"] += r["settled_pnl"] > 0
                g["down"] += r["settled_pnl"] < 0
                g["pnl"] += r["settled_pnl"]
            for g in groups.values():
                g["pnl"] = round(g["pnl"], 2)
            state = self.root / "live-results"
            cfg_path = self.root / "live.json"
            config = json.loads(cfg_path.read_text()) if cfg_path.exists() else None
            acct_path = state / "dashboard" / "account.json"
            account = json.loads(acct_path.read_text())["rows"][0] if acct_path.exists() else None
            return {
                "paper_source": self.paper.note, "paper_through": self.through,
                "fleet": {"running": len(running), "up": sum(r["settled_pnl"] > 0 for r in running),
                          "down": sum(r["settled_pnl"] < 0 for r in running),
                          "pnl": round(sum(r["settled_pnl"] for r in running), 2)},
                "groups": sorted(groups.values(), key=lambda g: -g["pnl"]),
                "bots": self.bots,
                "live": dashboard.live_rows(state, config),
                "live_orders": dashboard.live_order_rows(state)[:50],
                "account": account,
            }

    def bot(self, key: str) -> dict | None:
        with self.lock:
            self._paper()
            row = next((r for r in self.bots if r["bot"] == key), None)
            if row is None:
                return None
            info = next((r for r in self._info if r["bot"] == key), {})
            games = sorted(self.by_bot["games"].get(key, []), key=lambda g: g["settled_at"], reverse=True)
            return {"bot": row, "info": info, "holdings": self.by_bot["holdings"].get(key, []),
                    "fills": sorted(self.by_bot["fills"].get(key, []), key=lambda f: -f["id"]),
                    "games": games[:RECENT_GAMES]}


def make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            try:
                if u.path in ("/", "/index.html"):
                    self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                elif u.path == "/api/summary":
                    self._json(state.summary())
                elif u.path == "/api/bot":
                    key = (parse_qs(u.query).get("key") or [""])[0]
                    out = state.bot(key)
                    self._json(out if out else {"error": f"no bot {key!r}"}, 200 if out else 404)
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as e:  # keep serving; show the error on the page
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)

        def log_message(self, fmt, *args):   # quiet: no line per request
            pass

    return Handler


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8110)
    ap.add_argument("--no-fetch", action="store_true", help="read the local paper ledger only")
    a = ap.parse_args(argv)
    state = State(fetch=not a.no_fetch)
    print("Loading the paper ledger (a few seconds)...", flush=True)
    state.summary()
    print(f"Paper results from {state.paper.note}, through {state.through}", flush=True)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(state))
    print(f"Dashboard at http://localhost:{a.port}  (Ctrl+C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
