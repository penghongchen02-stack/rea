export type MarketKind = "moneyline" | "spread" | "total" | "team_total" | "player_prop" | "future";

/** Game segment a market settles on. */
export type Period = "full" | "h1" | "q1" | "p1" | "f5";

export const PERIOD_LABEL: Record<Period, string> = {
  full: "Full game",
  h1: "1st half",
  q1: "1st quarter",
  p1: "1st period",
  f5: "First 5 innings",
};

export interface Book {
  id: string;
  name: string;
  /** The low-margin book used as the fair-price reference for sharp +EV. */
  sharp: boolean;
}

export interface Sport {
  id: string;
  name: string;
  threeWay: boolean;
  spread: number;
  total: number;
  /** Periods offered besides the full game. */
  periods: Period[];
}

export interface SportEvent {
  id: string;
  sportId: string;
  kind: "game" | "future";
  /** Display name, e.g. "Metro Comets @ Desert Suns" or "Pro Basketball Champion". */
  title: string;
  home: string | null;
  away: string | null;
  startTime: string;
}

/** One book's quote on a market: its line and an American price per outcome. */
export interface Offer {
  /** Handicap for the first outcome (spread) or the line (totals and props); null when the market has no line. */
  point: number | null;
  prices: number[];
}

export interface MarketLine {
  id: string;
  eventId: string;
  kind: MarketKind;
  period: Period;
  /** Team for a team total, player for a prop; null otherwise. */
  participant: string | null;
  /** Statistic for a player prop, e.g. "Points"; null otherwise. */
  propType: string | null;
  outcomes: string[];
  /** The main line most books hang; null for markets without a line. */
  point: number | null;
  /** Current quote per book; null when the book does not offer the market. */
  offers: Record<string, Offer | null>;
  /** First quote seen per book, for line-movement tracking. */
  opening: Record<string, Offer | null>;
  /** When each book's quote last changed. */
  changedAt: Record<string, string>;
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
