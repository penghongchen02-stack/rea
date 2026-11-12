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

## Phone dashboard (port 8090)

`web/mobile.html`, served by the engine on `PAPERBOT_PORT_MOBILE` (default 8090) with one compact `/summary.json`
(refreshed every 5 s), for the standard universe only (`PAPERBOT_MOBILE_UNIVERSE`, default `std`; user, 11-12 00:05Z):
key and feed health (the key shows as working when the signed market stream is connected and receiving; key values are
never shown), net P/L with bots up/down, windows won, fees and open positions, the live 5-minute window of each coin
(lead over the price to beat in bp, Up price, model probability, time left), every bot ranked by P/L, the latest
settled windows and fills. iPhone layout: system font, light and dark mode, safe areas, bottom tab bar, add-to-home-screen
ready.

## Report replicas in the tryout (paperbot/reports.py)

Replicas of the nine crypto Up/Down traders in the PR&R reports trade in the tryout with $1,000 each, on their own coins
and window lengths, sizing their own orders from each trader's reported fill sizes (log-normal through the reported
median and P95, capped at the reported maximum) and capping dollars per market (user, 11-11 22:40Z):

| replica | coins / windows | style |
|---|---|---|
| rep_whiskas | BTC 5m | (rebuilt from live trades) both sides early at a pair cost <= $1.02, then adds to the leading side at $0.60-0.92 up to 3x; $180/market |
| rep_doggystyie | BTC 5m | rests both sides in the first 3 minutes; $15-50 fills, $200/market |
| rep_neversmiling | 4 coins 5m, 15m | rests both sides at $0.30-0.70 in 10-90% of the window, 3x tilt, skips 14-16 UTC; $161/market |
| rep_flippingsharks | BTC 5m, ETH 5m, BTC 15m | (rebuilt from live trades) buys the cheap side (<= $0.40), buys the other side when the pair would cost <= $0.90 (locked profit); $60/market |
| rep_bonereaper | BTC, ETH, SOL 5m, 15m | rests both sides when the pair is <= $1.10, tilt at 2x; 1% of bankroll ($10) per market |
| rep_pbot6 | 4 coins 5m, 15m | one entry per window on the side the model prices 3c+ above its mid, only once 10+ settlement seconds are pinned; $30/market |
| rep_slipme | BTC 5m | opens both sides (60 shares), adds to the favoured side, cheap hedges < $0.20, late adds < $0.97; $300/market |
| rep_sirmartingale | BTC 5m, 15m, ETH 5m | one side 5c+ below fair value, averages down, sells into strength ($0.80 or +35c); off 23-02 UTC |
| rep_btc5mscour | BTC 5m | last 120 s, buys the resolving side at $0.90-0.99 unless the lead is < 0.05%; $250/market |

The both-sides traders rest bids: on Polymarket US, Up and Down share one book, so taking both sides costs $1 plus the
spread, while resting a bid for Up and an offer of YES (a bid for Down) fills a pair at $1 minus the spread. Several of
these traders earned their profit from liquidity rewards, which the simulation does not pay. The other 29 reports
(sports, esports, weather, politics) cannot trade here. Since 11-11 22:40Z every tryout bot and the champion size their
own orders (no $1-$30 range). At 23:50Z rep_whiskas, rep_flippingsharks and rep_pbot6 were rebuilt and restarted on a
fresh $1,000 after losing $768, $576 and $1,000 in their first hour; remove a tryout bot with `{"remove": key}` in add.json.

## Report replicas in the standard universe, and estimated rewards (11-12 00:43Z)

The nine report replicas also run in the standard universe (user's request), added through `paperbot-data/roster_req.json`
(`{"add": [...], "restore": [...]}`, paperbot/roster.py), with sizes and per-market caps scaled to the $100 bank
(written for $1,000). The standard `bonereaper` bot, deleted at -$100 in the 17:15Z prune, was restored on a fresh
account. Several report traders earn mostly liquidity rewards and rebates, so each replica carries the rate its report
shows (dollars of rewards per dollar filled: Doggystyie 2.69%, Whiskas 1.05%, Bonereaper 0.82%, SirMartingale 0.67%
from its live wallet, BTC5MScour 0.015%; none reported for the others) and its account accumulates `est_rewards`
beside its P/L. These are polymarket.com rates; Polymarket US Tier 1 pays no maker rebate, so the estimate is never
added to cash or P/L. Both-sides replicas (Doggystyie, NeverSmiling, Bonereaper) now quote a side only while its filled
shares are at most 1.5x the other side's plus one median clip, so a one-way run stops adding (it kept buying the
falling side before).

