# Pair bots (paper only)

Uploaded by the user on 2026-11-28; the same files run on their server. No order code: the bot reads
the Polymarket US market stream (signed sign-in only) and the public international stream, and keeps
paper orders in memory. Records go to `state.json`, `events.jsonl` and `pairbot.log` next to each bot
and are not committed.

- `pairbot-now/`: winners, main spread and main total; first side at 30-80c.
- `pairbot-before/`: winners only; first side at 15-85c.

Run in a cloud session whose environment allows api.polymarket.us and
ws-subscriptions-clob.polymarket.com and sets `PM_US_KEY_ID` / `PM_US_SECRET_KEY`:

    pip install "websockets==13.1" cryptography   # 14+ renamed extra_headers; the bot needs 13.x
    pairbots/run.sh now 110      # or: before

It uses this repo's `psb` package in place of psb-live50.zip (identical code).

## Simulation check copies

`pairbot-check/` and `pairbot-strict/` hold the same file: pairbot-now's rules plus "markout" events,
the fair price of each filled side 5, 30 and 60 s after the fill (negative = it moved against us,
the fills a real trader would pick us off on). `pair_report.py` there adds that summary.

- `pairbot-check`: best case, the same fill rules as pairbot-now (`PAIR_STRICT=0`, 0.3 s delay).
- `pairbot-strict`: worst case, only trades through our price fill us, 1 s delay.

    PAIR_CLIP=25 PAIR_STRICT=0 pairbots/run.sh check 110
    PAIR_CLIP=25 PAIR_STRICT=1 PAIR_ORDER_DELAY=1.0 pairbots/run.sh strict 110
