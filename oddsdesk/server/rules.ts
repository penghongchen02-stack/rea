/**
 * Product rules shared by the Express API and the in-browser demo backend:
 * request validation, plan gating and bet statistics. No Node-only imports.
 */
import { z } from "zod";
import { americanToDecimal } from "../shared/odds-math.js";
import type { MarketFilter } from "./analysis.js";
import type { Bet, User } from "./types.js";

/** What a free account sees before upgrading. */
export const FREE_LIMITS = { ev: 3, arbs: 1 } as const;

export const marketFilterSchema = z
  .object({
    sport: z.string().optional(),
    market: z.enum(["moneyline", "spread", "total"]).optional(),
  })
  .transform(({ sport, market }): MarketFilter => ({ sportId: sport || undefined, kind: market }));

/** Minimum EV in percent; converted to a fraction. */
export const minEvSchema = z.coerce.number().min(-100).max(100).default(0).transform((n) => n / 100);

export const credentialsSchema = z.object({
  email: z.string().trim().toLowerCase().email().max(254),
  password: z.string().min(8, "Password must be at least 8 characters").max(200),
});

const americanSchema = z
  .number()
  .int()
  .refine((n) => Math.abs(n) >= 100, "American odds must be <= -100 or >= +100");

export const newBetSchema = z.object({
  description: z.string().trim().min(1).max(200),
  book: z.string().trim().min(1).max(60),
  americanOdds: americanSchema,
  stake: z.number().positive().max(1_000_000),
});

export const betUpdateSchema = z.object({ status: z.enum(["open", "won", "lost", "push"]) });

export function gate<T>(items: T[], user: User | undefined, freeLimit: number): { items: T[]; total: number; locked: number } {
  const visible = user?.plan === "pro" ? items : items.slice(0, freeLimit);
  return { items: visible, total: items.length, locked: items.length - visible.length };
}

export function publicUser(user: User): Omit<User, "passwordHash"> {
  const { passwordHash: _omit, ...rest } = user;
  return rest;
}

export function sortBets(bets: readonly Bet[]): Bet[] {
  return [...bets].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export function betStats(bets: readonly Bet[]) {
  let staked = 0;
  let profit = 0;
  let settled = 0;
  let won = 0;
  for (const bet of bets) {
    if (bet.status === "open") continue;
    settled++;
    staked += bet.stake;
    if (bet.status === "won") {
      won++;
      profit += bet.stake * (americanToDecimal(bet.americanOdds) - 1);
    } else if (bet.status === "lost") {
      profit -= bet.stake;
    }
  }
  return {
    total: bets.length,
    open: bets.length - settled,
    settled,
    won,
    staked: round2(staked),
    profit: round2(profit),
    roi: staked > 0 ? profit / staked : 0,
  };
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}
