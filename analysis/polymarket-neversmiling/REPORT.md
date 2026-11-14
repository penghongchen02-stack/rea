# Reverse-engineering the Polymarket bot "NeverSmiling"

Wallet `0xfcdc071df7080c214196bb0b3b751e5417f9d8e3` (profile `neversmiling`, pseudonym
`Favorable-Shape`, created 2026-03-30). Subject of the PR&R trader report at
https://www.polyresearchrobotics.com/reports/neversmiling (window 2026-05-18 to 2026-06-02).

This analysis re-derives the bot's behaviour from Polymarket's public data APIs for the
account's whole life (2026-04-01 to 2026-08-07, 104 active days), not just the report's
16-day window, and checks the report's claims against it. Analysis date: 2026-11-07.

## 1. Summary

NeverSmiling is a fully automated two-sided liquidity bot on Polymarket's short-duration
crypto "Up or Down" markets (BTC, ETH, SOL, XRP; 5-minute, 15-minute and 1-hour windows).
It never sells. It buys both outcomes of nearly every market it touches, merges the matched
pairs back into USDC inside the window, redeems the rest a minute after the window closes,
and repeats across roughly 200 to 1,100 markets per day, 24 hours a day.

The trading itself earns almost nothing. Over the account's life the gross trading result
(+$144k) is almost exactly cancelled by taker fees (−$142k). The profit is Polymarket's
rebate programs: $19.6k of maker rebates plus $37.5k of taker rebates, for a lifetime net
of about $59k. The bot runs on a float of roughly $5k to $11k; the "$1.02M deployed" in the
report is turnover, not capital.

| Lifetime (2026-04-01 to 2026-08-07) | USDC |
| - | - |
| Fills (buy only, maker + taker) | 1,924,231 |
| Markets traded | 83,013 |
| Buy notional (ex fee) | 9,003,766 |
| Settlement redemptions | +5,838,396 |
| Complete-set merges | +3,309,827 |
| Gross trading result | +144,457 |
| Taker fees paid | −142,191 |
| Net trading result | +2,266 |
| Maker rebates | +19,587 |
| Taker rebates | +37,462 |
| Net including rebates | +59,316 |
| Deepest cash drawdown (capital actually needed) | 4,946 |

## 2. Data and method

Sources (all public, no authentication):

- `data-api.polymarket.com/v2/activity?user=` : every fill, redemption, merge and rebate
  payment (2,080,295 rows). Each TRADE row carries `size`, `price` and `usdc_size`.
- `data-api.polymarket.com/v2/trades?user=` : 1,073,400 rows. This feed turned out to list
  only the account's taker fills (May to August counts match the fee-based taker count to
  the row). Anyone building on this endpoint alone undercounts a maker's activity.
- `data-api.polymarket.com/v2/trades?condition=` : the public per-market feed, used to
  confirm counterparties and that the bot's rows there are exactly its taker fills.
- `clob.polymarket.com/markets/{condition_id}` : resolution winner, fee parameters.
- `clob.polymarket.com/rebates/current` : per-day, per-market maker rebates.
- `docs.polymarket.com` : fee formula `fee = shares × 0.07 × p × (1 − p)` for crypto,
  maker rebate 20% of taker fees, taker rebate tiers (live 2026-05-28).

Maker versus taker: for each fill, `usdc_size − size × price` equals the documented taker
fee to within 0.004% when the bot was the taker and is exactly zero when it was the maker.
Membership in the taker-only trades feed gives the same label on 100% of May to August
fills, and also labels April fills, when these markets charged no fee.

Resolution winner: the REDEEM rows' `outcome_index` is not reliable (it agreed with the
true winner on 51% of markets). The winner was instead derived from the redeemed amount,
which must equal the shares held on one side after merges. This labels 96% of markets; the
remaining 4% were fully merged and had no settlement exposure. A random sample of 149
labels was checked against the CLOB resolution: 149 of 149 agree.

