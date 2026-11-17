# psb: Polymarket sports strategy replay and paper trading

Tests the seven sports strategies described in the
[PR&R trader reports](https://www.polyresearchrobotics.com/reports) against
real data, and paper-trades them on live Polymarket US order books.

**This project never sends an order.** It uses only public, unauthenticated
endpoints and has no API-key handling or order code. Live trading would be a
separate, deliberate change.

## What it does

| Step | Command | Data |
|---|---|---|
| Download each source wallet's recent fills, maker/taker tags, market resolutions and paid fees | `python3 -m psb.fetch --days 30 --max-trades 100000` | `data-api.polymarket.com/v2`, `gamma-api.polymarket.com` |
| Replay: P&L as traded, after the wallet's real fees, under Polymarket US fees, and under each report's rules | `python3 -m psb.replay` | `data/` |
| Paper trading on Polymarket US | `python3 -m psb.paper --cycles 60 --interval 60` | `gateway.polymarket.us` (US books), `gamma-api` (fair value) |
| Settle and report paper positions | `python3 -m psb.paper --report` | `paper/ledger.json` |
| Tests | `python3 -m pytest` | |

Python 3.10+, no runtime dependencies.

## The seven strategies

| Report | Rules this code applies (from the report) | Side signal |
|---|---|---|
| FERRARICHAMPIONS2026 | tennis, MLB, soccer, UFC; no NHL or NBA; quotes both sides | fair value |
| SWISSTONY | buy only; no NBA; skip $0.40–0.50; skip 18, 20, 21 UTC | fair value |
| GAMBLINGISALLYOURNEED | soccer, MLB, NFL, CBB; skip $0.40–0.50; skip 10, 12, 13 UTC; both sides | fair value |
| SINENOONEEI | tennis, MLB totals, esports; buy $0.10–0.80; 08–22 UTC except 16; taker only | fair value |
| KCH123 | buy NFL, CFB, CBB at $0.20–0.70; skip 16–18, 20, 22 UTC (report gives no time zone; read as UTC) | fair value |
| GEIYECAPIXIE | League of Legends only | fair value |
| RANDOMGAMEBET | CS, LoL, Dota, Valorant; skip 06–07 UTC; both sides | fair value |

None of the reports discloses how the trader picks a side. The swisstony
report guesses a sportsbook feed, and SINENOONEEI's edge is a live human read. Their
"dominance ≥ 2x" filters are computed from the wallet's own later allocation,
so they cannot be applied ahead of time and are left out. Every strategy here
therefore uses the same stand-in signal: the matching international Polymarket
midpoint as fair value. That is a related venue's price, not an independent
forecast, so this tests the report rules and execution, not the traders' edge.

## Polymarket US differences that matter

* **One side per market.** Buying the other side cashes out the held side
  ([docs](https://docs.polymarket.us/learn/trading/basics/opposing-sides.md)).
  Per contract this pays exactly what holding both sides would, and the fee
  formula is symmetric, so both-sides strategies still work economically
  (`tests/test_core.py::test_us_cash_out_equals_holding_both_sides`).
* **Taker fee** `Θ × contracts × p × (1−p)`, Θ = 0.095 (new account) down to
  0.0695 (≥ $250K 30-day taker volume), effective 2026-11-14
  ([docs](https://docs.polymarket.us/fees.md)). Maker fills pay nothing; maker
  rebates start above $10M monthly maker volume.

## Replay results (run 2026-11-17)

Fills on resolved markets, most recent 30 days of each wallet's activity (capped
at 100,000 fills). "Rules" = only the fills the report's rules would also
take. US fees are applied to the fills that were taker fills on Polymarket.

| Wallet | Window | Resolved notional | Taker share | Gross ROI | After their real fees | With US fees (Θ 0.0695 / 0.095) | Rules agree (notional) | Rules-only gross | Rules-only with US fees |
|---|---|---|---|---|---|---|---|---|---|
| ferrarichampions2026 | Oct 25 → Nov 15* | $42.7M | 35% | +2.14% | +1.80% | +1.02% / +0.61% | 46% | −1.03% | −1.71% |
| swisstony | Aug 21 → Aug 26* | $14.2M | 83% | +0.49% | −0.27% | −1.02% / −1.57% | 62% | −0.31% | −1.76% |
| gamblingisallyourneed | Jun 15 → Jul 15 | $2.2M | 0% | −11.91% | −11.91% | −11.92% / −11.92% | 82% | −8.63% | −8.64% |
| sinenooneei | Oct 18 → Nov 17 | $2.7M | 88% | +4.14% | +2.39% | +1.43% / +0.44% | 24% | −8.82% | −11.13% |
| kch123† | Jun 2 → Jun 29 | $0.67M | 34% | −35.51% | −35.83% | −35.97% / −36.14% | 0% | n/a | n/a |
| geiyecapixie | Oct 16 → Nov 15 | $0.91M | 79% | −0.56% | −0.56% | −1.76% / −2.20% | 100% | −0.56% | −1.76% |
| randomgamebet | Oct 18 → Nov 17 | $1.23M | 0% | +1.04% | +1.04% | +1.04% / +1.04% | 100% | +1.05% | +1.05% |

\* Hit the 100,000-fill cap, so the window is shorter than 30 days.
† Address inferred: the kch123 report shows none; this is the profile named exactly `kch123`.
Swisstony, gamblingisallyourneed and kch123 have had no fills since Aug 26, Jul 15 and Jun 29.

What this shows, for these windows only:

* Three wallets are still profitable after their own fees (ferrari, sinenooneei,
  randomgamebet). After Polymarket US taker fees, ferrari and sinenooneei shrink to
  roughly +0.4% to +1.4%; randomgamebet trades as a maker and is unaffected.
* The report rules did not hold up out of sample. Where they filtered anything,
  the kept fills did worse than the wallet's full book in three of four cases
  (ferrari, swisstony, sinenooneei). The traders' best categories moved: ferrari
  made its money in NBA (+8.3%) and NHL (+26%), which its report says to avoid.
* Short windows, one wallet each: none of this is evidence of a durable edge.

## Paper trading

Each cycle reads every open US game-winner market in the chosen leagues, matches
it to the international game (league prefix, start time within 6 h, team names),
and lets each strategy act. Fill model:

* **Taker:** fills at the best price up to displayed size and pays the US taker fee
  (tier 1 by default, as for a new account).
* **Maker:** a one-cycle resting bid that fills only if a later cycle shows the
  opposite price strictly through it. This ignores queue position.

Each strategy gets its own **$1,000 paper bankroll** (`--bankroll`). An order is
skipped unless it fits in available cash = bankroll + settled P&L − worst-case
loss of open positions − cost of resting quotes; buying the opposite side frees
cash as on Polymarket US. Defaults: 10 contracts per order, 100 per strategy per
market, 2¢ minimum edge after fees. Positions use a signed contract count, netted as on Polymarket US.
The ledger is `paper/ledger.json`; the log is `paper/paper.log`.

Known limits: polling REST, not WebSocket; no latency or queue model; only
game-winner markets (no spreads or totals yet, so SINENOONEEI's MLB totals rule and
kch123's spread trades are not exercised). On 2026-11-17 Polymarket US listed
no MLB games (off-season) and no events in its tennis leagues (`itfm`, `atpcq`).
