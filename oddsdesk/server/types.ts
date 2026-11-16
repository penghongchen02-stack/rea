export type MarketKind = "moneyline" | "spread" | "total";

export interface Book {
  id: string;
  name: string;
  /** The low-margin book used as the fair-price reference for +EV. */
  sharp: boolean;
}

export interface Sport {
  id: string;
  name: string;
  threeWay: boolean;
  spread: number;
  total: number;
}

export interface SportEvent {
  id: string;
  sportId: string;
  home: string;
  away: string;
  startTime: string;
}

export interface MarketLine {
  id: string;
  eventId: string;
  kind: MarketKind;
  /** Spread for the first outcome, or the total; null for moneyline. */
  point: number | null;
  outcomes: string[];
  /** American odds per outcome, keyed by book id; null when the book has no market. */
  prices: Record<string, number[] | null>;
}

export type Plan = "free" | "pro";

export interface User {
  id: string;
  email: string;
  passwordHash: string;
  plan: Plan;
  createdAt: string;
}

export type BetStatus = "open" | "won" | "lost" | "push";

export interface Bet {
  id: string;
  userId: string;
  createdAt: string;
  description: string;
  book: string;
  americanOdds: number;
  stake: number;
  status: BetStatus;
}
