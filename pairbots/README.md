# Pair bots (paper only)

Uploaded by the user on 2026-11-28; the same files run on their server. No order code: the bot reads
the Polymarket US market stream (signed sign-in only) and the public international stream, and keeps
paper orders in memory. Records go to `state.json`, `events.jsonl` and `pairbot.log` next to each bot
and are not committed.

- `pairbot-now/`: winners, main spread and main total; first side at 30-80c.
- `pairbot-before/`: winners only; first side at 15-85c.

Run in a cloud session whose environment allows api.polymarket.us and
ws-subscriptions-clob.polymarket.com and sets `PM_US_KEY_ID` / `PM_US_SECRET_KEY`:

    pip install websockets cryptography
    pairbots/run.sh now 110      # or: before

It uses this repo's `psb` package in place of psb-live50.zip (identical code).
