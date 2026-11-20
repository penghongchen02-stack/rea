"""Dashboard export of the live runner's state. No test touches the network."""
import json

from psb import dashboard, paper
from tests.test_paper import NOW

BOT = "Live_19-29"
CONFIG = {"armed": False, "bot": None, "bankroll_usd": 1000,
          "limits": {"max_order_usd": 5, "max_exposure_usd": 50, "max_daily_loss_usd": 25}}


def _state(tmp_path, log_lines, orders=()):
    (tmp_path / "live.log").write_text("\n".join(log_lines) + "\n")
    led = paper.Ledger(bankroll=1000.0, started_at="2026-11-17T00:00:00Z")
    led.record(BOT, "nba-a-b", "long", 0.40, 10, 0.1, "taker", "t", 0.45, NOW)
    led.settled["nba-a-b"] = 1.0
    led.save(tmp_path / "shadow-ledger.json")
    (tmp_path / "shadow-orders.jsonl").write_text(
        "".join(json.dumps(o) + "\n" for o in orders))
    (tmp_path / "live.json").write_text(json.dumps(CONFIG))
    return tmp_path


def test_live_rows_shadow_only(tmp_path):
    state = _state(tmp_path, [
        f"2026-11-20T02:30:18Z SHADOW: no orders sent; bot {BOT}; limits Limits(max_order_usd=5.0); "
        "not armed because: config has \"armed\": false; config names no bot",
        '2026-11-20T02:30:38Z stats {"polls": 4, "takes": 1, "maker_fills": 0, "no_cash": 2} '
        "open_orders=0 exposure=$3.50",
    ])
    real, shadow = dashboard.live_rows(state, CONFIG)
    assert real["mode"] == "Real money" and not real["has_run"] and real["settled_pnl"] is None
    assert real["max_order_usd"] == 5 and real["config_armed"] is False
    assert shadow["has_run"] and shadow["bot"] == BOT
    assert shadow["started"] == "2026-11-20T02:30:18Z" and shadow["last_seen"] == "2026-11-20T02:30:38Z"
    assert shadow["not_armed_because"].startswith('config has "armed": false')
    assert shadow["exposure"] == 3.5 and shadow["polls"] == 4 and shadow["no_cash"] == 2
    assert shadow["settled_pnl"] == 5.9 and shadow["taker_fills"] == 1   # 10 * (1 - 0.40) - 0.10 fee


def test_live_rows_real_run_and_halt(tmp_path):
    state = _state(tmp_path, [
        f"2026-11-20T02:30:18Z SHADOW: no orders sent; bot {BOT}; limits L; not armed because: x",
        f"2026-11-20T03:00:00Z ARMED: REAL MONEY; bot {BOT}; limits L",
        '2026-11-20T03:02:00Z stats {"polls": 9, "takes": 0, "maker_fills": 0, "no_cash": 0} '
        "open_orders=2 exposure=$9.00",
        "2026-11-20T03:05:00Z HALT: stop file found",
    ])
    real, shadow = dashboard.live_rows(state, CONFIG)
    assert real["has_run"] and real["not_armed_because"] is None
    assert real["open_orders"] == 2 and real["exposure"] == 9.0
    assert real["halted"] == "stop file found" and real["last_seen"] == "2026-11-20T03:05:00Z"
    assert real["settled_pnl"] is None          # no real ledger.json yet
    assert shadow["last_seen"] == "2026-11-20T02:30:18Z" and shadow["exposure"] is None


def test_live_orders_newest_first_and_export(tmp_path):
    o = {"bot": BOT, "slug": "nba-a-b", "kind": "take", "side": "long", "price": 0.4,
         "contracts": 12.5, "tag": "t", "sent": False, "order_id": None}
    state = _state(tmp_path, [], [dict(o, t="2026-11-20T02:31:00Z"), dict(o, t="2026-11-20T02:35:00Z")])
    rows = dashboard.live_order_rows(state)
    assert [r["t"] for r in rows] == ["2026-11-20T02:35:00Z", "2026-11-20T02:31:00Z"]
    assert rows[0]["dollars"] == 5.0 and rows[0]["mode"].startswith("Shadow") and rows[0]["id"] == 0
    dashboard.main(["--live", str(state), "--config", str(state / "live.json")])
    out = json.loads((state / "dashboard" / "live.json").read_text())
    assert len(out["rows"]) == 2 and out["meta"][0]["label"] == "dashboard.py"
    assert len(json.loads((state / "dashboard" / "live-orders.json").read_text())["rows"]) == 2