## Filtered replicas (11-12 00:52Z)

At the user's request the replicas keep what pays in each trader's report and leave out its losing parts; all nine
restarted on fresh accounts in the standard universe ($100) and the tryout ($1,000). For every replica: at most 5% of the
bank in one window (the reports' per-market sizes reach 30% of $1,000) and no new entries for the rest of the UTC day after
settled losses of 10% of the bank. Per trader: Bonereaper only the late dominant side at $0.80-0.97 (every band under
$0.80 lost in its report; its pairs cost a median $1.15 live); Doggystyie only pairs at or under $0.99 and a tilt on a
strong signal (its trading alone lost 1%); Whiskas no leg at $0.90+ and no trading at 08 and 14 UTC; NeverSmiling off at
1, 14, 15, 16 and 23 UTC; FlippingSharks BTC only (ETH 5m lost 10% live) and no weekends; PBOT-6 15-minute windows only,
$0.30-0.60, high conviction only (its BTC 5m lost 26% live; its $0.10-0.20 and $0.60-0.70 bands lost). SLIP-ME,
SirMartingale and BTC5MScour already carried their reports' filters. The filters come from the same data that showed
the losses, so they are in-sample and still have to prove themselves.

## Combo (11-12 01:18Z)

`combo` (paperbot/combo.py) runs three approaches on one account, in the standard universe ($100) and the tryout
($1,000): XArbFast's cross-window arbitrage, LockEarly's settlement lock, and the FlippingSharks swing pairs (BTC 5m and
15m, cheap side at <= 40c, the other side once the pair costs <= 90c) the rest of the time. Each piece is sized to at
most 5% of the bank, and nothing new opens for the rest of the UTC day after settled losses of 10%. The champion can
hire it only if it passes the tryout.

## Replicas without the hedge leg; standard universe = replicas only; br177 as champion (11-12 01:47Z)

User's requests. (1) The reports themselves name what loses: the second, losing leg ("hedge tax": SLIP-ME's underdog
buys cost about $1.2M, Doggystyie's two-sided trading lost 1%, FlippingSharks' and Bonereaper's best filter was the
dominant side only), and here both sides of one book always cost $1 plus the spread. In the first 40 minutes the
two-sided and cheap-side replicas won 3 of 9-12 windows each while the leading-side ones won 7 of 7 and 9 of 10. So
rep_whiskas, rep_doggystyie, rep_slipme and rep_flippingsharks now buy only the leading side the model agrees with
(Whiskas $0.60-0.90 in the last 3 minutes; Doggystyie one resting bid at |z| >= 2, $0.50-0.90; SLIP-ME adds at |z| >= 1
up to $0.90 and late below $0.97; FlippingSharks $0.55-0.85 at |z| >= 1.5 and still completes a pair at <= $0.90); the
combo's swing piece follows FlippingSharks. They restarted on fresh accounts. (2) The standard universe keeps only the
`rep_*` bots (`only_prefix` in its config); the other standard bots still run in dollar, dollar30 and dollar1000, and
their standard accounts are saved in `paperbot-data/accounts.before-rep-only.json` (with `roster.before-rep-only.json`).
(3) The champion was un-paused by hiring br177 by hand (`{"key": "br177", "base": "br177", "src": "lab", "own": true}`;
`own` keeps its own coins, BTC and ETH, besides its own order sizes of $0.10-$100 with $100 per window).

## Rep1000 universe (port 8089)

The nine report replicas alone with $1,000 each (user, 11-12 01:55Z; `registry: "reports"`), beside the $100 ones in the
standard universe and the $1,000 ones competing in the tryout. Same rules; order sizes scale with the bank.

## br177 variants in the tryout (11-12 08:33Z)

`paperbot/br177lab.py`: br177 with exactly one change each, named after the change, plus `br177_plain` to compare
against; all use the lab br177's random seed, so order sizes match and only the change differs. In the tryout with
$1,000 each (`src: "var"`): quarter-size bets during a cold streak (2 or fewer wins in the last 8 windows), cap of 5% of
the bank per window, bet size following the edge, BTC only, stop for the UTC day after losing 10%, quarter size after
giving back half the day's peak profit, skip quiet markets (volatility under 70% of its recent median), no early exit,
resting entries at the touch (no fee), odds blended 80/20 with the market price. The champion's br177 is unchanged.

## Share-of-bank order sizes on the losing dashboards (11-12 09:40Z)

