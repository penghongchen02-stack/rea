/** Pure betting math shared by the API and the calculators. Prices are decimal unless named otherwise. */

export function americanToDecimal(american: number): number {
  if (!Number.isFinite(american) || Math.abs(american) < 100) {
    throw new RangeError(`American odds must be <= -100 or >= +100, got ${american}`);
  }
  return american > 0 ? 1 + american / 100 : 1 + 100 / -american;
}

export function decimalToAmerican(decimal: number): number {
  assertDecimal(decimal);
  const american = decimal >= 2 ? (decimal - 1) * 100 : -100 / (decimal - 1);
  const rounded = Math.round(american);
  // Rounding can land inside (-100, 100) only at even money; normalise to +100.
  return Math.abs(rounded) < 100 ? 100 : rounded;
}

export function decimalToFractional(decimal: number): string {
  assertDecimal(decimal);
  const value = decimal - 1;
  let best = { n: Math.round(value), d: 1, err: Infinity };
  for (let d = 1; d <= 100; d++) {
    const n = Math.round(value * d);
    const err = Math.abs(value - n / d);
    if (err < best.err - 1e-9) best = { n, d, err };
    if (err < 1e-9) break;
  }
  const g = gcd(best.n, best.d);
  return `${best.n / g}/${best.d / g}`;
}

export function impliedProbability(decimal: number): number {
  assertDecimal(decimal);
  return 1 / decimal;
}

export function probabilityToDecimal(probability: number): number {
  if (!(probability > 0 && probability < 1)) throw new RangeError("Probability must be in (0, 1)");
  return 1 / probability;
}

/** Bookmaker margin (overround) of a complete market, e.g. 0.045 for 4.5%. */
export function overround(decimals: readonly number[]): number {
  return decimals.reduce((sum, d) => sum + impliedProbability(d), 0) - 1;
}

/** Remove the margin proportionally (multiplicative method). Returns fair probabilities summing to 1. */
export function devig(decimals: readonly number[]): number[] {
  if (decimals.length < 2) throw new RangeError("A market needs at least two outcomes");
  const implied = decimals.map(impliedProbability);
  const total = implied.reduce((a, b) => a + b, 0);
  return implied.map((p) => p / total);
}

/** Expected value per unit staked, e.g. 0.03 = +3%. */
export function expectedValue(trueProbability: number, decimal: number): number {
  assertDecimal(decimal);
  return trueProbability * decimal - 1;
}

/** Full-Kelly fraction of bankroll; 0 when the bet has no edge. */
export function kellyFraction(trueProbability: number, decimal: number): number {
  assertDecimal(decimal);
  const f = (trueProbability * decimal - 1) / (decimal - 1);
  return Math.max(0, f);
}

export interface ArbitragePlan {
  isArbitrage: boolean;
  /** Return on total stake, e.g. 0.021 = 2.1%. Negative when no arbitrage exists. */
  profitMargin: number;
  stakes: number[];
  payout: number;
}

/** Split a total stake across every outcome so each one pays the same amount. */
export function arbitrage(decimals: readonly number[], totalStake: number): ArbitragePlan {
  if (decimals.length < 2) throw new RangeError("A market needs at least two outcomes");
  const inverse = decimals.map(impliedProbability);
  const sum = inverse.reduce((a, b) => a + b, 0);
  const stakes = inverse.map((p) => (totalStake * p) / sum);
  return { isArbitrage: sum < 1, profitMargin: 1 / sum - 1, stakes, payout: totalStake / sum };
}

export function parlayDecimal(decimals: readonly number[]): number {
  if (decimals.length === 0) throw new RangeError("A parlay needs at least one leg");
  return decimals.reduce((acc, d) => {
    assertDecimal(d);
    return acc * d;
  }, 1);
}

export interface HedgePlan {
  hedgeStake: number;
  /** Profit if the original bet wins. */
  profitIfOriginalWins: number;
  /** Profit if the hedge wins. */
  profitIfHedgeWins: number;
}

/** Hedge stake that locks equal profit whichever side wins. */
export function hedge(originalStake: number, originalDecimal: number, hedgeDecimal: number): HedgePlan {
  assertDecimal(originalDecimal);
  assertDecimal(hedgeDecimal);
  const hedgeStake = (originalStake * originalDecimal) / hedgeDecimal;
  const profit = originalStake * originalDecimal - originalStake - hedgeStake;
  return { hedgeStake, profitIfOriginalWins: profit, profitIfHedgeWins: profit };
}

function assertDecimal(decimal: number): void {
  if (!Number.isFinite(decimal) || decimal <= 1) throw new RangeError(`Decimal odds must be > 1, got ${decimal}`);
}

function gcd(a: number, b: number): number {
  a = Math.abs(a);
  b = Math.abs(b);
  while (b) [a, b] = [b, a % b];
  return a || 1;
}
