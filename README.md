# psb: Polymarket sports strategy replay and paper trading

Rebuilds the seven sports traders described in the
[PR&R trader reports](https://www.polyresearchrobotics.com/reports) as bots that
follow each report's playbook, backtests the source wallets on real data, and
paper-trades the bots on live Polymarket US order books.

**This project never sends an order.** It uses only public, unauthenticated
endpoints and has no API-key handling or order code. Live trading would be a
separate, deliberate change.

## Commands

| Step | Command | Data |
|---|---|---|
| Download each source wallet's recent fills, maker/taker tags, resolutions and paid fees | `python3 -m psb.fetch --days 30 --max-trades 100000` | `data-api.polymarket.com/v2`, `gamma-api.polymarket.com` |
| Replay the wallets: as traded, after their real fees, under Polymarket US fees, and under each report's filters | `python3 -m psb.replay` | `data/` |
| Paper-trade the seven bots, $1,000 each | `python3 -m psb.paper --minutes 120` | `gateway.polymarket.us`, `gamma-api`, `clob.polymarket.com` |
| Settle and report paper positions | `python3 -m psb.paper --report` | `paper/ledger.json` |
| One scheduled run: resume, trade, commit and push results | `scripts/run_paper.sh 110` | `paper-results/` |
| Tests | `python3 -m pytest` | |

Python 3.10+, no runtime dependencies.

## The seven bots

Each bot follows the mechanics its report describes. Dollar sizes in the reports
assume $100K–$5M books; here each bot has $1,000, so sizes keep the reports'
ratios at a smaller scale.

| Bot | What it does (from the report) |
|---|---|
| **FERRARICHAMPIONS2026** | Tennis, MLB, soccer, UFC, NBA regular season; no NHL. Game winners, main spread and main total. Enters the **dominant side first** (the side the model favors), sized by dominance at 1×, 1.5×, 2.5× or 5× a $10 base clip. Then **hedges the other side within 15 minutes**, but only while the paired cost stays under $1.00 (resting at the $0.97 target). After 15 minutes the dominant leg rides alone. |
| **SWISSTONY** | Soccer, tennis and US sports except NBA (no esports); skips $0.40–0.50 and 18, 20, 21 UTC. Fair-value comparator: **small $5 probes, then one edge-sized anchor** (quarter-Kelly, 5% per-market ceiling). Buy-only, holds to resolution, before and during games. Hedges later only if the pair costs ≤ $1.00 (the report's >$1.00 hedges lost money and are not copied). |
| **GAMBLINGISALLYOURNEED** | Soccer, MLB, NFL, college basketball; skips $0.40–0.50 and 10, 12, 13 UTC. **Market maker:** rests bids on both sides about 1.6¢ under fair, so pairs close near the report's $0.968 median, with 2× size on the favored side. Its **exit engine** closes held positions. |
| **SINENOONEEI** | Tennis, MLB totals, esports match winners; $0.10–0.80; 08–22 UTC except 16. **Live first:** 3+ small probes, then **one anchor 5–20× the average probe** once the match moves toward the thesis. No anchor above $0.80, never sells. Esports and pre-match at half size. |
| **KCH123** | NFL, college football, college basketball; game winners, spreads, totals; $0.20–0.70; skips 16–18, 20, 22 UTC. **Bursts of many small buys** (up to 20 × $5 per market), buy-only, hold to resolution. |
| **GEIYECAPIXIE** | League of Legends only, live, 09–14 UTC. **Buys the team building a lead**, in clips sized by conviction ($10 / $30 / $60 / $100), probing $3 otherwise; small clips only on T1 games. **Exits early:** sells in tranches at +15¢, +25¢ and +35¢ over entry, closes at ≥ $0.95, and flattens on a 15¢ reversal. |
| **RANDOMGAMEBET** | Live esports only (CS2, Dota 2, Valorant, LoL); skips 06–07 UTC. **Takes the dominant side**, then **hedges during the match** whenever the pair costs < $0.975, at 50/50, 60/40, 75/25 or 85/15 splits by dominance. Skips entries whose expected paired cost exceeds $0.98 (the report's pre-check). |

### What is not copied, and why

* **The signal that picks the side.** No report discloses it: they guess a
  sportsbook feed (SWISSTONY, GAMBLINGISALLYOURNEED), a game-outcome model
  (FERRARICHAMPIONS2026), or a live read of the match or game state (SINENOONEEI,
  GEIYECAPIXIE). Every bot uses the same stand-in: the matching international
  Polymarket price. That is a related venue's price, not a forecast, so these
  bots copy the traders' habits and execution, not their edge.
* **GEIYECAPIXIE's 10–20% opposing hedge.** On Polymarket US you hold one side
  per market, so a hedge only shrinks the main position at a spread cost.
* **Every line on a game** (FERRARICHAMPIONS2026 runs all O/U lines). The bots
  trade the main spread and the main total only.
* **Live speed.** The reference wallets react in seconds. This polls live games
  every 15 s over REST, and a live entry needs the edge on two polls in a row.

## Polymarket US differences that matter

* **One side per market.** Buying the other side cashes out the held side
  ([docs](https://docs.polymarket.us/learn/trading/basics/opposing-sides.md)).
  Per contract this pays exactly what holding both sides would, and the fee is
  symmetric, so "buy both sides" still works: the second buy closes the position
  at the paired cost (`tests/test_core.py::test_us_cash_out_equals_holding_both_sides`).
* **Taker fee** `Θ × contracts × p × (1−p)`, Θ = 0.095 (new account) down to
  0.0695 (≥ $250K 30-day taker volume), effective 2026-11-14
  ([docs](https://docs.polymarket.us/fees.md)). Maker fills pay nothing; maker
  rebates start above $10M monthly maker volume. Paper trading uses 0.095.

## Paper-trading engine

* **Discovery** (every 15 min): open full-game markets in the chosen leagues
  (game winner, main spread, main total), each linked to the same international
  outcome. Both teams must match by name and start times within 3 h; spreads
  link by team and line, or by the complement (Knicks +2.5 = not Raptors −2.5).
  A US market with no matching international line stays unlinked.
* **Polling:** live games every 15 s, upcoming games (next 24 h) every 3 min.
  Each poll reads fresh international midpoints in one batch
  (`clob.polymarket.com/books`), then the US book. A fair value needs an
  international bid–ask spread ≤ 4¢.
* **Fills:** *Take* walks the US book up to the bot's limit and pays the taker
  fee. *Rest* is a paper bid that fills only when real US trades print at or
  through its price after it was placed, up to the traded volume, or when the
  book crosses it. Queue position is ignored, so this is optimistic. Every bot is
  simulated independently against the same book.
* **Cash:** each bot can only use bankroll + settled P&L − worst-case loss of
  open positions − cost of resting orders.
* **Valuation:** open positions are marked at the US mid, or carried at cost when
  there is no two-sided book.

On 2026-11-17 Polymarket US listed no MLB (off-season), no tennis events and no
League of Legends games. That leaves GEIYECAPIXIE idle, and SINENOONEEI and
FERRARICHAMPIONS2026 without their core sports.

## Replay results (run 2026-11-17)

Fills on resolved markets, most recent 30 days of each wallet's activity (capped
at 100,000 fills). "Filters" = only the fills the report's market, price and
hour rules would also take (the mechanics above cannot be replayed from fills).
US fees are applied to the fills that were taker fills on Polymarket.

| Wallet | Window | Resolved notional | Taker share | Gross ROI | After their real fees | With US fees (Θ 0.0695 / 0.095) | Filters keep (notional) | Filters-only gross | Filters-only with US fees |
|---|---|---|---|---|---|---|---|---|---|
| ferrarichampions2026 | Oct 25 → Nov 15* | $42.7M | 35% | +2.14% | +1.80% | +1.02% / +0.61% | 66% | +1.89% | +1.01% |
| swisstony | Aug 21 → Aug 26* | $14.2M | 83% | +0.49% | −0.27% | −1.02% / −1.57% | 62% | −0.31% | −1.76% |
| gamblingisallyourneed | Jun 15 → Jul 15 | $2.2M | 0% | −11.91% | −11.91% | −11.92% / −11.92% | 82% | −8.63% | −8.64% |
| sinenooneei | Oct 18 → Nov 17 | $2.7M | 88% | +4.14% | +2.39% | +1.43% / +0.44% | 10% | +16.19% | +13.63% |
| kch123† | Jun 2 → Jun 29 | $0.67M | 34% | −35.51% | −35.83% | −35.97% / −36.14% | 0% | n/a | n/a |
| geiyecapixie | Oct 16 → Nov 15 | $0.91M | 79% | −0.56% | −0.56% | −1.76% / −2.20% | 100% | −0.56% | −1.76% |
| randomgamebet | Oct 18 → Nov 17 | $1.23M | 0% | +1.04% | +1.04% | +1.04% / +1.04% | 100% | +1.05% | +1.05% |

\* Hit the 100,000-fill cap, so the window is shorter than 30 days.
† Address inferred: the kch123 report shows none; this is the profile named exactly `kch123`.
SWISSTONY, GAMBLINGISALLYOURNEED and KCH123 have had no fills since Aug 26, Jul 15 and Jun 29.

For these windows only:

* Three wallets are profitable after their own fees: FERRARICHAMPIONS2026,
  SINENOONEEI and RANDOMGAMEBET. Under Polymarket US taker fees,
  FERRARICHAMPIONS2026 and SINENOONEEI shrink to about +0.4% to +1.4%;
  RANDOMGAMEBET trades as a maker and is unaffected.
* The report filters helped SINENOONEEI (+16% on the 10% of its trading volume they
  keep, a small sample) and GAMBLINGISALLYOURNEED (less negative), and hurt
  SWISSTONY. They roughly preserved FERRARICHAMPIONS2026.
* These are short windows, one wallet each: none of this is evidence of a
  durable edge.

## Scheduled paper runs

`scripts/run_paper.sh` resumes from `paper-results/ledger.json`, paper-trades for
the given minutes, writes `paper-results/report.md`, and commits and pushes both,
so a run in a fresh container picks up where the last one stopped. A lock file
prevents overlapping runs. A Claude Code scheduled task starts it every 2 hours;
between runs nothing is polled, so resting paper orders and live games are missed.
