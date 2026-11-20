"""The local dashboard server: what /api/summary and /api/bot return. No network
beyond a server on 127.0.0.1 started by the test itself."""
import json
import os
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from psb import paper, serve
from tests.test_paper import NOW

BOT = "Live_19-29"


def _root(tmp_path):
    led = paper.Ledger(bankroll=1000.0, started_at="2026-11-17T00:00:00Z")
    led.record(BOT, "nba-a-b", "long", 0.40, 10, 0.1, "taker", "entry", 0.45, NOW)
    led.settled["nba-a-b"] = 1.0
    led.settled_at["nba-a-b"] = "2026-11-17T15:00:00Z"
    led.record(BOT, "nba-c-d", "short", 0.30, 5, 0.0, "taker", "entry", None, NOW)
    led.save(tmp_path / "paper-results" / "ledger.json")
    (tmp_path / "live-results" / "dashboard").mkdir(parents=True)
    (tmp_path / "live-results" / "live.log").write_text(
        f"2026-11-20T02:30:18Z SHADOW: no orders sent; bot {BOT}; limits L; not armed because: x\n")
    (tmp_path / "live-results" / "dashboard" / "account.json").write_text(json.dumps(
        {"rows": [{"connected": True, "buying_power": 100.0, "cash": 100.0, "open_orders": 0}]}))
    (tmp_path / "live.json").write_text(json.dumps({"armed": False, "bot": None,
                                                    "limits": {"max_order_usd": 5}}))
    return tmp_path


@pytest.mark.running_bots
def test_summary_and_bot_detail(tmp_path):
    state = serve.State(fetch=False, root=_root(tmp_path))
    s = state.summary()
    assert s["paper_source"] == "local file paper-results/ledger.json"
    assert s["fleet"]["running"] == len(serve.dashboard.BOTS) and s["fleet"]["up"] == 1
    assert s["fleet"]["pnl"] == 5.9
    assert any(g["group"] == "Live_19 versions" and g["up"] == 1 for g in s["groups"])
    assert s["account"]["buying_power"] == 100.0
    assert [r["has_run"] for r in s["live"]] == [False, True]
    d = state.bot(BOT)
    assert d["bot"]["settled_pnl"] == 5.9 and d["info"]["title"]
    assert [h["market"] for h in d["holdings"]] == ["nba-c-d"]
    assert [g["market"] for g in d["games"]] == ["nba-a-b"]
    assert len(d["fills"]) == 2
    assert state.bot("no-such-bot") is None


@pytest.mark.running_bots
def test_reloads_only_when_the_ledger_changes(tmp_path):
    root = _root(tmp_path)
    state = serve.State(fetch=False, root=root)
    state.summary()
    first = state._paper_for
    state.summary()
    assert state._paper_for is first
    led = paper.Ledger.load(root / "paper-results" / "ledger.json", 1000.0)
    led.settled["nba-c-d"] = 0.0
    led.save(root / "paper-results" / "ledger.json")

    os.utime(root / "paper-results" / "ledger.json", (1e9 + 5, 1e9 + 5))
    assert state.summary()["fleet"]["pnl"] == 9.4      # + 5 x (1 - 0.30) on the short
    assert state._paper_for is not first


@pytest.mark.running_bots
def test_http_endpoints(tmp_path):
    state = serve.State(fetch=False, root=_root(tmp_path))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), serve.make_handler(state))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode()
        assert "<title>Paper Bot Desk</title>" in page
        s = json.loads(urllib.request.urlopen(base + "/api/summary").read())
        assert s["fleet"]["up"] == 1
        b = json.loads(urllib.request.urlopen(base + "/api/bot?key=" + BOT).read())
        assert b["bot"]["bot"] == BOT
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/api/bot?key=nope")
        assert e.value.code == 404
    finally:
        srv.shutdown()
