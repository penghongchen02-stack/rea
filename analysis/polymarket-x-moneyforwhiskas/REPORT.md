# Reverse-engineering the Polymarket bot "x-MoneyForWhiskas"

Wallet `0x3048d65321be3497164cdfc2996f94f98a2e7537` (profile `x-MoneyForWhiskas`, pseudonym
`Flashy-Gold`, bio "Trading to pay for my cat's food. JUNO", created 2026-06-08). Subject of the
PR&R report at https://www.polyresearchrobotics.com/reports/x-moneyforwhiskas (window 2026-08-01
to 2026-08-26). Still active at the time of this analysis (last fill 2026-11-07 11:54 UTC).

Same method and scripts as the NeverSmiling analysis (`../polymarket-neversmiling/`): the whole
life of the wallet from Polymarket's public data APIs, maker/taker labels from fee presence and
the taker-only trades feed (100% agreement), winners derived from redeemed amounts (96.4% of
markets labelled), rebates from the activity feed and the CLOB's per-day maker-rebate endpoint.

## 1. Summary

This is a different, better bot than NeverSmiling. It makes real trading profit before fees,
pays roughly three quarters of it back in taker fees, and then collects the largest taker
rebate Polymarket offers on top. It is a taker-first bot: 73% of its notional crosses the
spread, in round lots of 30, 50, 60 and 90 contracts. It buys both sides of 93% of its markets,
holds everything to settlement, never merges, never sells, and tilts hard toward one side when
it has a view. The tilt is real: measured on fills from only the first quarter of each window
the heavy side wins 3 to 7 points more often than its price implies, which is where the
pre-fee edge comes from.

| Lifetime (2026-06-10 to 2026-11-07, 148 active days) | USDC |
| - | - |
| Fills (buy, maker + taker) | 1,627,178 (plus 9 sells) |
| Markets | 41,087, all BTC 5-minute except 3,338 ETH 5-minute and 2 BTC hourly |
| Buy notional (ex fee) | 21,211,593 |
| Settlement redemptions | +21,857,114 |
| Gross trading result (before fees) | +645,539 |
| Taker fees paid | −475,407 (73.6% of gross) |
| Net trading result | +170,132 |
| Maker rebates | +33,438 |
| Taker rebates | +206,346 |
| Liquidity rewards | +2,450 |
| Net including rebates | about +412,000 |
| USDC outstanding in open windows | median $613, p99 $3,458, max $10,014 |

It turned about $21M of volume on a float that never exceeded $10k.

## 2. The report's window, reconciled

| Metric | Report | This analysis |
| - | - | - |
| Markets | 6,795 | 6,866 |
| Fills | 324,176 | 327,956 (activity feed); 206,896 taker fills |
| Buy notional | $4,128,425 | $4,091,690 ex fee, $4,190,205 incl. fee |
| Both-sides rate | 97.6% | 97.5% |
| Median clip / p99 / max | $8.20 / $62.70 / $134.53 | $8.20 / $63.36 / $134.53 |
| Dominant-side win rate 1–1.5x / 1.5–2x / 2–3x / 3x+ | 62.6 / 80.4 / 88.8 / 92.6% | 62.8 / 80.4 / 88.1 / 92.5% |
| Trading P/L (fees in cost) | +$38,776 | +$35,338 |
| Taker rebates | $37,619 | $37,619 |
| Maker rebates | $5,255 | $5,255 |
| "Unexplained" | $51,936 | taker fees: $98,515 |

The report takes Polymarket's figure of $134,180 as the account's total and backs out an
"unexplained $51,936". Polymarket's figure is the trading result before fees: this analysis gets
$133,853 for the same window. Subtract the $98,515 of taker fees and the net trading result is
$35,338; the $42,874 of rebates are on top, not inside. The account's true window result is
about $78k, not $134k.

## 3. How the bot works

### 3.1 Universe and clock

- BTC 5-minute windows, around the clock: 3.1% to 5.5% of fills in every UTC hour, with a
  mild peak at 13:00 to 16:00 UTC. ETH 5-minute windows were added on 2026-08-24 and dropped
  again after mid-September. One market at a time (median 1 distinct market per minute).
- First fill a median 17 seconds after the window opens (p90 79 s); last fill a median 91
  seconds before the close. Fill intensity is roughly flat from the open to 80% of the window,
  then tapers: it is not an end-of-window sniper.

### 3.2 Two-sided taking with pair discipline

- 93% of markets (97.5% in the report window) end with positions on both sides. The second
  side follows the first after a median 10 seconds (6 s in the window).
- 73% of notional is taker. On 35,669 markets it took both sides; the sum of its two taker
  VWAPs has a median of $0.970 and is below $1.00 on 67% of them. These are not simultaneous
  arbitrages (only 15% of first legs are within 2 seconds): it takes one side, then takes the
  other when the book has moved enough for the pair to cost under about $0.97.
- Maker fills are the small clips (5 and 10 contracts) and exist on both sides in 81% of
  markets where it rests anything; maker VWAPs sum to a median $0.981.
- Nothing is merged. Both sides are held to settlement and the winner is redeemed within
  minutes (77,017 redemptions). With 5-minute windows the capital comes back fast enough
  that merging is unnecessary.

### 3.3 The directional tilt, tested before the fact

The report's 62.6% to 92.6% dominance curve reproduces, and unlike NeverSmiling it survives an
ex-ante test. Using only fills from the first quarter of each window:

