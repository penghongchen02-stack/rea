/**
 * Product rules shared by the Express API and the in-browser demo backend:
 * request validation, plan gating and bet statistics. No Node-only imports.
 */
import { z } from "zod";
import { americanToDecimal } from "../shared/odds-math.js";
import type { MarketFilter } from "./analysis.js";
import { DEFAULT_FEES } from "./pm/arbs.js";
import type { Bet, User } from "./types.js";

/** Rows a free or signed-out visitor sees per tool before upgrading. */
export const FREE_LIMITS = { ev: 3, arbs: 1, lowHold: 5, middles: 2, moves: 5, pmArbs: 2 } as const;

const KINDS = ["moneyline", "spread", "total", "team_total", "player_prop", "future"] as const;
const PERIODS = ["full", "h1", "q1", "p1", "f5"] as const;

export const marketFilterSchema = z
  .object({
    sport: z.string().max(40).optional(),
    market: z.enum(KINDS).optional(),
    period: z.enum(PERIODS).optional(),
    event: z.string().max(80).optional(),
    prop: z.string().max(60).optional(),
    /** Comma-separated book ids; omitted means every book. */
    books: z.string().max(400).optional(),
  })
  .transform(({ sport, market, period, event, prop, books }): MarketFilter => ({
    sportId: sport || undefined,
    kind: market,
    period,
    eventId: event || undefined,
    propType: prop || undefined,
    books: books ? books.split(",").filter(Boolean) : undefined,
  }));

/** Minimum EV in percent; converted to a fraction. */
export const minEvSchema = z.coerce.number().min(-100).max(100).default(0).transform((n) => n / 100);

export const evMethodSchema = z.enum(["market", "sharp"]).default("market");

/** Minimum implied-probability change in percentage points; converted to a fraction. */
export const minMoveSchema = z.coerce.number().min(0).max(100).default(1).transform((n) => n / 100);

export const pmLeagueSchema = z.object({ league: z.string().max(20).default("nba") });

/** Fee coefficients and minimum edge are percentages in the query; converted to fractions. */
export const pmArbQuerySchema = pmLeagueSchema.extend({
  kalshiFee: z.coerce.number().min(0).max(100).default(DEFAULT_FEES.rate.kalshi * 100).transform((n) => n / 100),
  polymarketFee: z.coerce.number().min(0).max(100).default(DEFAULT_FEES.rate.polymarket * 100).transform((n) => n / 100),
  minEdge: z.coerce.number().min(-100).max(100).default(-2).transform((n) => n / 100),
});

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

/** Most rows any tool returns in one response; `total` still counts every match. */
export const MAX_ROWS = 200;

export function gate<T>(items: T[], user: User | undefined, freeLimit: number): { items: T[]; total: number; locked: number } {
  const pro = user?.plan === "pro";
  const visible = items.slice(0, pro ? MAX_ROWS : freeLimit);
  // Locked counts rows hidden by the plan, not rows past the response cap.
  return { items: visible, total: items.length, locked: pro ? 0 : items.length - visible.length };
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
