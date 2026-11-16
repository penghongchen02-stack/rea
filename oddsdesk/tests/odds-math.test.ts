import { describe, expect, it } from "vitest";
import {
  americanToDecimal,
  arbitrage,
  decimalToAmerican,
  decimalToFractional,
  devig,
  expectedValue,
  hedge,
  kellyFraction,
  overround,
  parlayDecimal,
} from "../shared/odds-math.js";

describe("odds conversion", () => {
  it("converts American to decimal and back", () => {
    expect(americanToDecimal(150)).toBe(2.5);
    expect(americanToDecimal(-200)).toBe(1.5);
    expect(decimalToAmerican(2.5)).toBe(150);
    expect(decimalToAmerican(1.5)).toBe(-200);
    expect(decimalToAmerican(2)).toBe(100);
  });

  it("rejects impossible prices", () => {
    expect(() => americanToDecimal(50)).toThrow(RangeError);
    expect(() => decimalToAmerican(1)).toThrow(RangeError);
  });

  it("formats fractional odds", () => {
    expect(decimalToFractional(2.5)).toBe("3/2");
    expect(decimalToFractional(1.5)).toBe("1/2");
    expect(decimalToFractional(11)).toBe("10/1");
  });
});

describe("pricing", () => {
  it("removes the margin from a -110/-110 market", () => {
    const d = americanToDecimal(-110);
    expect(overround([d, d])).toBeCloseTo(0.0476, 3);
    expect(devig([d, d])).toEqual([0.5, 0.5]);
  });

  it("computes EV and Kelly", () => {
    expect(expectedValue(0.5, 2.1)).toBeCloseTo(0.05);
    expect(kellyFraction(0.5, 2.1)).toBeCloseTo(0.0455, 4);
    expect(kellyFraction(0.4, 2)).toBe(0);
  });

  it("plans an arbitrage with equal payouts", () => {
    const plan = arbitrage([2.1, 2.1], 100);
    expect(plan.isArbitrage).toBe(true);
    expect(plan.profitMargin).toBeCloseTo(0.05);
    expect(plan.stakes[0]! * 2.1).toBeCloseTo(plan.stakes[1]! * 2.1);
    expect(arbitrage([1.9, 1.9], 100).isArbitrage).toBe(false);
  });

  it("prices parlays and hedges", () => {
    expect(parlayDecimal([2, 1.5, 3])).toBe(9);
    const plan = hedge(100, 3, 2);
    expect(plan.hedgeStake).toBe(150);
    expect(plan.profitIfOriginalWins).toBe(50);
  });
});
