"""Check that the Polymarket US keys work, and show the account's balance.

    POLYMARKET_KEY_ID=... POLYMARKET_SECRET_KEY=... python3 scripts/check_account.py

Read-only: two GET requests (balances, open orders). It never places or cancels
an order, and never prints or saves the keys. The result is also written to
live-results/dashboard/account.json for the dashboard (no keys in it).
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from psb.live import KEY_ENV, SECRET_ENV, LiveError, Signer, UsClient, _num  # noqa: E402

OUT = ROOT / "live-results" / "dashboard" / "account.json"
FIELDS = (("cash", "currentBalance"), ("buying_power", "buyingPower"),
          ("available_to_withdraw", "availableToWithdraw"), ("in_open_orders", "openOrders"),
          ("unsettled", "unsettledFunds"), ("positions_value", "assetNotional"))


def account_row(checked_at: str, balances: list[dict] | None, open_orders: int | None,
                error: str | None = None) -> dict:
    usd = next((b for b in balances or [] if b.get("currency", "USD") == "USD"), {})
    row = {"account": "Polymarket US", "checked_at": checked_at, "connected": error is None,
           "error": error, "open_orders": open_orders}
    for name, key in FIELDS:
        row[name] = _num(usd.get(key)) if usd else None
    return row


def save(row: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    meta = [{"label": "check_account.py", "value": Path(__file__).read_text(), "type": "code",
             "lang": "python"},
            {"label": "Input", "value": "GET /v1/account/balances and GET /v1/orders/open "
                                        "on api.polymarket.us, signed with the account's keys"}]
    OUT.write_text(json.dumps({"rows": [row], "meta": meta}))


def main() -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    key, secret = os.environ.get(KEY_ENV), os.environ.get(SECRET_ENV)
    if not key or not secret:
        print(f"{KEY_ENV} / {SECRET_ENV} are not set in this Terminal window.")
        return 1
    try:
        client = UsClient(Signer(key.strip(), secret.strip()))
        balances = client._call("GET", "/v1/account/balances").get("balances") or []
        orders = client.open_orders()
    except LiveError as e:
        print(f"NOT CONNECTED: {e}")
        save(account_row(now, None, None, str(e)[:300]))
        return 1
    print("CONNECTED to Polymarket US (read-only check, nothing was traded).")
    if not balances:
        print("The account reported no balances.")
    for b in balances:
        cur = b.get("currency", "USD")
        print(f"\n{cur} balance:")
        for k, v in b.items():
            if k == "currency":
                continue
            n = _num(v)
            print(f"  {k}: " + (f"${n:,.2f}" if n is not None else str(v)))
    print(f"\nOpen orders on the account: {len(orders)}")
    save(account_row(now, balances, len(orders)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
