# Paper-trading harness: old model vs improved bot

Two simulated $100 accounts trade the same live Polymarket US crypto Up/Down markets
(BTC, ETH, SOL, XRP; 5-minute and 15-minute windows) side by side.

- `old`: behavioural replica of NeverSmiling as measured in `../REPORT.md`.
- `improved`: the fixes from the report (pull quotes on index moves, no averaging down,
  fee-aware taking, settlement-average logic in the final minute, no quoting in the pick-off zone).

Data: Polymarket US gateway (markets, order books with last-trade stats, and the
settlement index candles at 1-second resolution, i.e. CF Benchmarks BRTI for BTC),
Coinbase Exchange websocket for spot, and a periodic cross-check of the index against
the values embedded on cfbenchmarks.com's BRTI page. No account or API key is used; all
orders are simulated against the public book.

```
pip install websockets
PAPERBOT_DATA=./paperbot-data PAPERBOT_PORT=8080 python3 -m paperbot.run
# dashboard: http://localhost:8080/
```

State persists in `paperbot-data/` (SQLite `paper.db` with fills, settlements, equity
samples and an index log; `accounts.json` for resume). Fill simulation is conservative:
resting orders fill only when the book trades through their price or printed volume at
their price exceeds the size that was queued ahead of them when placed; takers pay the
Tier 1 fee (0.095 × qty × p × (1−p)); no rebates. Settlement uses the market's published
`settlementPrice` and falls back to the 60-second index average.

## Bots and universes (current)

80 paper accounts now run on one engine in four universes: `std` (port 8080, fixed contract
clips, $100 each), `dollar` (8081, every order a random $0.10-$10), `dollar30` (8082,
$0.10-$30 with $1,000 each) and `dollar1000` (8083, $0.10-$1,000 with $30,000 each). The dashboard names every bot (1-80). Bots 31, 64-80 form the
arbitrage family (`paperbot/ladder.py`, `paperbot/arbs.py`): they trade only relations fixed
by the settlement rules (dominance between contracts sharing a settlement instant, bucket
tiling, mutual exclusion, one-touch versus strike, and the bounded 60-second average in the
final seconds), with every leg sized equally and verified on live books.

Data notes: the gateway sits behind a 30-second public cache, so every request carries a
unique query parameter (otherwise books arrive up to 30 s stale), and requests use a plain
client user agent (the default Python agent is throttled far below the documented limit).

Fill realism: paper takers consume the displayed liquidity of a book snapshot (per universe), so repeated
passes against the same book never fill more than the exchange showed; a level's consumption is carried
over while the exchange still shows the same size there. Multi-leg sets are sized to what cash covers
before any leg is sent, and unequal fills are unwound at market.

## Evolution universe (port 8086)

The best 20 bots across every other dashboard (mean return over the universes each appears in, bots with at least
one fill) start with $1,000 each; every order is a random $0.10-$50, and Bonereaper-lab variants keep their own
sizing. Every hour 5 bots are removed and replaced by 3 children of the current top 5 (picked 5:4:3:2:1 by rank)
and 2 immigrants (the best bots of the other dashboards whose strategy is not here yet). Bots are ranked by a steady
score: average realized P/L per settled market minus half its standard error, so consistent bots beat one lucky market.
Bots with fewer than 5 settled markets are protected for their first 3 hours, and no strategy family (a seed and
its descendants) may hold more than 6 of the 20 places: over-cap families lose their weakest members first. A child copies its parent's strategy and settings and changes 1-3 tuning
numbers (price caps, z thresholds, timing windows, edges, sizes); request-rate settings are never changed. Each
child is scored once, after a full period, by its return minus the median, and that score is credited to the
settings it moved and their direction; later mutations of the same setting lean toward the direction that did
better. The dashboard's Evolution panel lists every cull and the learned leanings. Code: `paperbot/evolve.py`;
state: `paperbot-data/evolve/evolve.json` (members, lineage, graveyard, lessons). It runs until the engine is
stopped; `PAPERBOT_EVOLVE_PERIOD` (seconds) changes the cull interval.

## Champion universe (port 8087)

One $1,000 bank that trades BTC, ETH, SOL and XRP 5-minute and 15-minute Up/Down windows (SOL and XRP added at
the user's request; the hired bot's own coin list is widened to all four) (Polymarket US lists no
30-minute windows). It trades as the single best bot of every other dashboard: each strategy, and each evolved
variant alive in the evolution universe, is scored on its settled 5m/15m markets of those coins only, with every market's
P/L rescaled to a $1,000 account, as the mean per market minus half its standard error (at least 20 markets).
Every hour, if the champion has not made money since it was hired (losing, or not trading), it is replaced by the best-scoring bot that has
not been fired in the last 24 hours; a champion that is up keeps its job. The bank, open positions included,
carries over between hires. Orders are $1-$30 (Bonereaper-lab variants keep their own contract sizing). The panel
shows the current hire, P/L since hiring, daily P/L, past champions and the candidate board. To hire a bot by hand, write `{"key": "<bot>"}` to
`paperbot-data/champion/hire.json` (any strategy, Bonereaper-lab variant or living evolve member); the hourly
review still applies. Code: `paperbot/champion.py`; state: `paperbot-data/champion/champion.json`. Paper results do not guarantee live results.