Window reproduction: with the same window the derived figures match the report closely.

| Metric | Report | This analysis |
| - | - | - |
| Markets | 6,360 | 6,360 |
| Fills | 173,640 | 176,965 (activity feed); 96,420 (taker feed) |
| Buy notional | $1,022,056 | $1,042,123 ex fee, $1,058,522 incl. fee |
| Both-sides rate | 79.2% | 79.2% |
| Median clip / p99 / max | $3.51 / $28.82 / $267.09 | $3.57 / $28.75 / $267.09 |
| Dominant-side win rate, 1–1.5x / 1.5–2x / 2–3x / 3x+ | 53.7 / 66.2 / 74.2 / 78.8% | 53.5 / 65.8 / 74.7 / 78.7% |
| Net cash-flow P/L | +$4,337 | +$3,507 after fees (+$19,905 before fees) |

## 3. How the bot works

### 3.1 Universe and schedule

- Only crypto Up/Down markets. Lifetime notional by series: BTC 5m $2.33M, ETH 5m $1.88M,
  SOL 15m $1.18M, BTC 1h $1.12M, BTC 15m $0.99M, ETH 1h $0.47M, SOL 5m $0.45M, XRP 5m
  $0.36M, ETH 15m $0.22M, XRP 15m $0.14M.
- Runs around the clock. Each UTC hour carries 3.3% to 5.0% of fills; there is no sleep
  window, only a mild daytime peak.
- About four markets are worked at once (median 3, p90 6 distinct markets per minute).

### 3.2 Life of one market

1. It enters shortly after the window opens, never before. Median first fill: 21 s into a
   5-minute window, 24 s into a 15-minute window, 16 s into an hourly window.
2. It buys both outcomes. On 78% of markets it holds both Up and Down. The first fill is at
   a median price of $0.48 and the first fill on the other side follows a median 41 s later
   at $0.46, so the opening pair costs about $0.96. Which side comes first is a coin flip
   (50.3%) and the side it ends up heavier on is the first side only 50% of the time.
3. It keeps working both sides through the window with a small, fixed ladder of clip sizes
   (section 3.4). Fill intensity is highest in the first tenth of the window (14% of USDC)
   and then roughly flat at 9 to 10% per tenth until the last few percent.
4. Price bands drift with time. Mid-priced fills ($0.40 to $0.60) happen early (47% of
   their USDC in the first quarter). Expensive fills ($0.90+) happen late (58% of their
   USDC in the last quarter) and cheap fills (under $0.10) also happen late (46% in the
   last quarter). This is the pattern of a quoter that keeps bidding both sides as the
   market resolves toward one of them, picking up the winner at $0.9x and the loser at
   $0.0x.
5. It merges matched pairs inside the window. 94% of the 10,493 MERGE events occur before
   the window ends (median at 77% of the window) in chunks of about $270 (p90 $514). Over
   the account's life 43% of paired shares were merged; in the report window, 59%.
   Merging returns $1.00 per Up+Down pair immediately, which is how the float stays tiny.
6. Whatever is left is redeemed about one minute after the window closes (median 1 min,
   p90 13 min). Redemptions are batched (145,416 redeem rows in 70,331 transactions).
7. It never sells. Zero SELL fills in 1.92 million.

### 3.3 Maker and taker roles

- Lifetime it is the taker on 56% of fills (58% of notional). April was 78% maker; from
  May 29 it is 60 to 70% taker.
- Where it rests orders it quotes both sides: 94% of markets with any maker fill have maker
  fills on both outcomes, and the sum of its maker VWAPs is below $1.00 on 59% of them
  (median $0.977). Its taker VWAPs sum below $1.00 on 67% of both-taker markets (median
  $0.928), so a large part of the spread is captured by taking, not quoting.