| USDC dominance | Markets | Dominant side wins | Its average price | Excess |
| - | - | - | - | - |
| 1–1.5x | 10,147 | 54.9% | 0.518 | +3.0 pts |
| 1.5–2x | 5,651 | 61.4% | 0.567 | +4.7 pts |
| 2–3x | 5,474 | 65.9% | 0.600 | +5.9 pts |
| 3x+ | 6,833 | 69.2% | 0.620 | +7.1 pts |

Over the first half of the window the excess grows to +4 to +11 points; over the whole window
to +10 to +21 points, which is where the report's 92.6% comes from. So part of the curve is
look-ahead (it keeps buying the winner as its price rises), but the early tilt is a genuine
signal of a few points. Per fill, its taker buys at $0.10 to $0.60 win 0.2 to 1.7 points
more often than their price implies; above $0.60 they win 1 to 2 points less.

### 3.4 Where the gross profit comes from

Per market, net of fees, lifetime:

| Paired cost (Up VWAP + Down VWAP) | Markets | P/L |
| - | - | - |
| under $0.90 | 6,160 | +$224,480 |
| $0.90 to $0.95 | 7,557 | +$190,441 |
| $0.95 to $0.97 | 4,735 | +$44,376 |
| $0.97 to $1.00 | 7,156 | −$38,009 |
| over $1.00 | 12,739 | −$255,863 |

By dominance, markets at 2x+ tilt made +$264k and markets near even sizing lost −$114k. The
bot earns when it pairs cheaply and tilts correctly, and loses when it pairs above $1.00. The
losses are the price of staying in every window.

### 3.5 Sizing

Round lots, never fractional: 5, 10, 15, 20, 25, 30, 50, 60, 80, 90 and 100 contracts, with
the 30/50/60/90 lots almost always taker (82% to 90%) and the 5/10 lots mostly maker. Median
fill $13 as taker, $3 as maker; p99 $77; lifetime max $594 (June) and under $200 since July.
Median USDC per market $310, p90 $1,321. Size does not depend on price.

### 3.6 Rebate tier climb

Taker rebate as a share of the previous day's fees: 4% on 2026-06-20 (Bronze), 16% a few days
later, 32% through July (Platinum), 44% in August (Diamond), 50% from September (Obsidian,
the top tier, which needs $10M of 30-day weighted volume). The profile shows Obsidian today
with about $2.0M of current 30-day weighted volume. Maker rebates ran a median $186 a day.

### 3.7 Timeline

| Period | Notional / week | Taker share | Net trading / week | Notes |
| - | - | - | - | - |
| Jun 15 to Jul 12 | $0.26M to $0.37M | 53 to 63% | +$3k to +$10k | ramp-up, BTC 5m only |
| Jul 13 to Aug 23 | $0.46M to $1.4M | 51 to 72% | +$2k to +$17k | volume quadruples; the Aug 7 switch to 60-second TWAP settlement did not slow it |
| Aug 24 to Sep 13 | $1.5M to $2.3M | 36 to 54% | −$1k to +$18k | ETH added; peak volume; one losing week |
| Sep 14 to Oct 11 | $1.3M to $1.9M | 60% | +$5k to +$10k | ETH dropped |
| Oct 12 to Nov 7 | $0.23M to $0.65M | 18 to 60% | +$2k to +$6k | volume cut by two thirds, taker share falls to about 20% in November |

Nothing in Polymarket's changelog explains the mid-October cut (the taker delay rose to
150 ms on September 4, before the peak). Whether the operator scaled down, lost a feed, or
met more competition is not visible in the data. Rebate income fell with volume: $87k in
September, $46k in October, $2k in early November.

## 4. What is different from NeverSmiling

| | NeverSmiling | x-MoneyForWhiskas |
| - | - | - |
| Taker share of notional | 58% | 73% |
| Gross trading before fees | +$144k on $9.0M | +$646k on $21.2M |
| Fees as share of gross | 98% | 74% |
| Net trading after fees | +$2k | +$170k |
| Rebates | +$57k | +$242k |
| Ex-ante directional edge (first quarter) | +2 to +3 pts | +3 to +7 pts |
| Merges | 43% of paired shares | none |
| Markets | BTC/ETH/SOL/XRP, 5m/15m/1h | BTC 5m (ETH 5m for three weeks) |
| Float | under $11k | under $10k |

Both are rebate farmers at heart, but this one also trades well enough to keep a quarter of its
gross after fees. Its taker-heavy style is deliberate: taker volume is what climbs the rebate
ladder, and at Obsidian half of every fee comes back.

## 5. Reproduction

```
export POLY_WALLET=0x3048d65321be3497164cdfc2996f94f98a2e7537 POLY_DATA_DIR=./data
export REPORT_LO=2026-08-01 REPORT_HI=2026-08-26
../polymarket-neversmiling/scripts/dump.sh trades   $POLY_DATA_DIR/trades.jsonl
../polymarket-neversmiling/scripts/dump.sh activity $POLY_DATA_DIR/activity.jsonl
python3 -I ../polymarket-neversmiling/scripts/analyze.py
python3 -I ../polymarket-neversmiling/scripts/derive_winners.py
python3 -I ../polymarket-neversmiling/scripts/analyze_markets.py        # add "window" for the report window
python3 -I ../polymarket-neversmiling/scripts/analyze_mechanics.py
python3 -I ../polymarket-neversmiling/scripts/analyze_extra.py
```

Outputs used here are in `results/`.

## 6. Limitations

- No on-chain verification; labels rest on the two API signals that agree on 100% of fills.
- 3.6% of markets have no derivable winner (fully offset positions); they carry no settlement
  exposure and are excluded from the win-rate tables only.
- The signal behind the early tilt is inferred from fill prices and timing, not observed.
- Rebate lump payments and the tier dates are read from payout ratios, not from the account.
