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

## Deleting losing bots (std, dollar, dollar30, dollar1000, bonelab, top20)

Writing `{"per": 5}` to a dashboard's `prune.json` (`paperbot-data/prune.json` for std, `paperbot-data/<name>/prune.json`
for the others) deletes every bot whose P/L is below zero at that moment (open positions at mark) and adds one new
bot for every 5 deleted (rounded up). Each new bot is a child of one of that dashboard's 5 most profitable bots with
1-3 settings changed, like the evolution universe's children, on a fresh account; it is labelled with its parent and
changes. Bots that never traded are kept. Deletions and new bots persist in `roster.json` (paperbot/roster.py).
The first prune ran at 17:15Z on 11-11: 281 bots deleted, 60 added.

## Evolution universe (port 8086)

The best 20 bots across every other dashboard (mean return over the universes each appears in, bots with at least
one fill) start with $1,000 each; every order is a random $0.10-$50, and Bonereaper-lab variants keep their own
sizing. Every bot with its own coin list (the Bonereaper-lab variants) trades BTC, ETH, SOL and XRP together. Every hour 5 bots are removed and replaced by 3 children of the current top 5 (picked 5:4:3:2:1 by rank)
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

## Report replicas in the tryout (paperbot/reports.py)

Replicas of the nine crypto Up/Down traders in the PR&R reports trade in the tryout with $1,000 each, on their own coins
and window lengths, sizing their own orders from each trader's reported fill sizes (log-normal through the reported
median and P95, capped at the reported maximum) and capping dollars per market (user, 11-11 22:40Z):

| replica | coins / windows | style |
|---|---|---|
| rep_whiskas | BTC, ETH 5m | rests bids on both sides when the pair costs <= $0.98, legs below $0.90, 2x tilt on a signal; $250/market |
| rep_doggystyie | BTC 5m | rests both sides in the first 3 minutes; $15-50 fills, $200/market |
| rep_neversmiling | 4 coins 5m, 15m | rests both sides at $0.30-0.70 in 10-90% of the window, 3x tilt, skips 14-16 UTC; $161/market |
| rep_flippingsharks | BTC 5m | rests both sides, more on the cheaper one, 60-295 s before close, skips 4, 16, 19, 20 UTC; $222/market |
| rep_bonereaper | BTC, ETH, SOL 5m, 15m | rests both sides when the pair is <= $1.10, tilt at 2x; 1% of bankroll ($10) per market |
| rep_pbot6 | 4 coins 5m, 15m | takes the side the model prices 8c+ above its mid (3-8c small), hedge 60-120 s later if the pair < $0.98 |
| rep_slipme | BTC 5m | opens both sides (60 shares), adds to the favoured side, cheap hedges < $0.20, late adds < $0.97; $300/market |
| rep_sirmartingale | BTC 5m, 15m, ETH 5m | one side 5c+ below fair value, averages down, sells into strength ($0.80 or +35c); off 23-02 UTC |
| rep_btc5mscour | BTC 5m | last 120 s, buys the resolving side at $0.90-0.99 unless the lead is < 0.05%; $250/market |

The both-sides traders rest bids: on Polymarket US, Up and Down share one book, so taking both sides costs $1 plus the
spread, while resting a bid for Up and an offer of YES (a bid for Down) fills a pair at $1 minus the spread. Several of
these traders earned their profit from liquidity rewards, which the simulation does not pay. The other 29 reports
(sports, esports, weather, politics) cannot trade here. Since 11-11 22:40Z every tryout bot and the champion size their
own orders (no $1-$30 range).

## Late favourite (late_fav)

From the PR&R trader reports (BTC5MScour, SLIP-ME) and their TWAP settlement study: 5-minute windows settle on an
average of the final seconds, so a clear leader late in the window rarely flips while thin leads stay near coin flips.
In the last 60 seconds of a BTC/ETH/SOL/XRP 5-minute window, late_fav buys the side whose projected settlement leads
the price to beat by at least 5 bp, at no more than 97c, when the model's probability beats the price plus the taker
fee by 1c; one side only, at most $60 per window, held to settlement (protective exit if confidence collapses). It
runs in every main dashboard and in the tryout; the champion hires it only if it passes the tryout.

Champion pause: `{"pause": true}` in `paperbot-data/champion/hire.json` stops trading (open positions still settle);
the champion resumes with the first bot that passes the tryout. Paused at 21:13Z on 11-11 at the user's request.
Hand-add a tryout candidate with `{"key", "base", "src"}` in `paperbot-data/tryout/add.json`.

Sizing fix (11-11 21:20Z): dollar-mode orders that sell YES (buy the Down side) were sized at the YES price, so a $30
order on the 94c Down side became 500 contracts; 1,052 such fills exceeded their dashboard's maximum order in dollar,
dollar30, top20 and tryout. Orders are now sized by what the side costs (1 - price for a sell), and exits keep the
held size instead of a new random amount. Earlier results on those dashboards include the oversized Down orders.

## Tryout universe (port 8088)

The champion's candidates trade side by side under the champion's exact rules (BTC, ETH, SOL and XRP 5-minute windows,
$1,000 each; every bot sizes its own orders since 11-11 22:40Z), so every candidate sees the same markets at the same time. The champion hires only bots
that pass here; if none has passed, it keeps its job. A bot passes with, all at once: at least 50 settled windows here,
P/L above zero, profit on at least 3 of the 4 coins, profit in more than half of the hours it traded, a positive edge
(share of windows its side won minus the average price it paid for that side), and profit in both the first and the
second half of its windows. Seats go to the best 12 bots of the other dashboards by a luck-adjusted score (mean P/L
per 5-minute window, rescaled to $1,000, minus sqrt(2 ln N) standard errors for N bots compared, roughly where the
luckiest of N edgeless bots would sit). Every hour, bots with 20+ windows that are losing with a negative edge, and
bots idle for 3 hours, are dropped and the next best join. Why: on 11-11, the top 5 bots picked by past P/L at a
cutoff lost money after it in all three cutoffs tested (6, 4 and 2 hours before 18:05Z), so past P/L from other
dashboards is not enough. Code: `paperbot/tryout.py`; state: `paperbot-data/tryout/tryout.json`.

## Champion universe (port 8087)

One $1,000 bank that trades only BTC, ETH, SOL and XRP 5-minute Up/Down windows (Polymarket US lists no
30-minute windows). It trades as the single best bot of every other dashboard: each strategy, and each evolved
variant of the evolution universe (alive, or culled with its settings kept), is scored on its settled 5m markets of those coins only, with every market's
P/L rescaled to a $1,000 account, as the mean per market minus half its standard error (at least 20 markets, and at least 5 on every one of the four coins).
Every hour, if the champion has not made money since it was hired (losing, or not trading), it is replaced by the best bot that has passed the tryout
(port 8088) and has not been fired in the last 24 hours (none passing: it stays); a champion that is up keeps its job. The bank, open positions included,
carries over between hires. The hired bot sizes its own orders (since 11-11 22:40Z; was $1-$30). The panel
shows the current hire, P/L since hiring, daily P/L, past champions and the candidate board. To hire a bot by hand, write `{"key": "<bot>"}` to
`paperbot-data/champion/hire.json` (any strategy, Bonereaper-lab variant or evolve member; add `"base"`, `"src"` and `"genes"` to give settings explicitly); the hourly
review still applies. Code: `paperbot/champion.py`; state: `paperbot-data/champion/champion.json`. Paper results do not guarantee live results.