- Its resting orders are adversely selected. Lifetime, maker fills win 8 to 13 points less
  often than their price implies (for example, maker buys at an average of $0.454 win
  37.7% of the time; taker buys at $0.456 win 44.0%). Taker fills run 0 to 5 points below
  price, which is roughly the fee.
- Counterparties are diverse. In four sampled markets the bot's maker fills were hit by
  hundreds of distinct wallets with no dominant one, so this is not a wallet cluster
  trading with itself.

### 3.4 Order sizing

Fills cluster on a discrete share-size ladder rather than a USDC budget. The ten most
common exact sizes account for 32% of fills; the rest are partial fills. Sizes 12.80,
20.00, 12.00, 8.00 and 24.50 shares are almost always maker fills (resting-order sizes).
Sizes 5.00, 8.40, 11.25, 19.60, 14.70, 21.00, 15.75 and 10.50 are 62 to 68% taker (the
sizes it sends when crossing). Several are a base size times 1.05 or 0.8. The median fill
is $3, p99 $25, maximum $359. Order size does not depend on price.

### 3.5 Phases over the account's life

| Period | Behaviour |
| - | - |
| Apr 1 to Apr 26 | 5-minute markets only, BTC/ETH/SOL/XRP, 78% maker, no taker fee charged yet. $1.7M notional, trading −$4.1k, maker rebates +$6.2k. Peak week Apr 6 to 12: $1.16M notional, $4.3k maker rebates. |
| Apr 27 to May 17 | Paused. |
| May 18 to May 21 | Restart, BTC 5m and 15m only, taker share rising 9% to 36%. |
| May 22 to May 28 | BTC 15m only, 100% taker, ~$20k to $25k notional per day. |
| May 28 | Polymarket's taker rebate program goes live. |
| May 29 onward | Expands to all four assets plus hourly markets, 60 to 70% taker; fees jump from ~$600/day to $1.5k to $3k/day. |
| Jun 20 onward | Taker rebates start arriving: 15% of the previous day's fees until Jun 30, 32% from Jul 4 (the Platinum tier rate, consistent with the ~$1.9M to $2.5M 30-day weighted taker volume implied by its fills). Three lump payments (Jun 23 $5,156, Jul 1 $1,587, Jul 8 $12,308) do not follow the daily rate and look like tier bonuses or catch-up payments; this could not be verified. |
| Jul | Volume tapering. |
| Aug 7 | Last fill. No activity since; taker tier back to 0. |

Monthly trading P/L after fees: April −$4,138, May +$3,696, June +$1,620, July +$976,
August +$113. Monthly rebates: April $6,194, May $1,896, June $13,695, July $31,332,
August $3,934.

## 4. Where the money comes from

Both in the report window and over the account's life, gross trading profit roughly equals
the fees it pays, and the rebates are the net income.

| | Lifetime | Report window |
| - | - | - |
| Gross trading result (before fees) | +$144,457 | +$19,905 |
| Taker fees | −$142,191 | −$16,398 |
| Net trading result | +$2,266 | +$3,507 |
| Maker rebates | +$19,587 | +$2,239 |
| Taker rebates | +$37,462 | $0 (program not yet paying) |
| Net | +$59,316 | +$5,746 |

The gross result is spread capture on pairs bought below $1.00, offset by losses on pairs
that end up costing more than $1.00 when the market runs away from its resting bids:

| Paired cost (Up VWAP + Down VWAP) | Markets | Net P/L after fees |
| - | - | - |
| under $0.90 | 25,053 | +$185,357 |
| $0.90 to $0.95 | 8,167 | +$29,674 |
| $0.95 to $0.97 | 3,546 | +$2,755 |
| $0.97 to $1.00 | 5,552 | −$5,380 |
| $1.00 to $1.03 | 5,366 | −$21,884 |
| $1.03 to $1.10 | 8,422 | −$64,437 |
| over $1.10 | 8,441 | −$132,732 |

One-sided markets (22% of markets, 5% of notional) netted +$8.9k; both-sided markets
netted −$6.6k after fees.