The user asked for sizes that follow the bank instead of fixed dollar ranges, and to leave what works alone. The
dashboards in profit keep their settings (dollar, dollar30, dollar1000, top20, evolve, bonelab, and the champion's
br177). On the three that were losing (standard, rep1000, tryout: `size_mode: "pct"`, `size_pct: (0.001, 0.02)`,
`window_pct: 0.05`) every order is a random 0.1%-2% of the bot's settled bank (starting cash plus settled results
after fees) and one market holds at most 5% of it, so the same setting works for $100, $1,000 or $30,000 and shrinks
as a bot loses. It applies to strategies, lab bots, replicas, late_fav and the combo (arbitrage legs included). The
eleven br177 copies in the tryout keep br177's own sizes, because they test one change each against the champion's
br177.

## Fast pick-off (fast_pickoff, 11-12 09:47Z)

The engine's loop makes about 0.85 passes a second, so every other bot reacts about 1.2 s after a price change.
`paperbot/fastpick.py` reacts inside the Coinbase tick instead (`SpotFeed.listeners`): on a spot jump of 2.5 sd over
1 s with the 5-minute window's mid unchanged and 2c of model edge after the fee, it plans a take at the touch and
reserves that size; the engine books it on its next pass at the decision-time price. Measured: 0.08 ms per tick live
(571 ticks, no signal yet), 0.07-0.44 ms per decision offline with the model stubbed. In the tryout with share-of-bank
sizes. Not measured: the delay of the Coinbase feed and of an order reaching the exchange; paper fills at the
decision-time quote are the best case, so its results are an upper bound.

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

## Fast 20 dashboard (port 8091)

`paperbot/fast20.py`. The 20 most profitable strategy bots of the dollar, dollar30, dollar1000, top20, bonelab and
evolve dashboards (return on starting cash after fees, at least 10 settled windows, picked once and kept in
`paperbot-data/fast20/fast20.json`; delete that file and restart to pick again), each on a fresh $1,000 account with
the share-of-bank sizing (0.1%-2% of the bank an order, 5% a window). Scanners are not eligible.

These bots run on their own thread (`Engine.t_fast`), 50 passes a second asked (`fast_hz`), about 40 measured, instead of the shared loop's
roughly one pass a second; the model is recomputed at that rate. Inputs: Coinbase spot is streamed
(every trade), the order books are streamed, and the benchmark index, which prints once a second, is polled twice a
second (was every 2 s; this applies to every dashboard). The panel shows how old each input is when the bots read it. The dashboard shows the measured passes a second and
the time one pass takes. Paper fills at the quote seen are the best case: a real order also has to reach the exchange,
and most bots wait 2-3 s between their own orders, so the gain is reacting sooner, not ordering more often. Past profit
of the picked bots is not evidence they will profit here (see the walk-forward result above).

## br177 direction fixes in the tryout (11-12 18:42Z)

On BTC 5-minute windows the lab's bots lost on Up bets while BTC kept closing Down, and lost most on early exits. Four
copies of br177 test the fixes next to `br177_unchanged_for_comparison` (`paperbot/br177lab.py`; the champion is not
touched): `br177_only_bet_with_the_3_minute_trend` (Up only while the coin's Coinbase price is above its price 180 s
ago, Down only while below), `br177_no_more_than_3_windows_in_a_row_on_one_side`, `br177_hold_to_the_close` (no early
exit) and `br177_trend_and_hold_to_the_close`. They were added with `"keep": 300` in `add.json`, which exempts a bot
from the tryout's hourly review until it has that many settled windows (the first set of br177 copies was dropped
after about 24).

## Direction guards on the losing lab bots and the champion (11-12 19:15Z)

The Bonereaper-lab bots (`bonelab.BoneVar`) that were losing, on whichever dashboard they run, and the champion's
br177 run with three guards: the 3-minute trend rule, no 4th window in a row on one side, and no early exit (the
protective exit and the variants' take-profit / stop-loss are off). A lab bot that was in profit is left as it was.
The choice is made once per bot from its settled P/L after fees and kept in `<dashboard dir>/guards.json` (edit that
file and restart to change one). The tryout's br177 copies keep one guard each and `br177_unchanged_for_comparison`
runs with none, so the effect can be measured. The guards had no settled record when they were switched on.

## br133 dashboard (port 8092)

br133, the first bot to pass the tryout, on its own fresh $1,000 account as it ran there: BTC, ETH, SOL and XRP
5-minute windows with the share-of-bank sizing, no guards.

## br133 fast dashboard (port 8093)

A second copy of br133 on a fresh $1,000 account: Bitcoin 5-minute windows only, run by its own 50-passes-a-second loop
(`Engine.t_fast`, as the Fast 20), with twice the tryout's stakes (0.2%-4% of the bank an order, 10% a window). The
entry rule is unchanged: still one entry per window.