Capital: the deepest cumulative cash drawdown over the whole life is $4,946 (2026-04-07);
the median daily low is about $10.7k out. In the report window the bot was never more
than $1,662 below its starting cash. The economics are therefore a few thousand dollars
of float earning tens of thousands in rebates, not 0.4% on a million.

## 5. The report's claims, re-examined

1. "Holds every position to settlement; SELL count is zero." Zero sells is correct, but the
   report missed merges. In its own window the bot merged $496,842 of complete sets, 47%
   of its buy notional and 59% of its paired shares, inside the windows. Settlement is the
   exit only for the unmatched remainder.
2. "$16,640 gap between Polymarket's P/L ($20,977) and computed P/L ($4,337), likely open
   positions." The gap is taker fees. Window fees were $16,398 and the gross result before
   fees was $19,905, which matches Polymarket's figure. The report's cost basis included
   fees; Polymarket's P/L does not.
3. "A genuine directional signal: 78.8% dominant-side win rate at 3x+ dominance." The
   number reproduces (78.7%), but it is measured after the fact. USDC "dominance" is
   computed over the whole window and the bot buys the eventual winner late at $0.90+,
   which makes the winner the dominant side by construction. Recomputing dominance from
   fills in the first quarter of each window, the dominant side wins 52% to 61% and beats
   its own average price by only 2 to 3 points in every bucket. There is a small edge,
   consistent with momentum in the opening seconds, not a 78.8% oracle.
4. "Spread P/L is only $160; the engine is directional accuracy." Spread capture is the
   engine: pairs under $0.95 produced +$215k gross lifetime. The $160 figure counted only
   fully paired shares held to settlement, ignoring merges.
5. "$1.02M deployed, +0.42% ROI, annualised ~9.6%." That is turnover. The float was a few
   thousand dollars, and the net economics are dominated by rebates the report never saw
   (its data had no MAKER_REBATE or TAKER_REBATE rows).
6. "Price paradox: loses on $0.90 to $1.00 buys." True as hold-to-settlement accounting,
   but those buys are mostly the expensive leg of a pair that is then merged; their
   standalone P/L is not what the bot realises.
7. "Edge category: market maker." Partly. Since May 29 it is a taker on about two thirds of
   fills and its maker fills are adversely selected; the bot's real product is fee rebates
   earned on volume on both sides of the book.

## 6. Reproduction

Scripts are in `scripts/`; raw dumps are not committed (about 1 GB of JSON). Set
`POLY_DATA_DIR` to a writable directory.

```
scripts/dump.sh trades   $POLY_DATA_DIR/trades.jsonl     # taker fills
scripts/dump.sh activity $POLY_DATA_DIR/activity.jsonl   # all fills, redeems, merges, rebates
python3 -I scripts/analyze.py            # overview, weekly regime, daily fees vs rebates
python3 -I scripts/derive_winners.py     # winner per market from redeemed amounts
python3 -I scripts/analyze_markets.py    # per-market reconstruction (add "window" for the report window)
python3 -I scripts/analyze_mechanics.py  # waterfall, capital, sizing, quoting, tier, validation
python3 -I scripts/analyze_extra.py      # per-fill win rates, ex-ante dominance test, timing
```

The outputs used for this report are in `results/`.

## 7. Limitations

- No on-chain verification: the public Polygon RPC refused archive queries, so maker/taker
  labels rest on two independent API signals (fee presence and taker-feed membership)
  that agree on 100% of fee-era fills, plus a four-market check against the public feed.
- Winner labels are derived, not read from resolution data, for 83k markets; validated on
  a random sample of 149.
- The three lump taker-rebate payments and the 15% rate in late June are unexplained.
- The Chainlink price tape was not pulled, so the bot's actual quoting signal (what it
  watches to tilt and when it crosses) is inferred from fill prices and timing only.
- The account has been inactive since 2026-08-07; nothing here describes a live strategy.
